r"""CallerID.com Ethernet call records (the "Whozz Calling?" UDP format Revel
listens for).

Source: CallerID.com Ethernet Link Supplement v3.1 (EL_Manual.pdf) and the
byte layout of CallerID.com's own Ethernet Emulator Deluxe
(github.com/callerid/ethernetEmulatorDeluxe). One record is 83 bytes:

    ^^<U> uuuuuu <S> ssssss $  01 I S 0000 G A1 MM/DD hh:mm AM NUMBER(14) NAME(15)
    \_ 21-byte header ______/  \_ 62 ASCII characters, single spaces between fields

The unit and serial fields are binary. Listeners skip the first 21 bytes
rather than search for "$" (CallerID.com warns a binary byte may be 0x24), so
the header only has to be the right length; we send the emulator's own bytes,
none of which is 0x24. No CR/LF follows the record.

We send only an inbound Start record ("I S"): that is all a Basic unit ever
sends, so every listener that supports Basic units acts on it. There is no
ring, off-hook or End record because there is no call.
"""

import re

PORT = 3520
VARIANT = "CallerID.com Ethernet Link, basic inbound start record (83 bytes)"
HEADER = b"^^<U>" + bytes([0, 0, 0, 0, 0, 1]) + b"<S>" + b"345678" + b"$"
NAME_CHARS = r"A-Za-z0-9 .,&'#/-"
NAME_MAX = 15                 # the record's name field; longer names are refused, never cut
NUMBER_FIELD = 14

RULES = {
    "name": f"required, 1-{NAME_MAX} characters of {NAME_CHARS.replace('A-Za-z0-9', 'A-Z a-z 0-9')} "
            "not starting with a space. Longer names are refused, never truncated.",
    "number": "required, a 10-digit North American number as 5055550100 or 505-555-0100; "
              "sent as 505-555-0100",
    "packet": f"{len(HEADER)}-byte header + 62 ASCII characters = 83 bytes, no line ending",
}


def digits(number):
    return re.sub(r"\D", "", number)


def check_name(name):
    if not isinstance(name, str) or not re.fullmatch(rf"[{NAME_CHARS}]{{1,{NAME_MAX}}}", name) or name.startswith(" "):
        return f"name: 1-{NAME_MAX} characters of letters, digits, space and . , & ' # / - (not starting with a space)"
    return None


def check_number(number):
    if not isinstance(number, str) or not re.fullmatch(r"\d{10}|\d{3}-\d{3}-\d{4}", number):
        return "number: 10 digits, as 5055550100 or 505-555-0100"
    if number[0] in "01" or digits(number)[3] in "01":
        return "number: not a valid North American number (area code and exchange cannot start with 0 or 1)"
    return None


def build(name, number, when, line=1):
    """The 83-byte record. `when` is a time.struct_time in store-local time
    (the record carries no zone, like a phone line's)."""
    d = digits(number)
    shown = f"{d[:3]}-{d[3:6]}-{d[6:]}"
    hour = when.tm_hour % 12 or 12
    record = (f"{line:02d} I S 0000 G A1 {when.tm_mon:02d}/{when.tm_mday:02d} "
              f"{hour:02d}:{when.tm_min:02d} {'AM' if when.tm_hour < 12 else 'PM'} "
              f"{shown.ljust(NUMBER_FIELD)} {name.ljust(NAME_MAX)}")
    packet = HEADER + record.encode("ascii")
    assert len(packet) == 83, len(packet)
    return packet


def parse(packet):
    """Read a record back the way listeners do (skip 21 bytes). Used by the
    tests and the dry run's self-check."""
    rec = packet[len(HEADER):].decode("ascii")
    return {"line": rec[0:2], "direction": rec[3], "event": rec[5], "duration": rec[7:11],
            "checksum": rec[12], "ring": rec[14:16], "date": rec[17:22], "time": rec[23:31],
            "number": rec[32:46].rstrip(), "name": rec[47:62].rstrip()}
