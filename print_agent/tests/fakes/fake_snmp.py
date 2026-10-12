"""Fake printer SNMP agent for the e2e test: answers GET for the page counter
with the number in $SHARED/count. `$SHARED/mode` = "snmpdown" stops answers."""

import os
import socket
import sys

sys.path.insert(0, "/opt/nmt-print-agent")
import snmp  # noqa: E402

SHARED = os.environ.get("SHARED", "/shared")


def read(name, default=""):
    try:
        with open(os.path.join(SHARED, name)) as f:
            return f.read().strip()
    except FileNotFoundError:
        return default


sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 161))
while True:
    data, peer = sock.recvfrom(4096)
    if read("mode") == "snmpdown":
        continue
    _, (ver, comm, (_, fields)), _ = snmp.decode(data)
    req_id = fields[0][1]
    oid_raw = fields[3][1][0][1][0][1]
    count = int(read("count", "0"))
    vb = snmp.tlv(0x30, snmp.tlv(0x30, snmp.tlv(0x06, oid_raw) + snmp.enc_int(count, 0x41)))
    pdu = snmp.tlv(0xA2, snmp.enc_int(req_id) + snmp.enc_int(0) + snmp.enc_int(0) + vb)
    sock.sendto(snmp.tlv(0x30, snmp.enc_int(1) + snmp.tlv(0x04, comm[1]) + pdu), peer)
