import math
from ..core.style import rgb, bgrgb, BOLD, DIM, RST
from ..core.config import CFG, save_config

THEME = CFG.get("theme", "bloomberg")


def set_theme(name):
    global THEME
    name = name.lower().strip()
    if name not in ("bloomberg", "nexus", "minimal"):
        return f"Unknown theme '{name}'. Available: bloomberg, nexus, minimal"
    THEME = name
    CFG["theme"] = name
    save_config()
    return f"Theme set to '{THEME}'."


def get_theme():
    return THEME


# ═══════════════════════════════════════════════════════════════════════════════
#  BLOOMBERG THEME — Amber on near-black
# ═══════════════════════════════════════════════════════════════════════════════
class BloombergPalette:
    amber = rgb(255, 170, 0)
    amber_d = rgb(200, 120, 0)
    amber_l = rgb(255, 215, 80)
    bg = bgrgb(10, 10, 15)
    bg2 = bgrgb(18, 18, 28)
    bg3 = bgrgb(0, 30, 60)
    border = rgb(60, 80, 100)
    up = rgb(0, 210, 100)
    dn = rgb(255, 60, 60)
    flat = rgb(180, 180, 180)
    cyan = rgb(0, 200, 220)
    white = rgb(220, 225, 230)
    head = rgb(255, 255, 255)
    label = rgb(130, 145, 160)
    go = rgb(255, 200, 0)
    hi = rgb(255, 255, 0)
    news = rgb(180, 220, 255)
    red = rgb(255, 80, 80)
    blue = rgb(60, 130, 220)


# ═══════════════════════════════════════════════════════════════════════════════
#  NEXUS THEME — Sci-fi rainbow on dark
# ═══════════════════════════════════════════════════════════════════════════════
class NexusPalette:
    aqua = rgb(0, 255, 255)
    neon = rgb(57, 255, 20)
    pink = rgb(255, 0, 153)
    yellow = rgb(255, 230, 0)
    orange = rgb(255, 140, 0)
    lav = rgb(180, 130, 255)
    ice = rgb(100, 220, 255)
    red = rgb(255, 50, 50)
    white = rgb(230, 230, 230)
    grey = rgb(100, 110, 120)
    dim = rgb(60, 70, 80)
    bg = bgrgb(5, 5, 10)
    bg2 = bgrgb(12, 12, 22)
    # Gradient helpers
    up = rgb(0, 210, 100)
    dn = rgb(255, 60, 60)


# ═══════════════════════════════════════════════════════════════════════════════
#  MINIMAL THEME — Clean, minimal
# ═══════════════════════════════════════════════════════════════════════════════
class MinimalPalette:
    amber = rgb(180, 180, 180)
    amber_d = rgb(120, 120, 120)
    amber_l = rgb(200, 200, 200)
    bg = bgrgb(15, 15, 20)
    bg2 = bgrgb(22, 22, 28)
    bg3 = bgrgb(30, 30, 40)
    border = rgb(80, 80, 90)
    up = rgb(100, 200, 100)
    dn = rgb(200, 100, 100)
    flat = rgb(150, 150, 150)
    cyan = rgb(100, 180, 220)
    white = rgb(200, 200, 200)
    head = rgb(220, 220, 220)
    label = rgb(140, 140, 140)
    go = rgb(180, 180, 180)
    hi = rgb(255, 255, 255)
    news = rgb(180, 200, 220)
    red = rgb(200, 80, 80)
    blue = rgb(80, 130, 200)


def get_palette():
    if THEME == "bloomberg":
        return BloombergPalette
    elif THEME == "nexus":
        return NexusPalette
    else:
        return MinimalPalette


# ── Rainbow cycle for nexus theme ──────────────────────────────────────────────
_rainbow_cache = {}


def _rainbow(t, offset=0.0, speed=1.0):
    phase = (t * speed + offset) * 2 * math.pi
    r = int(127 + 127 * math.sin(phase))
    g = int(127 + 127 * math.sin(phase + 2.094))
    b = int(127 + 127 * math.sin(phase + 4.189))
    return r, g, b


def rc(t, offset=0.0, speed=1.0):
    key = (round(t, 2), round(offset, 2), round(speed, 2))
    if key not in _rainbow_cache:
        if len(_rainbow_cache) > 512:
            _rainbow_cache.clear()
        _rainbow_cache[key] = _rainbow(t, offset, speed)
    return _rainbow_cache[key]
