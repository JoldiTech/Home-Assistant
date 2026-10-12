"""Minimal SNMPv2c GET, standard library only.

The agent reads exactly one value over SNMP: the printer's lifetime page
counter (Printer-MIB prtMarkerLifeCount). A full SNMP stack would be a large
dependency for a single read-only GET.
"""

import os
import socket

_INT, _OCTETS, _NULL, _OID, _SEQ = 0x02, 0x04, 0x05, 0x06, 0x30
_GET, _RESPONSE = 0xA0, 0xA2
_NUMERIC = (0x02, 0x41, 0x42, 0x43, 0x46)  # INTEGER, Counter32, Gauge32, TimeTicks, Counter64


class SNMPError(Exception):
    pass


def _len(n):
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def tlv(tag, payload):
    return bytes([tag]) + _len(len(payload)) + payload


def enc_int(v, tag=_INT):
    return tlv(tag, v.to_bytes(max(1, (v.bit_length() + 8) // 8), "big", signed=True))


def enc_oid(oid):
    arcs = [int(a) for a in oid.strip(".").split(".")]
    out = bytearray([40 * arcs[0] + arcs[1]])
    for a in arcs[2:]:
        chunk = [a & 0x7F]
        a >>= 7
        while a:
            chunk.insert(0, 0x80 | (a & 0x7F))
            a >>= 7
        out += bytes(chunk)
    return tlv(_OID, bytes(out))


def decode(data, pos=0):
    """Parse one TLV at pos. Returns (tag, value, next_pos); constructed types
    decode to a list of (tag, value) children."""
    tag = data[pos]
    n = data[pos + 1]
    pos += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(data[pos:pos + k], "big")
        pos += k
    raw = data[pos:pos + n]
    if len(raw) != n:
        raise SNMPError("truncated")
    end = pos + n
    if tag & 0x20:  # constructed
        kids, p = [], 0
        while p < len(raw):
            t, v, p = decode(raw, p)
            kids.append((t, v))
        return tag, kids, end
    if tag in _NUMERIC:
        return tag, int.from_bytes(raw, "big", signed=(tag == _INT)), end
    return tag, raw, end


def get(host, oid, community="public", timeout=2.0, retries=1, port=161):
    """Return the integer value of `oid`, or raise SNMPError."""
    req_id = int.from_bytes(os.urandom(3), "big")
    varbinds = tlv(_SEQ, tlv(_SEQ, enc_oid(oid) + tlv(_NULL, b"")))
    pdu = tlv(_GET, enc_int(req_id) + enc_int(0) + enc_int(0) + varbinds)
    msg = tlv(_SEQ, enc_int(1) + tlv(_OCTETS, community.encode()) + pdu)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        for _ in range(retries + 1):
            sock.sendto(msg, (host, port))
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            try:
                _, (ver, comm, (ptag, fields)), _ = decode(data)
            except (ValueError, IndexError, TypeError) as e:
                raise SNMPError(f"malformed response: {e}")
            if ptag != _RESPONSE or fields[0][1] != req_id:
                continue
            if fields[1][1] != 0:
                raise SNMPError(f"error-status {fields[1][1]}")
            vtag, value = fields[3][1][0][1][1]
            if vtag not in _NUMERIC:
                raise SNMPError(f"no numeric value (tag 0x{vtag:02x})")
            return value
        raise SNMPError("timeout")
    finally:
        sock.close()
