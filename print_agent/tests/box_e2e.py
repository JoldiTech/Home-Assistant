#!/usr/bin/env python3
"""The add-on image running CryptoCart's register box beside the agent.

Checks the add-on's side of the box, not the box itself (CryptoCart tests
that): the box is a second s6 service that starts, stays down when it is not
set up or refuses its configuration (exit 78), restarts alone after any other
exit, takes a pairing code from the
add-on's stdin, retries an unreachable company with backoff once paired (a
key is seeded) and writes only under /data/cryptocart. The company is a stand-in on a private network that
logs each connection and closes it, so every dial fails.

    docker build --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm \
        -t nmt-print-agent:test print_agent/nmt_print_agent
    python3 print_agent/tests/box_e2e.py
"""

import base64
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

IMAGE = os.environ.get("E2E_IMAGE", "nmt-print-agent:test")
NET = "nmt-box-e2e"
COMPANY = "nmteaco.test"
WORK = tempfile.mkdtemp(prefix="nmt-box-e2e-")
SHARED = os.path.join(WORK, "shared")
WAIT_S = int(os.environ.get("BOX_E2E_WAIT", "95"))

COUNTER = r"""
import socket, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 443)); s.listen(16)
while True:
    c, _ = s.accept()
    with open("/shared/conns.log", "a") as f:
        f.write(f"{time.time():.3f}\n")
    c.close()
"""

results = []


def sh(*args, check=True, **kw):
    return subprocess.run(args, check=check, capture_output=True, text=True, **kw)


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


def options(**extra):
    o = {
        "dashboard_url": "http://dashboard.invalid", "signing_key": "k" * 64, "agent_name": "downtown",
        "printer_uri": "ipp://192.0.2.1/ipp/print", "printer_ip": "192.0.2.1", "snmp_community": "public",
        "listen_port": 8080, "lan_ip": "192.168.22.254", "lan_subnet": "192.168.22.0/24",
        "store_timezone": "America/Denver", "revel_caller_id_mode": "off", "revel_caller_id_port": 3520,
        "revel_caller_id_copies": 1, "revel_test_number": "505-555-0100", "revel_targets": [],
    }
    o.update(extra)
    return o


