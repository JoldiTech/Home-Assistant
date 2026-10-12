"""The handler registry for typed requests (store_requests.py).

Static: these three and nothing else. Printing is not here; it keeps its own
route (POST /print) and queue in agent.py.

- diagnostic.echo   returns its challenge through the real request queue.
                    Sends nothing anywhere.
- diagnostic.probe  pings one configured register (3 echo requests, 1 s
                    each). A reply means the address answered; no reply
                    proves nothing (iPads drop pings while asleep).
- revel.caller_id   one CallerID.com-format call record, by UDP unicast, to
                    one configured register. See callerid.py for the packet.
"""

import datetime
import ipaddress
import re
import socket
import subprocess
import time

import callerid

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

# What a dashboard may name as the agent itself.
AGENT_TARGET = "agent"


class Echo:
    type = "diagnostic.echo"
    version = "1"
    max_ttl = 600
    queue_max = 50
    per_minute = 30
    sends = "nothing (no printer, no network, no Revel)"

    def targets(self):
        return {AGENT_TARGET: {"id": AGENT_TARGET, "name": "this agent"}}

    def validate_payload(self, payload, target, dry_run):
        if set(payload) != {"challenge"}:
            return None, "payload must be exactly {\"challenge\": ...}"
        c = payload["challenge"]
        if not isinstance(c, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", c):
            return None, "challenge: 8-64 characters of A-Z a-z 0-9 _ -"
        return {"challenge": c}, None

    def permitted(self, target, payload, dry_run):
        return True, None, None

    def execute(self, record, gate):
        return {"state": "dry_run_passed" if record["dry_run"] else "completed",
                "result": {"challenge": record["payload"]["challenge"]},
                "evidence": {"executed_by": f"handler:{self.type} worker", "outbound_sends": 0}}

    def capability(self):
        return {"type": self.type, "version": self.version, "available": True, "targets": [AGENT_TARGET],
                "max_ttl_s": self.max_ttl, "rate_per_minute": self.per_minute, "queue_max": self.queue_max,
                "sends": self.sends,
                "payload": {"challenge": "required, 8-64 characters of A-Z a-z 0-9 _ -"},
                "result": {"challenge": "the same string, returned through the handler's queue"}}


class Registers:
    """The configured Revel registers, from the add-on options. The only
    addresses any handler here will ever contact."""

    ID = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")

    def __init__(self, opts, lan_subnet):
        self.items = {}
        self.problems = []
        net = None
        try:
            net = ipaddress.ip_network(lan_subnet)
        except ValueError:
            self.problems.append("lan_subnet is not a network")
        for t in opts.get("revel_targets") or []:
            tid = str(t.get("id", ""))
            try:
                ip = ipaddress.ip_address(str(t.get("ip", "")))
            except ValueError:
                self.problems.append(f"target {tid[:32]!r}: ip is not an address")
                continue
            if not self.ID.fullmatch(tid) or tid == AGENT_TARGET or tid in self.items:
                self.problems.append(f"target {tid[:32]!r}: bad or repeated id")
                continue
            if ip.version != 4 or not ip.is_private or (net is not None and ip not in net):
                self.problems.append(f"target {tid}: ip must be a private IPv4 on {lan_subnet}")
                continue
            self.items[tid] = {
                "id": tid, "name": str(t.get("name") or tid)[:40], "ip": str(ip),
                "revel_station": t.get("revel_station"), "test": bool(t.get("test")),
            }

    def __contains__(self, tid):
        return tid in self.items

    def get(self, tid):
        return self.items.get(tid)


class Probe:
    type = "diagnostic.probe"
    version = "1"
    max_ttl = 600
    queue_max = 10
    per_minute = 6
    sends = "3 ICMP echo requests to one configured register"

    def __init__(self, registers):
        self.registers = registers

    def targets(self):
        return self.registers.items

    def validate_payload(self, payload, target, dry_run):
        if payload:
            return None, "payload must be {}"
        return {}, None

    def permitted(self, target, payload, dry_run):
        return True, None, None

    def execute(self, record, gate):
        t = self.registers.get(record["target"])
        ev = {"target_ip": t["ip"], "method": "icmp echo x3, 1 s timeout each"}
        if record["dry_run"]:
            return {"state": "dry_run_passed", "evidence": dict(ev, outbound_sends=0)}
        if not gate():
            return {"state": "expired", "code": "expired", "message": "expired before the probe ran"}
        try:
            r = subprocess.run(["ping", "-n", "-c", "3", "-W", "1", t["ip"]],
                               capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as e:
            return {"state": "failed", "code": "probe_unavailable", "message": type(e).__name__,
                    "evidence": dict(ev, outbound_sends=0)}
        m = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", r.stdout)
        rtt = re.search(r"= [\d.]+/([\d.]+)/", r.stdout)
        sent, got = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        ev.update(outbound_sends=sent, replies=got, avg_rtt_ms=float(rtt.group(1)) if rtt else None,
                  reading=("answered" if got else
                           "no reply: does NOT show the register is off or unreachable (iPads ignore pings while asleep)"))
        return {"state": "completed", "evidence": ev, "result": {"answered": bool(got)}}

    def capability(self):
        return {"type": self.type, "version": self.version, "available": bool(self.registers.items),
                "targets": sorted(self.registers.items), "max_ttl_s": self.max_ttl,
                "rate_per_minute": self.per_minute, "queue_max": self.queue_max, "sends": self.sends,
                "payload": {}, "result": {"answered": "true if any reply came back; false proves nothing"}}


class CallerId:
    type = "revel.caller_id"
    version = "1"
    max_ttl = 120                 # a pickup alert older than two minutes is not worth showing
    queue_max = 20
    per_minute = 6
    MODES = ("off", "test", "on")

    def __init__(self, registers, opts, sock_factory=None):
        self.registers = registers
        self.mode = opts.get("revel_caller_id_mode") if opts.get("revel_caller_id_mode") in self.MODES else "off"
        self.port = int(opts.get("revel_caller_id_port") or callerid.PORT)
        self.test_number = re.sub(r"\D", "", str(opts.get("revel_test_number") or ""))
        # CallerID.com advises repeating a record for Wi-Fi displays (its
        # emulator sends 8 copies 40 ms apart; listeners drop repeats).
        # Whether Revel drops repeats is unverified, so the default is 1.
        self.copies = max(1, min(8, int(opts.get("revel_caller_id_copies") or 1)))
        self.tz = str(opts.get("store_timezone") or "America/Denver")
        self.sock_factory = sock_factory or (lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        self.icmp_wait = 1.0
        self.copy_gap = 0.04

    def store_time(self):
        """Now on the shop's clock; the record carries no zone."""
        if ZoneInfo is not None:
            try:
                return datetime.datetime.now(ZoneInfo(self.tz)).timetuple()
            except Exception:  # noqa: BLE001 — no tzdata: fall back to the container's clock
                pass
        return time.localtime()

    def targets(self):
        return self.registers.items

    def synthetic(self, payload):
        return payload.get("number") == self.test_number and payload.get("name", "").startswith("TEST")

    def validate_payload(self, payload, target, dry_run):
        if set(payload) != {"name", "number"}:
            return None, "payload must be exactly {\"name\": ..., \"number\": ...}"
        name, number = payload["name"], payload["number"]
        problem = callerid.check_name(name) or callerid.check_number(number)
        if problem:
            return None, problem
        return {"name": name, "number": callerid.digits(number)}, None

    def permitted(self, target, payload, dry_run):
        if not self.registers.items:
            return False, "handler_unavailable", "no Revel registers are configured"
        if dry_run:
            return True, None, None
        if self.mode == "off":
            return False, "live_disabled", "live caller-ID sends are off (revel_caller_id_mode: off); dry runs only"
        if self.mode == "test":
            if not self.registers.get(target)["test"]:
                return False, "live_not_permitted", "in test mode only a register marked test: true may get a live send"
            if not self.synthetic(payload):
                return False, "live_not_permitted", "in test mode a live send must use the configured test number and a name starting TEST"
        return True, None, None

    def preview(self, payload, packet):
        """The packet text only for the synthetic test identity; otherwise
        just its shape, so a customer's name and number never come back."""
        if self.synthetic(payload):
            return packet.decode("ascii")
        return None

    def execute(self, record, gate):
        t = self.registers.get(record["target"])
        packet = callerid.build(record["payload"]["name"], record["payload"]["number"], self.store_time())
        back = callerid.parse(packet)
        ev = {"protocol": callerid.VARIANT, "transport": "udp unicast", "destination_port": self.port,
              "recipients": 1, "recipient_scope": f"{t['id']} ({t['name']}) only, no broadcast",
              "copies": self.copies, "packet_bytes": len(packet),
              "self_check": "ok" if (back["name"], back["number"].replace("-", "")) ==
                            (record["payload"]["name"].rstrip(), record["payload"]["number"]) else "MISMATCH",
              "packet_preview": self.preview(record["payload"], packet)}
        if record["dry_run"]:
            return {"state": "dry_run_passed", "evidence": dict(ev, outbound_sends=0)}
        if not gate():
            return {"state": "expired", "code": "expired", "message": "not sent: its expiry passed",
                    "evidence": dict(ev, outbound_sends=0)}
        s = self.sock_factory()
        try:
            try:
                # connect() so a port-unreachable reply (nothing listening on
                # the register) surfaces as ECONNREFUSED on the next recv.
                s.connect((t["ip"], self.port))
                n = s.send(packet)
            except OSError as e:
                return {"state": "failed", "code": "send_error",
                        "message": f"the network refused the send ({type(e).__name__}); nothing went out",
                        "evidence": dict(ev, outbound_sends=0)}
            ev.update(outbound_sends=1, bytes_sent=n, sent_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            for _ in range(self.copies - 1):
                time.sleep(self.copy_gap)
                try:
                    s.send(packet)
                    ev["outbound_sends"] += 1
                except OSError:  # e.g. a port-unreachable from the first copy: stop repeating
                    break
            s.settimeout(self.icmp_wait)
            try:
                s.recv(512)
                ev["reply"] = "unexpected datagram (ignored)"
            except ConnectionRefusedError:
                ev["icmp"] = "port unreachable: the register answered that nothing listens on this port"
                return {"state": "failed", "code": "port_unreachable",
                        "message": "the register refused the packet: caller ID is not listening there",
                        "evidence": ev}
            except (socket.timeout, OSError):
                ev["icmp"] = f"no error within {self.icmp_wait:g} s (not proof of delivery)"
            return {"state": "sent", "evidence": ev}
        finally:
            s.close()

    @staticmethod
    def observations(record):
        if record["dry_run"] or record["state"] != "sent":
            return None
        return {"received_by_revel": "unknown", "displayed": "unknown", "sounded": "unknown",
                "operator_confirmed": "unknown",
                "basis": "UDP has no acknowledgment; only a person at the register can confirm these"}

    def capability(self):
        return {
            "type": self.type, "version": self.version,
            "available": bool(self.registers.items), "mode": self.mode,
            "live_sends": {"off": "refused", "test": "only to registers marked test, with the test identity",
                           "on": "to any configured register"}[self.mode],
            "targets": sorted(self.registers.items),
            "test_targets": sorted(k for k, v in self.registers.items.items() if v["test"]),
            "test_identity": {"name": "must start with TEST", "number": "the configured revel_test_number"},
            "max_ttl_s": self.max_ttl, "rate_per_minute": self.per_minute, "queue_max": self.queue_max,
            "protocol": callerid.VARIANT, "destination_port": self.port, "copies_per_request": self.copies,
            "store_timezone": self.tz,
            "delivery": "UDP unicast to the one named register; never broadcast",
            "payload": callerid.RULES,
            "states": ["accepted", "running", "dry_run_passed", "sent", "failed", "uncertain", "expired"],
            "evidence_limits": "sent = the network took the bytes. Whether Revel received, showed or sounded "
                               "anything is reported as unknown; ICMP port-unreachable is reported as failed.",
        }


def build(opts):
    """The registry, built once from the add-on options."""
    registers = Registers(opts, opts.get("lan_subnet") or "192.168.22.0/24")
    return registers, [Echo(), Probe(registers), CallerId(registers, opts)]
