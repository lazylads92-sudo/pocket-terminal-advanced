import re, shutil
from .config import get_args

_args = get_args()
_COLOR_ENABLED = not _args.no_color


def _color(code):
    return f"\033[{code}m" if _COLOR_ENABLED else ""


def _rgb(r, g, b):
    return _color(f"38;2;{r};{g};{b}")


def _bgrgb(r, g, b):
    return _color(f"48;2;{r};{g};{b}")


class Style:
    CYAN = _color("96")
    GREEN = _color("92")
    RED = _color("91")
    YELLOW = _color("93")
    MAGENTA = _color("95")
    BOLD = _color("1")
    DIM = _color("2")
    ITALIC = _color("3")
    RESET = _color("0")

    @staticmethod
    def strip(text):
        return re.sub(r"\033\[[0-9;]*m", "", text)

    @staticmethod
    def terminal_width():
        return shutil.get_terminal_size((100, 40)).columns


RST = Style.RESET
BOLD = Style.BOLD
DIM = Style.DIM
ITAL = Style.ITALIC


def rgb(r, g, b):
    return _rgb(r, g, b)


def bgrgb(r, g, b):
    return _bgrgb(r, g, b)


# ── ANSI helpers ────────────────────────────────────────────────────────────────
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def vlen(s):
    return len(_ANSI_RE.sub("", s))


def rpad(s, width):
    v = vlen(s)
    return s if v >= width else s + " " * (width - v)


def clip(s, width):
    raw = _ANSI_RE.sub("", s)
    if len(raw) <= width:
        return s
    ansi_map = {}
    plain_pos = 0
    i = 0
    while i < len(s):
        m = _ANSI_RE.match(s, i)
        if m:
            ansi_map.setdefault(plain_pos, []).append(m.group())
            i = m.end()
        else:
            plain_pos += 1
            i += 1
    parts = []
    for idx, ch in enumerate(raw):
        if idx in ansi_map:
            parts.extend(ansi_map[idx])
        if idx >= width - 1:
            parts.append(RST + rgb(100, 110, 120) + "…" + RST)
            break
        parts.append(ch)
    return "".join(parts)
