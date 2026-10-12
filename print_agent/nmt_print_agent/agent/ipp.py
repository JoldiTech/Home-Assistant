"""Minimal IPP/1.1 client (RFC 8010/8011), standard library only.

Enough for the agent's two read operations: Get-Printer-Attributes against
the printer, and Get-Job-Attributes against the local CUPS. Printing itself
goes through CUPS (`lp`), never through this module.
"""

import http.client
import struct
import urllib.parse

GET_JOB_ATTRIBUTES = 0x0009
GET_PRINTER_ATTRIBUTES = 0x000B

# Delimiter tags
_OPERATION = 0x01
_END = 0x03

# Value tags
_INTEGER = 0x21
_BOOLEAN = 0x22
_ENUM = 0x23
_KEYWORD = 0x44
_URI = 0x45
_CHARSET = 0x47
_LANGUAGE = 0x48
_NAME = 0x42
_STRING_TAGS = range(0x41, 0x4A)  # text, name, keyword, uri, charset, language, mime…

# job-state values
JOB_PENDING, JOB_HELD, JOB_PROCESSING, JOB_STOPPED = 3, 4, 5, 6
JOB_CANCELED, JOB_ABORTED, JOB_COMPLETED = 7, 8, 9
JOB_TERMINAL = (JOB_CANCELED, JOB_ABORTED, JOB_COMPLETED)


class IPPError(Exception):
    pass


def _attr(tag, name, value):
    if isinstance(value, int):
        raw = struct.pack(">i", value)
    else:
        raw = value.encode("utf-8")
    n = name.encode("ascii")
    return struct.pack(">BH", tag, len(n)) + n + struct.pack(">H", len(raw)) + raw


def _encode(op, uri_attr, uri, requested=(), extra=()):
    out = [struct.pack(">BBHI", 2, 0, op, 1), bytes([_OPERATION])]
    out.append(_attr(_CHARSET, "attributes-charset", "utf-8"))
    out.append(_attr(_LANGUAGE, "attributes-natural-language", "en"))
    out.append(_attr(_URI, uri_attr, uri))
    for tag, name, value in extra:
        out.append(_attr(tag, name, value))
    out.append(_attr(_NAME, "requesting-user-name", "nmt-print-agent"))
    for i, kw in enumerate(requested):
        out.append(_attr(_KEYWORD, "requested-attributes" if i == 0 else "", kw))
    out.append(bytes([_END]))
    return b"".join(out)


def _decode(data):
    """Return (status_code, {attribute-name: [values]}) merged over all groups."""
    if len(data) < 9:
        raise IPPError("short IPP response")
    status = struct.unpack(">H", data[2:4])[0]
    attrs = {}
    pos, last = 8, None
    while pos < len(data):
        tag = data[pos]
        pos += 1
        if tag == _END:
            break
        if tag < 0x10:  # start of another attribute group
            last = None
            continue
        nlen = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += 2
        name = data[pos:pos + nlen].decode("utf-8", "replace")
        pos += nlen
        vlen = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += 2
        raw = data[pos:pos + vlen]
        pos += vlen
        if tag in (_INTEGER, _ENUM) and vlen == 4:
            value = struct.unpack(">i", raw)[0]
        elif tag == _BOOLEAN and vlen == 1:
            value = bool(raw[0])
        elif tag in _STRING_TAGS:
            value = raw.decode("utf-8", "replace")
        else:
            value = raw
        if name:
            last = name
            attrs.setdefault(name, []).append(value)
        elif last is not None:  # additional value of the previous attribute
            attrs[last].append(value)
    return status, attrs


def _post(uri, body, timeout):
    u = urllib.parse.urlsplit(uri)
    port = u.port or 631
    if u.scheme in ("ipps", "https"):
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False  # printers present self-signed certificates
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(u.hostname, port, timeout=timeout, context=ctx)
    else:
        conn = http.client.HTTPConnection(u.hostname, port, timeout=timeout)
    try:
        conn.request("POST", u.path or "/", body, {"Content-Type": "application/ipp"})
        resp = conn.getresponse()
        data = resp.read(1 << 20)
        if resp.status != 200:
            raise IPPError(f"HTTP {resp.status}")
        return data
    finally:
        conn.close()


def get_printer_attributes(uri, requested, timeout=4.0):
    status, attrs = _decode(_post(uri, _encode(GET_PRINTER_ATTRIBUTES, "printer-uri", uri, requested), timeout))
    if status >= 0x0100:
        raise IPPError(f"IPP status 0x{status:04x}")
    return attrs


def get_job_attributes(printer_uri, job_id, requested, timeout=4.0):
    body = _encode(GET_JOB_ATTRIBUTES, "printer-uri", printer_uri, requested,
                   extra=[(_INTEGER, "job-id", int(job_id))])
    status, attrs = _decode(_post(printer_uri, body, timeout))
    if status >= 0x0100:
        raise IPPError(f"IPP status 0x{status:04x}")
    return attrs
