import time, shutil, sys
from ..core.style import rgb, bgrgb, BOLD, DIM, ITAL, RST, vlen, rpad, clip, _ANSI_RE
from .themes import get_palette, THEME, rc

_SYMBOLS = ["◰", "◳", "◲", "◱"]
_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_PULSE   = ["○", "◔", "◐", "◕", "●", "◕", "◐", "◔"]

class BorderEngine:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self._box_w = 0
        self._inn_w = 0
        self._palette = None
        self._border_seg_cache = {}
        self._mkt_cache = (False, "CLOSED", "")
        self._mkt_cache_ts = 0.0
        self._mkt_cache_ttl = 5.0
        self._dim_cache = {}
        self._clip_cache = {}
        self._rpad_cache = {}
        self._cache_max = 512
        self._refresh_dimensions()

    def _clear_caches(self):
        if len(self._dim_cache) > self._cache_max: self._dim_cache.clear()
        if len(self._clip_cache) > self._cache_max: self._clip_cache.clear()
        if len(self._rpad_cache) > self._cache_max: self._rpad_cache.clear()

    def _refresh_dimensions(self):
        w = shutil.get_terminal_size((100, 40)).columns
        self._box_w = max(82, min(w - 2, 120))
        self._inn_w = self._box_w - 2
        self._palette = get_palette()
        self._prebuilt_top = f"{self._palette.amber}╔{'═'*(self._box_w-2)}╗{RST}"
        self._prebuilt_bot = f"{self._palette.amber}╚{'═'*(self._box_w-2)}╝{RST}"
        self._prebuilt_div = f"{self._palette.amber}╠{'─'*(self._box_w-2)}╣{RST}"

    @property
    def box_w(self): return self._box_w
    @property
    def inn_w(self): return self._inn_w

    def _fast_row(self, content, w=None):
        w = w or self._box_w
        P = self._palette
        inn = w - 2
        raw = content
        if _ANSI_RE.search(content):
            ck = (content, inn)
            if ck in self._clip_cache:
                clp = self._clip_cache[ck]
            else:
                clp = clip(content, inn)
                if len(self._clip_cache) < self._cache_max:
                    self._clip_cache[ck] = clp
        else:
            clp = content if len(content) <= inn else content[:inn-1] + "…"
        rk = (clp, inn)
        if rk in self._rpad_cache:
            pad = self._rpad_cache[rk]
        else:
            vl = vlen(clp)
            pad = clp if vl >= inn else clp + " " * (inn - vl)
            if len(self._rpad_cache) < self._cache_max:
                self._rpad_cache[rk] = pad
        return f"{P.amber}║{RST}{pad}{P.amber}║{RST}"

    def bb_top(self, label=""):
        if not label: return self._prebuilt_top
        P = self._palette
        lbl = f"  {BOLD}{P.head}{label}{RST}{P.amber}  "
        lbl_vis = len(label) + 4
        fill = self._box_w - 2 - lbl_vis
        lf = fill // 2; rf = fill - lf
        return f"{P.amber}╔{'═'*lf}{RST}{lbl}{P.amber}{'═'*rf}╗{RST}"

    def bb_bot(self): return self._prebuilt_bot

    def bb_div(self, label=""):
        if not label: return self._prebuilt_div
        P = self._palette; w = self._box_w
        lv = len(label) + 4; fill = w - 2 - lv
        l = fill // 2; r = fill - l
        return f"{P.amber}╠{'─'*l}  {BOLD}{P.amber_l}{label}{RST}{P.amber}  {'─'*r}╣{RST}"

    def bb_row(self, content):
        if not _ANSI_RE.search(content) and len(content) <= self._inn_w:
            pad = content + " " * (self._inn_w - len(content))
            return f"{self._palette.amber}║{RST}{pad}{self._palette.amber}║{RST}"
        return self._fast_row(content)

    def bb_kv(self, label, value, label_w=22, val_color=None):
        P = self._palette; vc = val_color or P.amber_l
        return self.bb_row(f"  {P.label}{label:<{label_w}}{RST}{BOLD}{vc}{value}{RST}")

    def bb_2col(self, l1, v1, l2, v2):
        P = self._palette; half = self._inn_w // 2
        a = f"  {P.label}{l1:<18}{RST}{BOLD}{P.amber_l}{v1}{RST}"
        b = f"  {P.label}{l2:<18}{RST}{BOLD}{P.amber_l}{v2}{RST}"
        p1 = rpad(a, half); p2 = rpad(b, self._inn_w - half)
        return f"{P.amber}║{RST}{p1}{P.amber}│{RST}{p2}{P.amber}║{RST}"

    def _market_status(self):
        now = time.time()
        if now - self._mkt_cache_ts < self._mkt_cache_ttl:
            return self._mkt_cache
        lt = time.gmtime(now + 5.5 * 3600)
        wd = lt.tm_wday; hr = lt.tm_hour + lt.tm_min / 60
        P = self._palette
        if wd >= 5:          r = (False, "WEEKEND",     P.amber_d)
        elif 9.0 <= hr < 9.25: r = (True, "PRE-MARKET", P.amber)
        elif 9.25 <= hr < 15.5: r = (True, "OPEN",       P.up)
        elif 15.5 <= hr < 16.0: r = (True, "AFTER-HOURS",P.blue)
        else:                  r = (False, "CLOSED",     P.dn)
        self._mkt_cache = r; self._mkt_cache_ts = now
        return r

    # ── Animated prompt with side ticker ─────────────────────────────────────────
    _anim_frame = 0
    _anim_ts = 0.0

    def _ticker_tape(self, frame, w):
        P = self._palette
        plain_items = ["NIFTY 24600", "BNK 51200", "SENSEX 81200",
                       "AAPL 198", "TSLA 345", "GOLD 71500"]
        dirs = [1, -1, 1, -1, 1, 1]  # 1=up, -1=down
        segs = []
        for i, (txt, d) in enumerate(zip(plain_items, dirs)):
            c = P.up if d > 0 else P.dn
            segs.append(f"{c}{txt}{RST}")
        text = " │ ".join(segs) + "   "
        text_plain = " │ ".join(plain_items) + "   "
        off = frame % max(1, len(text_plain))
        # Safe substring: find the nearest plain char boundary
        plain_len = len(text_plain)
        end = min(off + w, plain_len)
        # Build result by mapping plain positions back to ANSI string
        result = []
        p = 0
        i = 0
        while i < len(text) and p < end:
            m = _ANSI_RE.match(text, i)
            if m:
                if p >= off:
                    result.append(m.group())
                i = m.end()
            else:
                if off <= p < end:
                    result.append(text[i])
                p += 1
                i += 1
        return P.bg3 + P.amber_d + "".join(result) + RST

    def bb_prompt(self, frame=None):
        if frame is None: frame = self._anim_frame
        P = self._palette
        now = time.strftime("%H:%M:%S", time.gmtime(time.time() + 5.5 * 3600))
        _, ses, sc = self._market_status()
        spin = _SPINNER[frame % len(_SPINNER)]
        pulse = _PULSE[frame % len(_PULSE)]
        fixed = f"{P.bg3}{BOLD}{P.amber} PT{RST}{P.bg3}{P.label}{now}{RST}{P.bg3} {sc}{pulse}{RST}{P.bg3}{P.label} {ses} {RST}"
        fixed_vis = 26 + len(ses)  # PT(3)+space(1)+time(8)+space(1)+pulse(1)+space(1)+ses+space(1)
        tape_w = max(4, self._box_w - fixed_vis - 2)
        tape = self._ticker_tape(frame, tape_w)
        return (
            f"\n{fixed}{tape}\n"
            f"{BOLD}{P.go}▶ {RST}"
        )

    def nx_prompt(self, frame=None):
        if frame is None: frame = self._anim_frame
        t = time.time() % 10.0
        now = time.strftime("%H:%M:%S", time.gmtime(time.time() + 5.5 * 3600))
        _, ses, sc = self._market_status()
        spin = _SPINNER[frame % len(_SPINNER)]
        r1, g1, b1 = rc(t, 0.0, speed=0.5)
        r2, g2, b2 = rc(t, 0.33, speed=0.5)
        r3, g3, b3 = rc(t, 0.66, speed=0.5)
        fixed = f"{rgb(r1,g1,b1)}┌─[{RST}{BOLD}{rgb(r1,g1,b1)}PT{RST}{rgb(r1,g1,b1)}]─[{RST}{rgb(r2,g2,b2)}{now}{RST}{rgb(r1,g1,b1)}]{RST} {sc}{spin}{RST} {rgb(r2,g2,b2)}{ses}{RST} "
        fixed_vis = 19 + len(ses)
        tape_w = max(4, self._box_w - fixed_vis - 3)
        seg = self._ticker_tape(frame, tape_w)
        return (
            f"\n{fixed}{seg}{rgb(r1,g1,b1)}]{RST}\n"
            f"{rgb(r3,g3,b3)}└─▶{RST} "
        )

    # ── Print result (cached, fast) ──────────────────────────────────────────────
    def bb_print_result(self, response):
        if not response or not response.strip(): return
        ts = time.strftime("%H:%M:%S", time.gmtime(time.time() + 5.5 * 3600))
        lines = response.strip().split("\n")
        P = self._palette
        top = self._prebuilt_top
        div = self._prebuilt_div
        bot = self._prebuilt_bot
        info = f"{P.amber}║{RST}  {P.label}{ts}  ·  {len(lines)} lines{RST}  {P.amber}║{RST}"
        parts = ["\n", top, "\n", info, "\n", div, "\n"]
        for ln in lines:
            if not _ANSI_RE.search(ln) and len(ln) <= self._inn_w:
                pad = ln + " " * (self._inn_w - len(ln))
                row = f"{P.amber}║{RST}{pad}{P.amber}║{RST}"
            else:
                row = self._fast_row(ln)
            parts.append(row); parts.append("\n")
        parts += [div, "\n", info, "\n", bot, "\n"]
        sys.stdout.write("".join(parts)); sys.stdout.flush()

    def _nx_print_result(self, response):
        if not response or not response.strip(): return
        t_now = time.time() % 10.0
        ts = time.strftime("%H:%M:%S", time.gmtime(time.time() + 5.5 * 3600))
        lines = response.strip().split("\n")
        top = self.nx_top(t_now); div = self.nx_div(t_now); bot = self.nx_bot(t_now)
        r1,g1,b1 = rc(t_now,0.0,0.4); r2,g2,b2 = rc(t_now,0.33,0.4)
        r3,g3,b3 = rc(t_now,0.66,0.4); r4,g4,b4 = rc(t_now,0.5,0.4)
        hdr = f" {BOLD}{rgb(r1,g1,b1)}⬡ OUTPUT{RST}  {DIM}{rgb(r2,g2,b2)}{ts}{RST}  {DIM}{rgb(r3,g3,b3)}{len(lines)} lines{RST}"
        parts = ["\n", top, "\n", self.nx_row(hdr, t_now, 0), "\n", div, "\n"]
        for i, ln in enumerate(lines):
            parts.append(self.nx_row(ln, t_now, i+1)); parts.append("\n")
        parts += [div, "\n", self.nx_row(f" {DIM}{rgb(r4,g4,b4)}PT  ·  {time.strftime('%H:%M:%S', time.gmtime(time.time()+5.5*3600))}{RST}", t_now, len(lines)+2), "\n", bot, "\n"]
        sys.stdout.write("".join(parts)); sys.stdout.flush()

    def print_result(self, response, theme=None):
        th = theme or THEME
        if th in ("bloomberg", "minimal"):
            self.bb_print_result(response)
        else:
            self._nx_print_result(response)

    def prompt_line(self, frame=0):
        self._anim_frame = frame
        if THEME in ("bloomberg", "minimal"):
            return self.bb_prompt(frame)
        return self.nx_prompt(frame)

    # ── Rainbow nexus primitives ─────────────────────────────────────────────────
    def _build_gradient_border(self, t, offset=0.0):
        key = (round(t, 2), round(offset, 2))
        if key in self._border_seg_cache:
            return self._border_seg_cache[key]
        stops = 6; seg_w = self._inn_w // stops
        parts = []
        for i in range(stops):
            r, g, b = rc(t, offset + i/stops, 0.4)
            ws = seg_w if i < stops-1 else self._inn_w - seg_w*(stops-1)
            parts.append(f"{rgb(r,g,b)}{'═'*ws}{RST}")
        res = "".join(parts)
        if len(self._border_seg_cache) > 128: self._border_seg_cache.clear()
        self._border_seg_cache[key] = res
        return res

    def nx_top(self, t=0.0):
        r0,g0,b0 = rc(t,0.0,0.4); r1,g1,b1 = rc(t,1.0,0.4)
        return f"{rgb(r0,g0,b0)}╔{RST}{self._build_gradient_border(t,0.0)}{rgb(r1,g1,b1)}╗{RST}"

    def nx_bot(self, t=0.0):
        r0,g0,b0 = rc(t,0.5,0.4); r1,g1,b1 = rc(t,1.5,0.4)
        return f"{rgb(r0,g0,b0)}╚{RST}{self._build_gradient_border(t,0.5)}{rgb(r1,g1,b1)}╝{RST}"

    def nx_div(self, t=0.0, label=""):
        if not label:
            fill = self._build_gradient_border(t,0.25)
            r0,g0,b0 = rc(t,0.25,0.4); r1,g1,b1 = rc(t,1.25,0.4)
            return f"{rgb(r0,g0,b0)}╠{RST}{fill}{rgb(r1,g1,b1)}╣{RST}"
        lp = f" {label} "; avail = self._inn_w - len(lp); lw = avail//2; rw = avail-lw
        r0,g0,b0 = rc(t,0.1,0.4); re,ge,be = rc(t,0.9,0.4); lr,lg,lb = rc(t,0.5,0.4)
        return f"{rgb(r0,g0,b0)}╠{'═'*lw}{RST}{BOLD}{rgb(lr,lg,lb)}{lp}{RST}{rgb(re,ge,be)}{'═'*rw}╣{RST}"

    def nx_row(self, content, t=0.0, row_idx=0):
        clipped = clip(content, self._inn_w)
        padded = rpad(clipped, self._inn_w)
        off = row_idx / 60.0
        rl,gl,bl = rc(t, off, 0.35); rr,gr,br = rc(t, off+0.5, 0.35)
        return f"{rgb(rl,gl,bl)}║{RST}{padded}{rgb(rr,gr,br)}║{RST}"

    def sparkline(self, values, w=20):
        BARS = "▁▂▃▄▅▆▇█"
        P = self._palette
        if len(values) < 2: return P.amber_d + "─"*w + RST
        try:
            import numpy as _np
            xs = _np.linspace(0, len(values)-1, w)
            pts = _np.interp(xs, _np.arange(len(values)), values)
        except ImportError:
            pts = [values[int(i/len(values)*w)] for i in range(w)]
        lo, hi = min(pts), max(pts); rng = hi-lo or 1
        out = ""
        for v in pts:
            n = (v-lo)/rng; b = BARS[min(int(n*7),7)]
            out += (P.up if n >= 0.5 else P.dn) + b
        return out + RST
