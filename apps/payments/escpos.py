"""ESC/POS bytes for laid-out receipt lines [(text, style)] (see bills.receipt_lines)."""
ESC, GS = b"\x1b", b"\x1d"
INIT = ESC + b"@"
ALIGN_LEFT, ALIGN_CENTER = ESC + b"a\x00", ESC + b"a\x01"
BOLD_ON, BOLD_OFF = ESC + b"E\x01", ESC + b"E\x00"
DOUBLE, NORMAL_SIZE = GS + b"!\x11", GS + b"!\x00"
FEED_CUT = ESC + b"d\x04" + GS + b"V\x42\x00"  # feed, then partial cut


def _txt(s: str) -> bytes:
    return s.encode("cp437", errors="replace")


def escpos_from_lines(lines, copies: int = 1) -> bytes:
    """Lines are already padded/centred to the paper width; BIG lines print double size."""
    one = [INIT, ALIGN_LEFT]
    for text, style in lines:
        if style == "big":
            one += [DOUBLE, BOLD_ON, _txt(text + "\n"), BOLD_OFF, NORMAL_SIZE]
        elif style == "bold":
            one += [BOLD_ON, _txt(text + "\n"), BOLD_OFF]
        else:
            one.append(_txt(text + "\n"))
    one.append(FEED_CUT)
    return b"".join(one) * max(1, min(copies, 5))
