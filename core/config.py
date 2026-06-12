import os, json, argparse, textwrap
from pathlib import Path

_ARGS = None
_APP_DIR = Path.home() / ".pocket_terminal_adv"
_APP_DIR.mkdir(exist_ok=True)
_CFG_FILE = _APP_DIR / "config.json"
_HIST_FILE = _APP_DIR / "history"
_LOG_FILE = _APP_DIR / "session.log"

_DEFAULT_CFG = {
    "default_market": "india",
    "default_period": "1y",
    "theme": "bloomberg",
    "show_tips": True,
    "max_history": 500,
    "refresh_rate": 0.5,
}


def parse_args():
    global _ARGS
    p = argparse.ArgumentParser(
        prog="pocket_terminal_adv",
        description="Pocket Terminal Advanced — Professional Financial Terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Examples:
          python -m pocket_terminal_advanced
          python -m pocket_terminal_advanced --log
          python -m pocket_terminal_advanced --no-color
          python -m pocket_terminal_advanced --theme nexus
        """),
    )
    p.add_argument("--version", action="store_true", help="Print version and exit")
    p.add_argument("--no-color", action="store_true", help="Disable ANSI color output")
    p.add_argument("--log", action="store_true", help="Write session log")
    p.add_argument("--config", metavar="PATH", help="Path to JSON config file")
    p.add_argument("--theme", choices=["bloomberg", "nexus", "minimal"], help="UI theme")
    p.add_argument("--debug", action="store_true", help="Enable debug logging")
    ns, _ = p.parse_known_args()
    _ARGS = ns
    return ns


def get_args():
    global _ARGS
    if _ARGS is None:
        parse_args()
    return _ARGS


def load_config():
    cfg = dict(_DEFAULT_CFG)
    args = get_args()
    cfg_path = Path(args.config) if args.config else _CFG_FILE
    if cfg_path.exists():
        try:
            with open(cfg_path) as f:
                user = json.load(f)
            cfg.update(user)
        except Exception:
            pass
    if args.theme:
        cfg["theme"] = args.theme
    return cfg


CFG = load_config()


def save_config():
    try:
        with open(_CFG_FILE, "w") as f:
            json.dump(CFG, f, indent=2)
        return True
    except Exception:
        return False
