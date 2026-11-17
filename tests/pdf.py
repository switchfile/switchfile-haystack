def table_pdf() -> bytes:
    """Build a one-page PDF with a heading and a ruled 3x3 table, without any PDF library."""
    rows = [("Item", "Qty", "Price"), ("Widget", "2", "10.50"), ("Gadget", "1", "99.00")]
    ops = ["BT /F1 18 Tf 72 760 Td (Invoice 4711) Tj ET"]
    left, top, width, height = 72, 720, 150, 24
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            x, y = left + c * width + 6, top - (r + 1) * height + 8
            ops.append(f"BT /F1 11 Tf {x} {y} Td ({text}) Tj ET")
    for r in range(len(rows) + 1):
        y = top - r * height
        ops.append(f"{left} {y} m {left + 3 * width} {y} l S")
    for c in range(4):
        x = left + c * width
        ops.append(f"{x} {top} m {x} {top - len(rows) * height} l S")
    stream = "\n".join(ops).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)
