"""A minimal, valid, Letter-size test PDF (standard library only).

The agent deletes every PDF once its job is done, so tests can't reuse one the
dashboard sent; they print this instead. No customer data.
"""

import time


def _esc(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(title="NMT print agent - TEST PAGE", pages=1):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    objs = {1: b"<< /Type /Catalog /Pages 2 0 R >>",
            3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
    kids = []
    for i in range(pages):
        page, content = 4 + 2 * i, 5 + 2 * i
        kids.append(f"{page} 0 R")
        lines = [(28, title), (14, "This is a test of the downtown print agent. It is not an order."),
                 (14, f"Page {i + 1} of {pages}    Sent {stamp}")]
        y, ops = 720, []
        for size, text in lines:
            ops.append(f"BT /F1 {size} Tf 72 {y} Td ({_esc(text)}) Tj ET")
            y -= size + 18
        stream = "\n".join(ops).encode()
        objs[page] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                      f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content} 0 R >>").encode()
        objs[content] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
    objs[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {pages} >>".encode()

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"
    xref = len(out)
    size = max(objs) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for num in range(1, size):
        out += b"%010d 00000 n \n" % offsets[num]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref)
    return bytes(out)


if __name__ == "__main__":
    import sys
    sys.stdout.buffer.write(make_pdf(pages=int(sys.argv[1]) if len(sys.argv) > 1 else 1))