def start(name, opts, stdin=False, key=None):
    data = os.path.join(WORK, name)
    os.makedirs(data)
    if key:
        os.makedirs(os.path.join(data, "cryptocart"))
        with open(os.path.join(data, "cryptocart", "key"), "w") as f:
            f.write(key)
    with open(os.path.join(data, "options.json"), "w") as f:
        json.dump(opts, f)
    args = ["docker", "run", "--name", name, "--network", NET, "-v", f"{data}:/data", IMAGE]
    if stdin:
        proc = subprocess.Popen(args[:2] + ["-i"] + args[2:], stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    else:
        sh(*args[:2], "-d", *args[2:])
        proc = None
    return data, proc


def logs(name):
    r = sh("docker", "logs", name, check=False)
    return r.stdout + r.stderr


FIND = """import os, sys
for p in filter(str.isdigit, os.listdir("/proc")):
    try:
        cmd = open(f"/proc/{p}/cmdline", "rb").read().replace(b"\\0", b" ").decode()
    except OSError:
        continue
    if cmd.startswith(sys.argv[1]):
        print(p); break
"""


def pid(name, prefix):
    """The pid whose command line starts with prefix (a pattern match would find this probe itself)."""
    return sh("docker", "exec", name, "python3", "-c", FIND, prefix, check=False).stdout.strip()


def svstat(name, service):
    return sh("docker", "exec", name, "/command/s6-svstat", f"/run/service/{service}", check=False).stdout.strip()


def agent_answers(name):
    probe = "import socket; socket.create_connection(('127.0.0.1', 8080), 2).close()"
    return sh("docker", "exec", name, "python3", "-c", probe, check=False).returncode == 0


def wait_for(fn, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.5)
    return False


def down_once(name):
    """The box reported down once and s6 left it down, after long enough for a loop to show."""
    time.sleep(15)
    return logs(name).count('"event":"box_down"') == 1 and svstat(name, "box").startswith("down")


def tree(path):
    out = set()
    for root, dirs, files in os.walk(path):
        for n in dirs + files:
            out.add(os.path.relpath(os.path.join(root, n), path))
    return out


def cpu_ticks(name, p):
    r = sh("docker", "exec", name, "cat", f"/proc/{p}/stat", check=False).stdout.split()
    return int(r[13]) + int(r[14]) if len(r) > 15 else -1


def main():
    os.makedirs(SHARED)
    sh("docker", "network", "create", NET)
    try:
        sh("docker", "run", "-d", "--name", "company", "--network", NET, "--network-alias", COMPANY,
           "-v", f"{SHARED}:/shared", "--entrypoint", "python3", IMAGE, "-c", COUNTER)

        start("notset", options())
        check("not set up: agent answers on 8080", wait_for(lambda: agent_answers("notset"), 30))
        check("not set up: box down once, no restart loop", down_once("notset"), logs("notset")[-800:])

        refusals = (("badlan-public", "BOX_LAN_IP 8.8.8.8", {"lan_ip": "8.8.8.8"}),
                    ("badlan-bridge", "BOX_LAN_IP 172.30.32.5", {"lan_ip": "172.30.32.5"}),
                    ("badcompany", "company 'Not A Company'", {"cryptocart_company": "Not A Company"}))
        for name, what, extra in refusals:
            start(name, options(**{"cryptocart_company": COMPANY, **extra}))
            check(f"{what}: refused with its reason, exit 78",
                  wait_for(lambda: "box configuration refused:" in logs(name) and '"exit":78' in logs(name), 30),
                  logs(name)[-800:])
            check(f"{what}: box down once, no restart loop", down_once(name), logs(name)[-800:])
            check(f"{what}: agent unaffected", agent_answers(name))

        # A paired box: it dials its company only once it holds a key (32 random bytes, base64url)
        key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
        data, proc = start("box", options(cryptocart_company=COMPANY), stdin=True, key=key)
        check("set up: box and agent both running",
              wait_for(lambda: pid("box", "node /opt/nmt-box") and pid("box", "python3 -u /opt/nmt-print-agent/agent.py") and agent_answers("box"), 30))
        node = pid("box", "node /opt/nmt-box")
        t0 = cpu_ticks("box", node)
        time.sleep(WAIT_S)
        ticks = cpu_ticks("box", node) - t0
        with open(os.path.join(SHARED, "conns.log")) as f:
            conns = [float(x) for x in f.read().split()]
        gaps = [round(b - a, 1) for a, b in zip(conns, conns[1:])]
        check("unreachable company: retries with backoff", 4 <= len(conns) <= 12 and gaps == sorted(gaps)
              and max(gaps) <= 33, f"{len(conns)} attempts, gaps {gaps}")
        print(f"      attempts {len(conns)} in {WAIT_S} s, gaps {gaps}")
        hz = int(sh("docker", "exec", "box", "getconf", "CLK_TCK").stdout.strip() or 100)
        cpu = ticks / hz / WAIT_S * 100
        check("unreachable company: CPU idle (under 1%)", 0 <= cpu < 1, f"{cpu:.2f}%")
        rss = sh("docker", "exec", "box", "sh", "-c", f"grep VmRSS /proc/{node}/status").stdout.split()
        print(f"      box CPU {cpu:.2f}% over {WAIT_S} s; idle memory {' '.join(rss[1:])}")

        before = tree(data)
        n = len(conns)
        code = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
        proc.stdin.write(code + "\n")
        proc.stdin.flush()
        check("stdin: a line reaches the box as a pairing code, sent to the company",
              wait_for(lambda: '"event":"pair_failed"' in logs("box"), 30)
              and len(open(os.path.join(SHARED, "conns.log")).read().split()) > n, logs("box")[-800:])
        new = {p for p in tree(data) - before}
        box_files = {p for p in tree(data) if p.startswith("cryptocart")}
        check("box writes only under /data/cryptocart",
              all(p.startswith("cryptocart") or p in ("options.json", "state.json", "requests.json", "spool") for p in tree(data)),
              sorted(tree(data)))
        check("box data folder made", {"cryptocart/journal", "cryptocart/tls"} <= box_files, sorted(box_files))
        diff = sh("docker", "diff", "box").stdout.split("\n")
        stray = [line for line in diff if line and "cryptocart" in line]
        check("nothing of the box's outside /data", not stray, stray)
        print(f"      new under /data after pairing: {sorted(new) or 'nothing'}; connections during it {len(open(os.path.join(SHARED, 'conns.log')).read().split()) - n}")

        agent_pid = pid("box", "python3 -u /opt/nmt-print-agent/agent.py")
        sh("docker", "exec", "box", "kill", "-9", node)
        check("box killed: restarted alone",
              wait_for(lambda: pid("box", "node /opt/nmt-box") not in ("", node), 20)
              and pid("box", "python3 -u /opt/nmt-print-agent/agent.py") == agent_pid and agent_answers("box"))
        node = pid("box", "node /opt/nmt-box")
        sh("docker", "exec", "box", "kill", "-9", agent_pid)
        check("agent killed: restarted alone, box untouched",
              wait_for(lambda: pid("box", "python3 -u /opt/nmt-print-agent/agent.py") not in ("", agent_pid) and agent_answers("box"), 30)
              and pid("box", "node /opt/nmt-box") == node)

        leaked = [line for line in logs("box").splitlines() if code in line or key in line]
        check("logs: neither the pairing code nor the key is logged", not leaked, leaked)
        proc.stdin.close()
    finally:
        for c in ("company", "notset", "badlan-public", "badlan-bridge", "badcompany", "box"):
            sh("docker", "rm", "-f", c, check=False)
        sh("docker", "network", "rm", NET, check=False)
        shutil.rmtree(WORK, ignore_errors=True)
    print(f"\n{sum(results)} passed, {len(results) - sum(results)} failed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
