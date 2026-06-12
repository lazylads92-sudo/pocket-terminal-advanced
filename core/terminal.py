import sys, os, time, datetime, select, re, tty, termios, atexit, signal
from .config import _HIST_FILE, CFG, get_args
from .style import Style

_ARGS = get_args()

# ── Session tracking ─────────────────────────────────────────────────────────────
class Session:
    def __init__(self):
        self.start_time = datetime.datetime.now()
        self.commands_run = 0
        self.errors = 0
        self.history = []

    def record(self, cmd, ok=True):
        self.commands_run += 1
        if not ok:
            self.errors += 1
        self.history.append(cmd)

    def elapsed(self):
        delta = datetime.datetime.now() - self.start_time
        secs = int(delta.total_seconds())
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h {m}m {s}s"
        elif m:
            return f"{m}m {s}s"
        return f"{s}s"

    def summary(self):
        return (
            f"  Commands run : {self.commands_run}\n"
            f"  Errors       : {self.errors}\n"
            f"  Session time : {self.elapsed()}\n"
            f"  Start time   : {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )


SESSION = Session()

# ── Signal handlers ──────────────────────────────────────────────────────────────
def _on_sigint(signum, frame):
    print(f"\n\n{Style.DIM}  [PT] Interrupted — type 'exit' to quit.{Style.RESET}")


signal.signal(signal.SIGINT, _on_sigint)


def _on_exit():
    if _READLINE_AVAILABLE:
        try:
            import readline
            readline.write_history_file(str(_HIST_FILE))
        except Exception:
            pass


atexit.register(_on_exit)

# ── Readline history ─────────────────────────────────────────────────────────────
_READLINE_AVAILABLE = False
try:
    import readline
    _READLINE_AVAILABLE = True
    if _HIST_FILE.exists():
        try:
            readline.read_history_file(str(_HIST_FILE))
        except Exception:
            pass
    readline.set_history_length(CFG.get("max_history", 500))
except ImportError:
    pass

# ── Raw-mode input with ghost text ───────────────────────────────────────────────
_CMD_CATALOGUE = []
_CMD_MAP = {}
_ALL_TOKENS = []


def set_catalogue(catalogue):
    global _CMD_CATALOGUE, _CMD_MAP, _ALL_TOKENS
    _CMD_CATALOGUE = catalogue
    _CMD_MAP = {cmd: (desc, args) for cmd, desc, args in catalogue}
    _ALL_TOKENS = sorted(_CMD_MAP.keys(), key=len, reverse=True)


def _best_suggestion(text):
    if not text:
        return None
    tl = text.lower()
    parts = tl.split(None, 1)
    cmd = parts[0]
    if cmd in _CMD_MAP:
        desc, examples = _CMD_MAP[cmd]
        if len(parts) == 1:
            return text + " " + desc.split("—")[0].strip()
        else:
            arg_part = parts[1]
            for ex in examples:
                if ex.lower().startswith(arg_part):
                    return cmd + " " + ex
        return None
    matches = [t for t in _ALL_TOKENS if t.startswith(tl)]
    if matches:
        best = min(matches, key=len)
        _, examples = _CMD_MAP[best]
        if examples:
            return best + " " + examples[0]
        return best
    return None


def input_with_ghost(prompt):
    if not sys.stdin.isatty():
        return input(prompt)
    fd = sys.stdin.fileno()
    old_tty = termios.tcgetattr(fd)
    HIDE = "\033[?25l"
    SHOW = "\033[?25h"
    CLR_EOL = "\033[K"
    G_ON = "\033[2m\033[36m"
    G_OFF = "\033[0m"
    buf = []
    hist_idx = -1
    saved_buf = []

    def _redraw(ghost=None):
        line = "".join(buf)
        ghost_txt = ""
        if ghost:
            if ghost.lower().startswith(line.lower()):
                tail = ghost[len(line) :]
                if tail:
                    ghost_txt = G_ON + tail + G_OFF
        sys.stdout.write("\r" + prompt + line + ghost_txt + CLR_EOL)
        if ghost_txt:
            raw_tail = re.sub(r"\033\[[0-9;]*m", "", ghost_txt)
            sys.stdout.write(f"\033[{len(raw_tail)}D")
        sys.stdout.flush()

    try:
        tty.setraw(fd)
        sys.stdout.write(HIDE)
        while True:
            current = "".join(buf)
            ghost = _best_suggestion(current)
            _redraw(ghost)
            ch = os.read(fd, 1)
            if ch in (b"\r", b"\n"):
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                result = "".join(buf).strip()
                if result and _READLINE_AVAILABLE:
                    try:
                        import readline
                        readline.add_history(result)
                    except Exception:
                        pass
                return result
            elif ch == b"\t":
                if ghost:
                    buf = list(ghost.split("—")[0].rstrip())
                continue
            elif ch == b"\x1b":
                seq = b""
                try:
                    tty.setraw(fd)
                    r, _, _ = select.select([fd], [], [], 0.05)
                    if r:
                        seq += os.read(fd, 1)
                        r2, _, _ = select.select([fd], [], [], 0.05)
                        if r2:
                            seq += os.read(fd, 1)
                            r3, _, _ = select.select([fd], [], [], 0.03)
                            if r3:
                                seq += os.read(fd, 4)
                except Exception:
                    pass
                if seq == b"[A":
                    if _READLINE_AVAILABLE:
                        import readline
                        hl = readline.get_current_history_length()
                        if hist_idx == -1:
                            saved_buf = list(buf)
                            hist_idx = hl
                        if hist_idx > 1:
                            hist_idx -= 1
                            entry = readline.get_history_item(hist_idx)
                            if entry:
                                buf = list(entry)
                elif seq == b"[B":
                    if _READLINE_AVAILABLE:
                        import readline
                        hl = readline.get_current_history_length()
                        if hist_idx != -1:
                            hist_idx += 1
                            if hist_idx > hl:
                                hist_idx = -1
                                buf = list(saved_buf)
                            else:
                                entry = readline.get_history_item(hist_idx)
                                if entry:
                                    buf = list(entry)
                elif seq in (b"[C", b"[1;5C", b"[C\x00"):
                    if ghost and ghost.lower().startswith("".join(buf).lower()):
                        tail = ghost[len(buf) :]
                        nxt = tail.lstrip()
                        word = nxt.split()[0] if nxt.split() else ""
                        if "—" not in word:
                            buf += list(word)
                continue
            elif ch == b"\x15":
                buf = []
                hist_idx = -1
                continue
            elif ch == b"\x03":
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                raise KeyboardInterrupt
            elif ch == b"\x04":
                if not buf:
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                    raise EOFError
                continue
            elif ch in (b"\x7f", b"\x08"):
                if buf:
                    buf.pop()
                    hist_idx = -1
                continue
            elif ch >= b" ":
                buf.append(ch.decode("utf-8", errors="replace"))
                hist_idx = -1
                continue
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_tty)
        sys.stdout.write(SHOW)
        sys.stdout.flush()


def ist_now(fmt="%H:%M:%S"):
    import datetime as _dt
    _IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
    return _dt.datetime.now(_IST).strftime(fmt)


def market_status():
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    wd = now.weekday()
    hr = now.hour + now.minute / 60
    if wd >= 5:
        return (False, "WEEKEND", rgb(255, 140, 0))
    elif 9.0 <= hr < 9.25:
        return (True, "PRE-MARKET", rgb(255, 140, 0))
    elif 9.25 <= hr < 15.5:
        return (True, "OPEN", rgb(0, 210, 100))
    elif 15.5 <= hr < 16.0:
        return (True, "AFTER-HOURS", rgb(180, 130, 255))
    else:
        return (False, "CLOSED", rgb(255, 60, 60))


def is_crypto(t):
    return any(
        t.endswith(x)
        for x in (
            "-USD", "-USDT", "USDT", "BTC", "ETH", "BNB", "-BTC", "-EUR",
            "DOGE", "SOL", "XRP", "ADA", "DOT", "MATIC", "AVAX", "LINK",
        )
    )


from .style import rgb, RST, BOLD
