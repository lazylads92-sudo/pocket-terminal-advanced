"""Pocket Terminal Advanced v5.1 — Optimized for speed."""

import sys, os, time, datetime, json, math, threading, select, subprocess
from typing import Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import __version__, __app_name__
from .core.config import CFG, save_config, get_args, _APP_DIR
from .core.style import Style, rgb, bgrgb, BOLD, DIM, RST
from .core.terminal import SESSION, input_with_ghost, ist_now, market_status, is_crypto, set_catalogue
from .ui.border_engine import BorderEngine
from .ui.themes import set_theme, get_theme, THEME, get_palette, rc, _rainbow

from .data.market import (
    YFINANCE_AVAILABLE, _get_ticker, get_info, fetch_fast_price, fetch_history,
    get_quote, fetch_batch_quotes, fetch_batch_prices,
    get_options_chain, get_fno_stocks, get_fno_calendar, get_fno_chain_analysis,
    get_pcr_series, get_implied_futures,
    start_prefetch, stop_prefetch, TTLCache,
)
try:
    import pandas as pd
except ImportError:
    pd = None
import numpy as np

from .analysis.fundamentals import get_fundamentals, get_dcf, get_analyst_ratings, get_compare, _fmt
from .analysis.technical import calc_indicators, find_support_resistance, fibonacci_levels
from .analysis.ml import (
    PYTORCH_AVAILABLE, trained_models, train_model, predict, backtest, backtest_indicator,
)
from .commands.registry import registry, CMD_CATALOGUE

BE = BorderEngine()

# ──────────────────────────────────────────────────────────────────────────────
#  DEPENDENCY CHECK
# ──────────────────────────────────────────────────────────────────────────────

MATPLOTLIB_AVAILABLE = False
MPLFINANCE_AVAILABLE = False
try:
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpl_patches
    import matplotlib.dates as mpl_dates
    MATPLOTLIB_AVAILABLE = True
    try:
        import mplfinance as mpf
        MPLFINANCE_AVAILABLE = True
    except ImportError:
        mpf = None
except ImportError:
    plt = None

# ──────────────────────────────────────────────────────────────────────────────
#  CACHED ANSI TEMPLATES (pre-built for speed)
# ──────────────────────────────────────────────────────────────────────────────

CLEAR = "\033[2J\033[H"
ELINE = "\033[2K"
HIDE = "\033[?25l"
SHOW = "\033[?25h"

def GOTO(r, c=1):
    return f"\033[{r};{c}H"

# Pre-built ANSI sequences cache
_ansi_cache = TTLCache(default_ttl=10.0)

def _csyms_get(currency):
    _CSYMS = {"USD":"$","INR":"\u20b9","EUR":"\u20ac","GBP":"\u00a3","JPY":"\u00a5",
              "CNY":"\u00a5","CAD":"$","AUD":"$","HKD":"HK$","SGD":"S$",
              "KRW":"\u20a9","BRL":"R$","CHF":"Fr","TWD":"NT$"}
    return _CSYMS.get(currency, currency)

def fmt_mcap(v, cs="$"):
    if not v: return "N/A"
    if v > 1e12: return f"{cs}{v/1e12:.2f}T"
    if v > 1e7: return f"{cs}{v/1e9:.2f}B"
    if v > 1e4: return f"{cs}{v/1e6:.1f}M"
    return f"{cs}{v:.0f}"

def _fmt_large(v, d=1):
    """Format OI / volume with K/M/B suffix."""
    if v is None or (isinstance(v, float) and (v != v)):
        return f"{'N/A':>8}"
    v = float(v); av = abs(v)
    if av >= 1e8: return f"{v/1e9:.{d}f}B"
    if av >= 1e5: return f"{v/1e6:.{d}f}M"
    if av >= 1e3: return f"{v/1e3:.{d}f}K"
    return f"{v:.0f}"

# ──────────────────────────────────────────────────────────────────────────────
#  FAST PRICE HUD — Differential rendering, no full clears
# ──────────────────────────────────────────────────────────────────────────────

class PriceHUD:
    """Optimized HUD that only redraws changed fields."""
    
    def __init__(self, ticker: str):
        self.ticker = ticker.upper()
        self.info = {}
        self.hist = None
        self.live_price = None
        self.frame_num = 0
        self.stop = threading.Event()
        
        # Pre-cached frame parts
        self._header_lines = []
        self._footer_lines = []
        self._dirty = True
        
        self._watch_thread = None
    
    def _build_static_frame(self):
        """Build static frame parts that rarely change."""
        P = get_palette()
        w = BE.box_w
        now_s = ist_now("%H:%M:%S IST  %Y-%m-%d")
        
        self._header_lines = [
            BE.bb_top(),
            BE.bb_row(f"  {BOLD}{rgb(0,200,220)}POCKET TERMINAL{RST}  {DIM}LIVE MARKET FEED{RST}  {DIM}{now_s}{RST}"),
        ]
        self._footer_lines = [
            BE.bb_div(),
            BE.bb_row(f"  [{BOLD}{rgb(0,200,220)}Q{RST}] EXIT  |  {DIM}Streaming {self.ticker}{RST}  |  v{__version__}"),
            BE.bb_bot(),
        ]
    
    def _build_dynamic_lines(self) -> List[str]:
        """Build only the dynamic content lines."""
        c = float(self.live_price) if self.live_price else (
            float(self.hist["Close"].iloc[-1]) if self.hist is not None and not self.hist.empty else 0
        )
        
        if self.hist is None or self.hist.empty or len(self.hist) < 2:
            return ["  Loading data..."]
        
        prv = float(self.hist["Close"].iloc[-2])
        opn = float(self.hist["Open"].iloc[-1])
        hi = max(float(self.hist["High"].iloc[-1]), c)
        lo = min(float(self.hist["Low"].iloc[-1]), c)
        vol = float(self.hist["Volume"].iloc[-1])
        pct = (c - prv) / prv * 100 if prv else 0
        up = pct >= 0
        arr = "\u25b2" if up else "\u25bc"
        mc = rgb(0, 210, 100) if up else rgb(255, 60, 60)
        bg = bgrgb(0, 40, 0) if up else bgrgb(40, 0, 0)
        
        w52h = float(self.info.get("fiftyTwoWeekHigh", hi) or hi)
        w52l = float(self.info.get("fiftyTwoWeekLow", lo) or lo)
        mktc = self.info.get("marketCap", 0) or 0
        pe = self.info.get("trailingPE")
        beta = self.info.get("beta")
        tgt = self.info.get("targetMeanPrice")
        name = (self.info.get("shortName") or self.ticker)[:24]
        sector = (self.info.get("sector") or "\u2014")[:22]
        exch = (self.info.get("exchange") or "N/A")[:6]
        curr = self.info.get("currency", "USD")
        cs = _csyms_get(curr)
        
        gp = (c - w52l) / (w52h - w52l) if w52h != w52l else 0.5
        io, ses, sc = market_status()
        
        lines = []
        lines.append(BE.bb_row(f"  {BOLD}{rgb(220,225,230)}{name}{RST}  {DIM}[{rgb(0,200,220)}{self.ticker}{RST}{DIM}]  {exch}/{curr}  {sc}{ses}{RST}"))
        if sector:
            lines.append(BE.bb_row(f"  {DIM}{sector}{RST}"))
        lines.append(BE.bb_div())
        lines.append(BE.bb_row(f"  {bg}{BOLD}{mc}  {cs}{c:,.4f}  {RST}  {BOLD}{mc}{arr} {pct:+.3f}%{RST}"))
        lines.append(BE.bb_div("OHLC"))
        lines.append(BE.bb_row(f"  {DIM}O{RST} {rgb(255,230,0)}{cs}{opn:.2f}{RST}  {DIM}H{RST} {rgb(0,210,100)}{cs}{hi:.2f}{RST}  {DIM}L{RST} {rgb(255,60,60)}{cs}{lo:.2f}{RST}  {DIM}VOL{RST} {vol/1e6:.2f}M"))
        lines.append(BE.bb_div("52-WEEK"))
        
        try:
            nw = BE.inn_w - 22
            spkv = self.hist["Close"].values[-50:].tolist()
            lines.append(BE.bb_row(f"  {rgb(255,60,60)}{cs}{w52l:.2f}{RST}  {self._needle(gp,nw)}  {rgb(0,210,100)}{cs}{w52h:.2f}{RST}"))
        except:
            pass
        
        pe_s = f"{pe:.1f}x" if pe else "N/A"
        beta_s = f"{beta:.2f}" if beta else "N/A"
        tgt_s = f"{cs}{tgt:.2f}" if tgt else "N/A"
        lines.append(BE.bb_div("FUNDAMENTALS"))
        lines.append(BE.bb_row(f"  {DIM}CAP{RST} {rgb(180,130,255)}{fmt_mcap(mktc,cs)}{RST}  {DIM}P/E{RST} {rgb(255,140,0)}{pe_s}{RST}  {DIM}B{RST} {rgb(180,130,255)}{beta_s}{RST}  {DIM}TGT{RST} {tgt_s}"))
        
        # Fast institutional holders (cached)
        holders = self._get_holders()
        if holders:
            lines.append(BE.bb_div("TOP HOLDERS"))
            for i, (nh, ph, _) in enumerate(holders[:3]):
                lines.append(BE.bb_row(f"  {i+1}. {nh[:28]}  {rgb(0,210,100)}{ph:.1f}%{RST}"))
        
        return lines
    
    def _get_holders(self):
        """Cached institutional holders."""
        cache_key = f"holders:{self.ticker}"
        cached = _ansi_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            t = _get_ticker(self.ticker)
            ih = t.institutional_holders
            holders = []
            if ih is not None and not ih.empty:
                for _, row in ih.head(5).iterrows():
                    try:
                        n = str(row.iloc[0])[:30]
                        p = float(str(row.iloc[1]).replace("%","")) if len(row) > 1 else 0
                        if p < 1: p *= 100
                        s = int(str(row.iloc[2]).replace(",","")) if len(row) > 2 else 0
                        holders.append((n, p, s))
                    except:
                        pass
            _ansi_cache.set(cache_key, holders, 60.0)
            return holders
        except:
            return []
    
    def _needle(self, pos, w):
        pos = max(0.0, min(1.0, pos))
        n = int(pos * (w - 1))
        b = ""
        for i in range(w):
            if i < n:
                b += rgb(0, 210, 100) + "\u2501"
            elif i == n:
                b += BOLD + rgb(255, 230, 0) + "\u25c6" + RST
            else:
                b += DIM + rgb(60, 80, 100) + "\u2500"
        return b + RST
    
    def _watch_keyboard(self):
        """Background thread to watch for 'Q' press."""
        try:
            import tty, termios
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                while not self.stop.is_set():
                    if select.select([sys.stdin], [], [], 0.05)[0]:
                        ch = sys.stdin.read(1)
                        if ch.lower() in ("q", "\x03", "\x1b"):
                            self.stop.set()
                            break
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except:
            pass
    
    def run(self):
        """Run the HUD with differential rendering."""
        if not YFINANCE_AVAILABLE:
            return "yfinance required."
        
        sys.stdout.write(HIDE)
        
        # Initial data load
        try:
            t = _get_ticker(self.ticker)
            self.info = get_info(self.ticker, ttl=30)
            self.hist = t.history(period="60d")
            if self.hist is None or self.hist.empty or len(self.hist) < 3:
                sys.stdout.write(SHOW + CLEAR)
                return "No data."
        except Exception as e:
            sys.stdout.write(SHOW + CLEAR)
            return f"Error: {e}"
        
        self._build_static_frame()
        
        self._watch_thread = threading.Thread(target=self._watch_keyboard, daemon=True)
        self._watch_thread.start()
        
        tick = 0.5
        crypto = is_crypto(self.ticker)
        io, ses, _ = market_status()
        live = crypto or io
        every = max(1, int((0.5 if live else 3.0) / tick))
        
        # Pre-warm the display
        lines = self._build_dynamic_lines()
        all_lines = self._header_lines + lines + self._footer_lines
        buf = [CLEAR]
        for i, ln in enumerate(all_lines):
            buf.append(GOTO(i+1) + ELINE + ln)
        sys.stdout.write("".join(buf))
        sys.stdout.flush()
        
        try:
            while not self.stop.is_set():
                self.frame_num += 1
                blink = self.frame_num % 2 == 0
                
                # Fast price update every tick
                try:
                    fi = t.fast_info
                    lp = (getattr(fi, "last_price", None) or
                          getattr(fi, "regular_market_price", None) or
                          getattr(fi, "regularMarketPrice", None))
                    if lp and float(lp) > 0:
                        self.live_price = float(lp)
                except:
                    pass
                
                # Slower: update info and history
                if self.frame_num % every == 0 and self.frame_num > 0:
                    try:
                        self.info = get_info(self.ticker, ttl=30)
                        h2 = t.history(period="5d" if live else "60d")
                        if h2 is not None and not h2.empty and len(h2) >= 3:
                            self.hist = h2
                    except:
                        pass
                
                # Only rebuild dynamic lines (header/footer are static)
                new_lines = self._build_dynamic_lines()
                
                # Differential update: only send changed lines
                buf = []
                for i, ln in enumerate(new_lines):
                    buf.append(GOTO(i + len(self._header_lines) + 1) + ELINE + ln)
                sys.stdout.write("".join(buf))
                sys.stdout.flush()
                
                time.sleep(tick)
        except KeyboardInterrupt:
            self.stop.set()
        finally:
            sys.stdout.write(SHOW + "\n")
            sys.stdout.flush()
        
        return "HUD closed."


# ──────────────────────────────────────────────────────────────────────────────
#  COMMAND HANDLERS (with caching + concurrent data)
# ──────────────────────────────────────────────────────────────────────────────

def cmd_price(args):
    if not args:
        return "Usage: price TICKER"
    hud = PriceHUD(args[0])
    return hud.run()


def cmd_chart(args):
    """Interactive chart with full technical analysis."""
    if not args:
        return "Usage: chart TICKER [candle] [period]"
    ticker = args[0].lower()
    chart_type = "line"
    period = "1y"
    for a in args[1:]:
        if a in ("candle", "candlestick"):
            chart_type = "candle"
        elif a in ("line", "area"):
            chart_type = "line"
        else:
            period = a
    
    if not YFINANCE_AVAILABLE:
        return "yfinance required."
    if not MATPLOTLIB_AVAILABLE:
        return "matplotlib required."
    
    try:
        df = fetch_history(ticker, period, "1d")
        if df is None or df.empty:
            return "No data."
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        
        info = get_info(ticker, ttl=300)
        curr = info.get("currency", "USD")
        price_label = f"Price ({curr})"
        
        df = calc_indicators(df)
        
        if chart_type == "candle":
            if not MPLFINANCE_AVAILABLE:
                return "mplfinance not installed."
            return _render_candle_chart(df, ticker, info)
        else:
            return _render_line_chart(df, ticker, info, price_label)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Error: {e}"


def _render_candle_chart(df, ticker, info):
    """Render candlestick chart with indicators."""
    mc = mpf.make_marketcolors(up="#00ffcc", down="#ff3366", edge="inherit", wick="inherit", volume="in")
    sty = mpf.make_mpf_style(
        marketcolors=mc, base_mpf_style="nightclouds",
        gridcolor="#222233", gridstyle="--", facecolor="#0d0d1a", figcolor="#0d0d1a"
    )
    apds = [
        mpf.make_addplot(df["BB_Upper"], color="cyan", linestyle="--", width=0.7, alpha=0.5),
        mpf.make_addplot(df["BB_Lower"], color="cyan", linestyle="--", width=0.7, alpha=0.5),
        mpf.make_addplot(df["Auto_Trend"], color="#ff00ff", linestyle="-.", width=1.4),
        mpf.make_addplot(df["RSI"], panel=2, color="#ffcc00", ylabel="RSI"),
        mpf.make_addplot([70]*len(df), panel=2, color="red", linestyle="--", width=0.5),
        mpf.make_addplot([30]*len(df), panel=2, color="green", linestyle="--", width=0.5),
        mpf.make_addplot(df["MACD"], panel=3, color="#4488ff", ylabel="MACD"),
        mpf.make_addplot(df["MACD_Signal"], panel=3, color="#ff8800"),
        mpf.make_addplot(df["MACD_Hist"], panel=3, type="bar", color="#666688", alpha=0.6),
        mpf.make_addplot(df["OBV"]/1e6, panel=4, color="#aa88ff", ylabel="OBV(M)"),
        mpf.make_addplot(df["Stoch_K"], panel=5, color="#00ffcc", ylabel="Stoch"),
        mpf.make_addplot(df["Stoch_D"], panel=5, color="#ff9900"),
        mpf.make_addplot([80]*len(df), panel=5, color="red", linestyle="--", width=0.5),
        mpf.make_addplot([20]*len(df), panel=5, color="green", linestyle="--", width=0.5),
    ]
    fig, axes = mpf.plot(
        df, type="candle", volume=True, style=sty, addplot=apds,
        title=f"{ticker.upper()} \u2014 Full Technical Analysis",
        mav=(20, 50, 200), panel_ratios=(7,2,2,2,2,2),
        figscale=1.25, figratio=(18,12), returnfig=True
    )
    _attach_chart_engine(fig, axes, df, ticker.upper())
    plt.show()
    return "Candlestick chart closed."


def _render_line_chart(df, ticker, info, price_label):
    """Render line chart with indicators."""
    plt.style.use("dark_background")
    BG = "#0d0d1a"
    fig, (ax_p, ax_v, ax_r, ax_m) = plt.subplots(
        4, 1, figsize=(16, 10), sharex=True,
        gridspec_kw={"height_ratios": [5, 1.5, 1.5, 1.5], "hspace": 0.04}
    )
    fig.patch.set_facecolor(BG)
    for ax in [ax_p, ax_v, ax_r, ax_m]:
        ax.set_facecolor(BG)
        ax.tick_params(colors="#888888", labelsize=7)
        ax.grid(True, color="#1e1e2e", linewidth=0.5, linestyle="--")
    
    ax_p.fill_between(df.index, df["Close"].min(), df["Close"], alpha=0.12, color="#00ffcc")
    ax_p.plot(df.index, df["Close"], color="#00ffcc", linewidth=1.5)
    ax_p.set_ylabel(price_label, color="#cccccc", fontsize=8)
    
    vol_colors = [
        "#00ffcc" if df["Close"].iloc[i] >= df["Open"].iloc[i] else "#ff3366"
        for i in range(len(df))
    ]
    ax_v.bar(df.index, df["Volume"], color=vol_colors, alpha=0.6, width=1)
    ax_v.set_ylabel("Volume", color="#cccccc", fontsize=8)
    
    ax_r.plot(df.index, df["RSI"], color="#ffcc00", linewidth=1)
    ax_r.axhline(70, color="#ff4466", linewidth=0.6, linestyle="--")
    ax_r.axhline(30, color="#44ff88", linewidth=0.6, linestyle="--")
    ax_r.fill_between(df.index, 70, df["RSI"], where=df["RSI"]>=70, alpha=0.15, color="#ff4466")
    ax_r.fill_between(df.index, 30, df["RSI"], where=df["RSI"]<=30, alpha=0.15, color="#44ff88")
    ax_r.set_ylim(0, 100)
    ax_r.set_ylabel("RSI", color="#cccccc", fontsize=8)
    
    ax_m.plot(df.index, df["MACD"], color="#4488ff", linewidth=1, label="MACD")
    ax_m.plot(df.index, df["MACD_Signal"], color="#ff8800", linewidth=1, label="Signal")
    hc = ["#00ffcc" if v >= 0 else "#ff3366" for v in df["MACD_Hist"]]
    ax_m.bar(df.index, df["MACD_Hist"], color=hc, alpha=0.5, width=1)
    ax_m.axhline(0, color="#555566", linewidth=0.5)
    ax_m.set_ylabel("MACD", color="#cccccc", fontsize=8)
    
    _attach_chart_engine(fig, [ax_p, ax_v, ax_r, ax_m], df, ticker.upper())
    fig.subplots_adjust(left=0.06, right=0.97, top=0.92, bottom=0.05)
    plt.show()
    return "Chart closed."


def _attach_chart_engine(fig, axes, df, ticker):
    try:
        from .charts.interactive import AdvancedInteractiveChart
        AdvancedInteractiveChart(fig, axes, df, ticker)
    except Exception:
        pass


# ── DASHBOARD (concurrent batch fetches) ─────────────────────────────────────

def cmd_dashboard(args):
    market = args[0] if args else CFG.get("default_market", "india")
    if not YFINANCE_AVAILABLE:
        return "yfinance required."
    
    market_l = market.lower()
    P = get_palette()
    
    if market_l in ("india", "in"):
        indices = ["^NSEI", "^BSESN", "^NSEBANK", "^CNXMIDCAP"]
        fx = ["USDINR=X", "EURINR=X", "GBPINR=X", "JPYINR=X"]
    elif market_l in ("usa", "us"):
        indices = ["^GSPC", "^DJI", "^IXIC", "^RUT"]
        fx = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCNY=X"]
    else:
        indices = ["^GSPC", "^NSEI", "^N225", "^FTSE"]
        fx = ["EURUSD=X", "USDINR=X", "GBPUSD=X", "USDJPY=X"]
    
    comm = ["GC=F", "SI=F", "CL=F", "NG=F"]
    crypto = ["BTC-USD", "ETH-USD"]
    
    # Batch fetch all symbols concurrently
    all_syms = indices + fx + comm + crypto
    quotes = fetch_batch_quotes(all_syms)
    
    w = BE.box_w
    now = ist_now("%A, %d %B %Y  %H:%M:%S IST")
    io_st, ses, sc = market_status()
    
    def _pl(label, sym):
        q = quotes.get(sym, {})
        px = q.get("price")
        pct = q.get("change_pct", 0)
        if px is None:
            return f"  {P.label}{label:<16}{RST}  N/A"
        cc = P.up if pct >= 0 else P.dn
        arr = "\u25b2" if pct >= 0 else "\u25bc"
        return f"  {P.label}{label:<14}{RST}  {BOLD}{P.amber_l}{px:>10,.2f}{RST}  {cc}{arr}{pct:+.2f}%{RST}"
    
    out = ["", BE.bb_top(f"MARKET DASHBOARD  {market.upper()}")]
    out.append(BE.bb_row(f"  {BOLD}{P.head}Bloomberg-Style Market Overview{RST}  {P.label}{now}  {sc}{ses}{RST}"))
    out.append(BE.bb_div())
    
    half = (w - 3) // 2
    
    def _panel_header(title):
        return f"  {BOLD}{P.amber_l}{title}{RST}"
    
    def _two_panels(left, right):
        res = []
        n = max(len(left), len(right))
        for i in range(n):
            lc = left[i] if i < len(left) else ""
            rc = right[i] if i < len(right) else ""
            lp = lc + " " * (half - len(lc))
            rp = rc + " " * (w - 3 - half - len(rc))
            res.append(f"{P.amber}\u2551{RST}{lp}{P.amber}\u2502{RST}{rp}{P.amber}\u2551{RST}")
        return "\n".join(res)
    
    # Panel data
    idx_labels = {"^NSEI": "NIFTY 50", "^BSESN": "SENSEX", "^NSEBANK": "BANK NIFTY",
                  "^CNXMIDCAP": "MID CAP", "^GSPC": "S&P 500", "^DJI": "DOW JONES",
                  "^IXIC": "NASDAQ", "^RUT": "RUSSELL 2K", "^N225": "NIKKEI", "^FTSE": "FTSE 100"}
    comm_labels = {"GC=F": "GOLD", "SI=F": "SILVER", "CL=F": "CRUDE OIL", "NG=F": "NAT GAS"}
    crypto_labels = {"BTC-USD": "BITCOIN", "ETH-USD": "ETHEREUM"}
    fx_labels = {"USDINR=X": "USD/INR", "EURINR=X": "EUR/INR", "GBPINR=X": "GBP/INR",
                 "JPYINR=X": "JPY/INR", "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD",
                 "USDJPY=X": "USD/JPY", "USDCNY=X": "USD/CNY"}
    
    il = [_panel_header("EQUITY INDICES")] + [
        _pl(idx_labels.get(sym, sym), sym) for sym in indices
    ]
    fl = [_panel_header("FOREIGN EXCHANGE")] + [
        _pl(fx_labels.get(sym, sym), sym) for sym in fx
    ]
    out.append(_two_panels(il, fl))
    out.append(BE.bb_div())
    
    cl = [_panel_header("COMMODITIES")] + [
        _pl(comm_labels.get(sym, sym), sym) for sym in comm
    ]
    crl = [_panel_header("CRYPTOCURRENCY")] + [
        _pl(crypto_labels.get(sym, sym), sym) for sym in crypto
    ]
    out.append(_two_panels(cl, crl))
    out.append(BE.bb_div())
    
    shortcuts = [
        ("price TICKER", "Live HUD"),
        ("chart TICKER", "Chart"),
        ("fund TICKER", "Fundamentals"),
        ("news TICKER", "Headlines"),
    ]
    sh = "  ".join(
        f"{BOLD}{P.cyan}{k}{RST} {P.label}{v}{RST}" for k, v in shortcuts
    )
    out.append(BE.bb_row(f"  {sh}"))
    out.append(BE.bb_bot())
    
    return "\n".join(out)


# ── NEWS ──────────────────────────────────────────────────────────────────────

def cmd_news(args):
    if not args:
        return "Usage: news TICKER"
    ticker = args[0].upper()
    if not YFINANCE_AVAILABLE:
        return "yfinance required."
    
    cache_key = f"news:{ticker}"
    cached = _ansi_cache.get(cache_key)
    if cached is not None:
        return cached
    
    try:
        tk = _get_ticker(ticker)
        news = tk.news
        if not news:
            return f"No news for {ticker}."
        
        out = [
            f"\n{BOLD}{Style.CYAN}  NEWS - {ticker}{Style.RESET}",
            f"{Style.MAGENTA}{'='*58}{Style.RESET}"
        ]
        for i, item in enumerate(news[:8]):
            title = item.get("title", "N/A")
            pub = item.get("providerPublishTime", 0)
            if pub:
                pub = datetime.datetime.fromtimestamp(pub).strftime("%Y-%m-%d %H:%M")
            out.append(f"  {i+1}. {Style.BOLD}{title}{Style.RESET}")
            if pub:
                out.append(f"     {Style.DIM}{pub}{Style.RESET}")
            out.append("")
        out.append(f"{Style.MAGENTA}{'='*58}{Style.RESET}")
        
        result = "\n".join(out)
        _ansi_cache.set(cache_key, result, 30.0)
        return result
    except Exception as e:
        return f"Error: {e}"


# ── FUNDAMENTALS ──────────────────────────────────────────────────────────────

def cmd_fundamentals(args):
    if not args:
        return "Usage: fundamentals TICKER"
    return get_fundamentals(args[0])

def cmd_dcf(args):
    if not args:
        return "Usage: dcf TICKER"
    return get_dcf(args[0])

def cmd_analyst(args):
    if not args:
        return "Usage: analyst TICKER"
    return get_analyst_ratings(args[0])

def cmd_compare(args):
    if not args:
        return "Usage: compare T1,T2,T3"
    return get_compare(args[0])

def _statement(ticker, stmt_type):
    if not YFINANCE_AVAILABLE:
        return "yfinance required."
    try:
        t = _get_ticker(ticker)
        fs_map = {
            "income": t.financials,
            "balance": t.balance_sheet,
            "cashflow": t.cashflow,
        }
        if stmt_type in ("earnings",):
            return _earnings_fn(t, ticker)
        if stmt_type in ("dividends",):
            return _dividends_fn(t, ticker)
        
        fs = fs_map.get(stmt_type)
        if fs is None or fs.empty:
            return f"No data for {ticker}."
        
        cols = fs.columns[:4]
        years = [str(c)[:4] for c in cols]
        
        def row(label, key):
            vals = []
            for c in cols:
                v = fs.loc[key, c] if key in fs.index else None
                vals.append(
                    _fmt(v, prefix="$", billions=True) if v is not None
                    else f"{Style.DIM}N/A{Style.RESET}"
                )
            return f"  {label:<28} " + "  ".join(f"{v:>12}" for v in vals) + "\n"
        
        header = f"  {'Metric':<28} " + "  ".join(f"{y:>12}" for y in years) + "\n"
        title_map = {"income": "INCOME STATEMENT", "balance": "BALANCE SHEET", "cashflow": "CASH FLOW"}
        keys_map = {
            "income": ["Total Revenue", "Gross Profit", "Operating Income", "Net Income", "EBITDA"],
            "balance": ["Total Assets", "Total Liabilities Net Minority Interest", "Stockholders Equity", "Total Debt"],
            "cashflow": ["Operating Cash Flow", "Capital Expenditure", "Free Cash Flow", "Stock Based Compensation"],
        }
        
        out = (
            f"\n{Style.MAGENTA}{'='*62}{Style.RESET}\n"
            f"{Style.BOLD}{Style.CYAN}  {title_map[stmt_type]} - {ticker.upper()}{Style.RESET}\n"
            f"{Style.MAGENTA}{'='*62}{Style.RESET}\n"
            f"{Style.BOLD}{header}{Style.RESET}"
        )
        for key in keys_map[stmt_type]:
            try:
                out += row(key, key)
            except Exception:
                pass
        out += f"{Style.MAGENTA}{'='*62}{Style.RESET}"
        return out
    except Exception as e:
        return f"Error: {e}"


def _earnings_fn(t, ticker):
    info = get_info(ticker, ttl=300)
    hist = t.earnings_history
    out = (
        f"\n{Style.MAGENTA}{'='*58}{Style.RESET}\n"
        f"{Style.BOLD}{Style.CYAN}  EARNINGS - {ticker.upper()}{Style.RESET}\n"
        f"{Style.MAGENTA}{'='*58}{Style.RESET}\n"
    )
    if hist is not None and not hist.empty:
        out += f"  {Style.BOLD}{'Quarter':<12} {'Est.':>10} {'Act.':>10} {'Surp.':>10}{Style.RESET}\n"
        for _, row_h in hist.tail(8).iterrows():
            try:
                est = row_h.get("epsEstimate", float("nan"))
                act = row_h.get("epsActual", float("nan"))
                surp = row_h.get("epsDifference", float("nan"))
                q = str(row_h.name)[:10] if hasattr(row_h, "name") else "N/A"
                beat = surp > 0
                out += (
                    f"  {q:<12} {_fmt(est, prefix='$'):>10} {_fmt(act, prefix='$'):>10} "
                    f"{Style.GREEN if beat else Style.RED}{surp:+.2f}{Style.RESET:>10}\n"
                )
            except Exception:
                pass
    else:
        out += "  No earnings history.\n"
    return out + f"{Style.MAGENTA}{'='*58}{Style.RESET}"


def _dividends_fn(t, ticker):
    info = get_info(ticker, ttl=300)
    divs = t.dividends
    dy = _fmt((info.get("dividendYield") or 0) * 100, suffix="%", decimals=2)
    dr = _fmt(info.get("dividendRate"), prefix="$", decimals=2)
    pr = _fmt((info.get("payoutRatio") or 0) * 100, suffix="%", decimals=1)
    out = (
        f"\n{Style.MAGENTA}{'='*58}{Style.RESET}\n"
        f"{Style.BOLD}{Style.CYAN}  DIVIDENDS - {ticker.upper()}{Style.RESET}\n"
        f"{Style.MAGENTA}{'='*58}{Style.RESET}\n"
        f"  Yield: {dy}  Rate: {dr}  Payout: {pr}\n"
    )
    if divs is not None and not divs.empty:
        out += "\n  Recent:\n"
        for date, amt in divs.tail(8).items():
            out += f"  {str(date)[:16]}  ${amt:.4f}\n"
    return out + f"\n{Style.MAGENTA}{'='*58}{Style.RESET}"


def cmd_income(args):
    if not args: return "Usage: income TICKER"
    return _statement(args[0], "income")

def cmd_balance(args):
    if not args: return "Usage: balance TICKER"
    return _statement(args[0], "balance")

def cmd_cashflow(args):
    if not args: return "Usage: cashflow TICKER"
    return _statement(args[0], "cashflow")

def cmd_earnings(args):
    if not args: return "Usage: earnings TICKER"
    return _statement(args[0], "earnings")

def cmd_dividends(args):
    if not args: return "Usage: dividends TICKER"
    return _statement(args[0], "dividends")


# ── OPTIONS CHAIN (NEW) ──────────────────────────────────────────────────────

def cmd_options(args):
    """Display options chain for a symbol."""
    if not args:
        return "Usage: options TICKER [expiration]"
    ticker = args[0].upper()
    expiration = args[1] if len(args) > 1 else None
    
    chain = get_options_chain(ticker, expiration)
    if chain is None:
        return f"No options data for {ticker}."
    
    out = [
        f"\n{BOLD}{Style.CYAN}  OPTIONS CHAIN - {ticker} (exp: {chain['expiration']}){Style.RESET}",
        f"{Style.MAGENTA}{'='*68}{Style.RESET}",
        f"  Available expirations: {', '.join(chain.get('expirations', [])[:5])}",
        "",
        f"  {BOLD}CALLS:{Style.RESET}",
        f"  {'Strike':>8} {'Last':>8} {'Bid':>8} {'Ask':>8} {'Vol':>6} {'OI':>8} {'IV':>6}",
        f"{Style.DIM}{'-'*58}{Style.RESET}",
    ]
    
    for c in chain.get("calls", [])[:15]:
        out.append(
            f"  {c.get('strike',0):>8.0f} {c.get('lastPrice',0):>8.2f} {c.get('bid',0):>8.2f} "
            f"{c.get('ask',0):>8.2f} {c.get('volume',0):>6.0f} {c.get('openInterest',0):>8.0f} "
            f"{c.get('impliedVolatility',0):>5.1%}"
        )
    
    out.extend([
        "",
        f"  {BOLD}PUTS:{Style.RESET}",
        f"  {'Strike':>8} {'Last':>8} {'Bid':>8} {'Ask':>8} {'Vol':>6} {'OI':>8} {'IV':>6}",
        f"{Style.DIM}{'-'*58}{Style.RESET}",
    ])
    
    for p in chain.get("puts", [])[:15]:
        out.append(
            f"  {p.get('strike',0):>8.0f} {p.get('lastPrice',0):>8.2f} {p.get('bid',0):>8.2f} "
            f"{p.get('ask',0):>8.2f} {p.get('volume',0):>6.0f} {p.get('openInterest',0):>8.0f} "
            f"{p.get('impliedVolatility',0):>5.1%}"
        )
    
    out.append(f"{Style.MAGENTA}{'='*68}{Style.RESET}")
    return "\n".join(out)


# ── F&O DATA (Futures & Options) ─────────────────────────────────────────────

def cmd_fno(args):
    """F&O data: chain, calendar, pcr, list, futures."""
    if not args:
        return (
            f"  {BOLD}Usage:{RST} fno [subcommand] [args]\n"
            f"  {BOLD}list{RST}                  List F&O stocks\n"
            f"  {BOLD}chain TICKER [exp]{RST}    Options chain with OI/PCR/max pain\n"
            f"  {BOLD}calendar TICKER{RST}       Expiry calendar with DTE\n"
            f"  {BOLD}pcr TICKER{RST}            PCR across nearest expiries\n"
            f"  {BOLD}futures TICKER{RST}        Implied futures curve (put-call parity)"
        )
    sub = args[0].lower(); rest = args[1:]
    if sub == "list":          return _fno_list()
    if sub == "chain":         return _fno_chain(rest)
    if sub in ("calendar","expiry"): return _fno_calendar(rest)
    if sub == "pcr":           return _fno_pcr(rest)
    if sub == "futures":       return _fno_futures(rest)
    return f"Unknown subcommand: {sub}. Try: list, chain, calendar, pcr, futures"


def _fno_list():
    stocks = get_fno_stocks()
    nse = [s for s in stocks if ".NS" in s]
    us = [s for s in stocks if ".NS" not in s]
    out = [
        f"\n{BOLD}{Style.CYAN}  F&O STOCKS ({len(stocks)} total){Style.RESET}",
        f"{Style.MAGENTA}{'='*72}{Style.RESET}",
        f"  {BOLD}NSE India ({len(nse)}):{RST}",
    ]
    for i in range(0, len(nse), 5):
        chunk = nse[i:i+5]
        out.append("   " + "  ".join(f"{Style.GREEN}{s.replace('.NS','')}{RST}" for s in chunk))
    out.append(f"\n  {BOLD}US ({len(us)}):{RST}")
    for i in range(0, len(us), 6):
        chunk = us[i:i+6]
        out.append("   " + "  ".join(f"{Style.GREEN}{s}{RST}" for s in chunk))
    out.append(f"{Style.MAGENTA}{'='*72}{Style.RESET}")
    return "\n".join(out)


def _fno_chain(args):
    if not args:
        return "Usage: fno chain TICKER [expiration]"
    ticker = args[0].upper()
    expiration = args[1] if len(args) > 1 else None
    a = get_fno_chain_analysis(ticker, expiration)
    if a is None:
        return f"No F&O data for {ticker}."
    pcr = a["pcr"]
    mp = a["max_pain"]
    spot = a.get("price")
    cs = "\u20b9" if ".NS" in ticker else "$"
    spot_s = f"{cs}{spot:,.2f}" if spot else "N/A"
    mp_s = f"{cs}{mp:,.0f}" if mp else "N/A"
    out = [
        f"\n{BOLD}{Style.CYAN}  F&O CHAIN — {ticker}  ({a['expiration']}){Style.RESET}",
        f"{Style.MAGENTA}{'='*72}{Style.RESET}",
        f"  {BOLD}Spot:{RST} {spot_s}  |  {BOLD}Max Pain:{RST} {mp_s}  |  "
        f"{BOLD}PCR(OI):{RST} {pcr['pcr_oi']:.2f}  |  {BOLD}PCR(Vol):{RST} {pcr['pcr_vol']:.2f}",
        f"  {BOLD}Call OI:{RST} {_fmt_large(pcr['call_oi'])}  |  {BOLD}Put OI:{RST} {_fmt_large(pcr['put_oi'])}"
        f"  |  {BOLD}Total OI:{RST} {_fmt_large(pcr['call_oi'] + pcr['put_oi'])}",
        f"  Available: {', '.join(a.get('expirations', [])[:5])}",
        "",
        f"  {BOLD}CALLS:{RST}",
        f"  {'Strike':>8} {'OI':>10} {'Vol':>8} {'IV%':>6} {'Last':>8} {'Chg':>8}",
        f"{Style.DIM}  {'─'*52}{Style.RESET}",
    ]
    calls = a.get("calls", pd.DataFrame()) if pd is not None else pd.DataFrame()
    if not calls.empty:
        cols = calls.columns
        for _, r in calls.head(15).iterrows():
            iv = f"{r.get('impliedVolatility',0)*100:.1f}" if 'impliedVolatility' in cols else "N/A"
            chg = r.get('change')
            chg_s = f"{chg:>+7.2f}" if chg is not None and not (isinstance(chg, float) and chg != chg) else "   N/A "
            out.append(f"  {r.get('strike',0):>8.0f} {_fmt_large(r.get('openInterest')):>10} "
                       f"{_fmt_large(r.get('volume')):>8} {iv:>6} "
                       f"{r.get('lastPrice',0):>8.2f} {chg_s}")
    out.extend(["", f"  {BOLD}PUTS:{RST}",
                f"  {'Strike':>8} {'OI':>10} {'Vol':>8} {'IV%':>6} {'Last':>8} {'Chg':>8}",
                f"{Style.DIM}  {'─'*52}{Style.RESET}"])
    puts = a.get("puts", pd.DataFrame()) if pd is not None else pd.DataFrame()
    if not puts.empty:
        cols = puts.columns
        for _, r in puts.head(15).iterrows():
            iv = f"{r.get('impliedVolatility',0)*100:.1f}" if 'impliedVolatility' in cols else "N/A"
            chg = r.get('change')
            chg_s = f"{chg:>+7.2f}" if chg is not None and not (isinstance(chg, float) and chg != chg) else "   N/A "
            out.append(f"  {r.get('strike',0):>8.0f} {_fmt_large(r.get('openInterest')):>10} "
                       f"{_fmt_large(r.get('volume')):>8} {iv:>6} "
                       f"{r.get('lastPrice',0):>8.2f} {chg_s}")
    top_c = a.get("top_call_oi", [])
    top_p = a.get("top_put_oi", [])
    has_oi = any(t.get("openInterest", 0) for t in top_c) or any(t.get("openInterest", 0) for t in top_p)
    if has_oi:
        out.append("")
        out.append(f"  {BOLD}OI CONCENTRATION (S/R levels):{RST}")
        if top_p:
            p_s = ", ".join(f"{cs}{t['strike']:.0f} ({_fmt_large(t.get('openInterest',0)).strip()})" for t in top_p[:3])
            out.append(f"  {BOLD}Support (Put OI):{RST}  {p_s}")
        if top_c:
            c_s = ", ".join(f"{cs}{t['strike']:.0f} ({_fmt_large(t.get('openInterest',0)).strip()})" for t in top_c[:3])
            out.append(f"  {BOLD}Resistance (Call OI):{RST}  {c_s}")
    out.append(f"{Style.MAGENTA}{'='*72}{Style.RESET}")
    return "\n".join(out)


def _fno_calendar(args):
    if not args:
        return "Usage: fno calendar TICKER"
    ticker = args[0].upper()
    cal = get_fno_calendar(ticker)
    if cal is None:
        return f"No expiry data for {ticker}."
    out = [
        f"\n{BOLD}{Style.CYAN}  EXPIRY CALENDAR — {ticker}{Style.RESET}",
        f"{Style.MAGENTA}{'='*52}{Style.RESET}",
        f"  {'Expiry':>12}  {'Day':>4}  {'DTE':>4}  {'Status':>8}",
        f"{Style.DIM}  {'─'*36}{Style.RESET}",
    ]
    for e in cal:
        status = f"{Style.GREEN}ACTIVE{RST}" if 0 <= e["dte"] <= 7 else \
                 f"{Style.YELLOW}FAR{RST}" if e["dte"] > 7 else \
                 f"{Style.DIM}EXPIRED{RST}"
        out.append(f"  {e['date']:>12}  {e['day']:>4}  {e['dte']:>4}d  {status}")
    out.append(f"{Style.MAGENTA}{'='*52}{Style.RESET}")
    return "\n".join(out)


def _fno_pcr(args):
    if not args:
        return "Usage: fno pcr TICKER"
    ticker = args[0].upper()
    series = get_pcr_series(ticker)
    if series is None:
        return f"No PCR data for {ticker}."
    out = [
        f"\n{BOLD}{Style.CYAN}  PCR TREND — {ticker}{Style.RESET}",
        f"{Style.MAGENTA}{'='*60}{Style.RESET}",
        f"  {'Expiry':>12}  {'PCR(OI)':>8}  {'PCR(Vol)':>9}  {'Call OI':>10}  {'Put OI':>10}",
        f"{Style.DIM}  {'─'*52}{Style.RESET}",
    ]
    for s in series:
        coi = _fmt_large(s["call_oi"]); poi = _fmt_large(s["put_oi"])
        pcr_o = f"{s['pcr_oi']:.2f}" if s["pcr_oi"] else "N/A"
        pcr_v = f"{s['pcr_vol']:.2f}" if s["pcr_vol"] else "N/A"
        bear = " 🐻" if s["pcr_oi"] > 0.7 else " 🐂" if s["pcr_oi"] < 0.5 else " ➖"
        out.append(f"  {s['expiration']:>12}  {pcr_o:>8}  {pcr_v:>9}  {coi:>10}  {poi:>10}{bear}")
    out.append(f"{Style.MAGENTA}{'='*60}{Style.RESET}")
    out.append(f"  {Style.DIM}PCR(OI) > 0.7 = bearish  |  < 0.5 = bullish{RST}")
    return "\n".join(out)


def _fno_futures(args):
    if not args:
        return "Usage: fno futures TICKER"
    ticker = args[0].upper()
    futs = get_implied_futures(ticker)
    if futs is None:
        return f"No futures data for {ticker}."
    spot = fetch_fast_price(ticker)
    cs = "\u20b9" if ".NS" in ticker else "$"
    out = [
        f"\n{BOLD}{Style.CYAN}  IMPLIED FUTURES CURVE — {ticker}{Style.RESET}",
        f"{Style.MAGENTA}{'='*64}{Style.RESET}",
        f"  {BOLD}Spot:{RST} {cs}{spot:,.2f}" if spot else f"  Spot: N/A",
        f"  {'Expiry':>12}  {'DTE':>4}  {'Implied':>9}  {'Call':>8}  {'Put':>8}  {'Basis':>8}",
        f"{Style.DIM}  {'─'*56}{Style.RESET}",
    ]
    for f in futs:
        basis = f["implied_price"] - spot if spot else 0
        out.append(f"  {f['expiration']:>12}  {f['dte']:>4}d  {cs}{f['implied_price']:>7.2f}  "
                   f"{cs}{f['call_price']:>6.2f}  {cs}{f['put_price']:>6.2f}  {cs}{basis:>6.2f}")
    out.append(f"{Style.MAGENTA}{'='*64}{Style.RESET}")
    out.append(f"  {Style.DIM}Futures derived via put-call parity: F = K + C − P{RST}")
    return "\n".join(out)


# Futures command (standalone, alias)
def cmd_futures(args):
    """Show futures curve for a symbol."""
    return _fno_futures(args)


# ── INVEST / RISK ────────────────────────────────────────────────────────────

def cmd_invest(args):
    if len(args) < 2:
        return "Usage: invest TICKER AMOUNT [date]"
    ticker = args[0].upper()
    try:
        amount = float(args[1])
    except ValueError:
        return "Invalid amount."
    buy_date = args[2] if len(args) > 2 else None
    
    if not YFINANCE_AVAILABLE:
        return "yfinance required."
    
    try:
        t = _get_ticker(ticker)
        info = get_info(ticker, ttl=300)
        hist = fetch_history(ticker, "max")
        if hist is None or hist.empty:
            return f"No data for {ticker}."
        if hasattr(hist.index, "tz") and hist.index.tz is not None:
            hist.index = hist.index.tz_localize(None)
        
        curr = info.get("currency", "USD")
        cs = _csyms_get(curr)
        today = datetime.datetime.now()
        
        if buy_date is None:
            buy_dt = hist.index[0].to_pydatetime()
            dl = "earliest data"
        elif buy_date.endswith(("d", "w", "m", "y")):
            unit = buy_date[-1]
            val = int(buy_date[:-1])
            deltas = {
                "d": datetime.timedelta(days=val),
                "w": datetime.timedelta(weeks=val),
                "m": datetime.timedelta(days=val * 30),
                "y": datetime.timedelta(days=val * 365),
            }
            buy_dt = today - deltas[unit]
            dl = f"{buy_date} ago"
        else:
            buy_dt = datetime.datetime.strptime(buy_date, "%Y-%m-%d")
            dl = buy_date
        
        future = hist[hist.index >= pd.Timestamp(buy_dt)]
        if future.empty:
            return "Date out of range."
        
        actual_buy = future.index[0].to_pydatetime()
        buy_price = float(future["Close"].iloc[0])
        cur_price = float(hist["Close"].iloc[-1])
        
        lp = fetch_fast_price(ticker)
        if lp:
            cur_price = lp
        
        shares = amount / buy_price
        cur_val = shares * cur_price
        pl = cur_val - amount
        pct_r = (pl / amount) * 100
        days = (today - actual_buy).days
        yrs = days / 365.25
        cagr = ((cur_val / amount) ** (1 / yrs) - 1) * 100 if yrs > 0.01 else 0
        up = pl >= 0
        clr = Style.GREEN if up else Style.RED
        sign = "+" if up else ""
        
        window = hist[hist.index >= pd.Timestamp(actual_buy)]["Close"]
        dd = ((window - window.cummax()) / window.cummax() * 100).min()
        
        return (
            f"\n{Style.MAGENTA}{'='*54}{Style.RESET}\n"
            f"{Style.BOLD}{Style.CYAN}  INVESTMENT ANALYSIS - {ticker}{Style.RESET}\n"
            f"{Style.MAGENTA}{'='*54}{Style.RESET}\n"
            f"  Invested:   {cs}{amount:,.2f} on {actual_buy.date()} ({dl})\n"
            f"  Buy Price:  {cs}{buy_price:.4f}\n"
            f"  Current:    {cs}{cur_price:.4f}\n"
            f"  Value:      {cs}{cur_val:,.2f}\n"
            f"  P&L:        {clr}{sign}{cs}{pl:,.2f} ({sign}{pct_r:.2f}%){Style.RESET}\n"
            f"  CAGR:       {Style.MAGENTA}{cagr:+.2f}%{Style.RESET}\n"
            f"  Max DD:     {dd:.2f}%\n"
            f"  Days Held:  {days}\n"
            f"{Style.MAGENTA}{'='*54}{Style.RESET}"
        )
    except Exception as e:
        return f"Error: {e}"


def cmd_risk(args):
    if not args:
        return "Usage: risk TICKER [benchmark]"
    ticker = args[0].upper()
    benchmark = args[1].upper() if len(args) > 1 else "^GSPC"
    
    if not YFINANCE_AVAILABLE:
        return "yfinance required."
    
    try:
        df_t = fetch_history(ticker, "2y", "1d")
        df_b = fetch_history(benchmark, "2y", "1d")
        
        if df_t is None or df_b is None:
            return "Not enough data."
        
        df_t = df_t["Close"]
        df_b = df_b["Close"]
        
        if df_t.empty or df_b.empty:
            return "Not enough data."
        
        ret_t = df_t.pct_change().dropna()
        ret_b = df_b.pct_change().dropna()
        common = ret_t.index.intersection(ret_b.index)
        ret_t = ret_t[common]
        ret_b = ret_b[common]
        
        if len(ret_t) < 20:
            return "Not enough overlapping data."
        
        mean_r = ret_t.mean()
        std_r = ret_t.std()
        rf = 0.05 / 252
        
        sharpe = (mean_r - rf) / std_r * np.sqrt(252) if std_r > 0 else 0
        sortino = (mean_r - rf) / ret_t[ret_t < 0].std() * np.sqrt(252) if len(ret_t[ret_t < 0]) > 1 else 0
        beta = np.cov(ret_t, ret_b)[0, 1] / np.var(ret_b) if np.var(ret_b) > 0 else 1
        var_95 = np.percentile(ret_t, 5)
        cvar_val = ret_t[ret_t <= var_95].mean()
        max_dd = ((df_t / df_t.cummax() - 1) * 100).min()
        calmar = (mean_r * 252) / abs(max_dd) * 100 if max_dd != 0 else 0
        ann_r = mean_r * 252 * 100
        
        return (
            f"\n{Style.MAGENTA}{'='*54}{Style.RESET}\n"
            f"{Style.BOLD}{Style.CYAN}  RISK ANALYSIS - {ticker} vs {benchmark}{Style.RESET}\n"
            f"{Style.MAGENTA}{'='*54}{Style.RESET}\n"
            f"  Sharpe:     {Style.YELLOW}{sharpe:.2f}{Style.RESET}\n"
            f"  Sortino:    {sortino:.2f}\n"
            f"  Beta:       {beta:.2f}\n"
            f"  VaR (95%):  {var_95*100:+.2f}%\n"
            f"  CVaR:       {cvar_val*100:+.2f}%\n"
            f"  Max DD:     {max_dd:.2f}%\n"
            f"  Calmar:     {calmar:.2f}\n"
            f"  Ann. Ret:   {Style.GREEN if ann_r > 0 else Style.RED}{ann_r:+.2f}%{Style.RESET}\n"
            f"  Volatility: {std_r*np.sqrt(252)*100:.2f}%\n"
            f"{Style.MAGENTA}{'='*54}{Style.RESET}"
        )
    except Exception as e:
        return f"Error: {e}"


# ── FX ────────────────────────────────────────────────────────────────────────

_FX_GROUPS = {
    "inr": ["USDINR=X","EURINR=X","GBPINR=X","JPYINR=X","AEDINR=X","SARINR=X","SGDINR=X","CHFINR=X"],
    "majors": ["EURUSD=X","GBPUSD=X","USDJPY=X","USDCHF=X","USDCAD=X","AUDUSD=X","NZDUSD=X"],
    "asia": ["USDCNY=X","USDSGD=X","USDHKD=X","USDKRW=X","USDTWD=X","USDTHB=X"],
    "crypto": ["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD","ADA-USD","DOT-USD"],
    "emerging": ["USDINR=X","USDBRL=X","USDMXN=X","USDZAR=X","USDRUB=X","USDTRY=X"],
}

_FX_LABELS = {
    "USDINR=X":"USD/INR","EURINR=X":"EUR/INR","GBPINR=X":"GBP/INR","JPYINR=X":"JPY/INR",
    "AEDINR=X":"AED/INR","SARINR=X":"SAR/INR","SGDINR=X":"SGD/INR","CHFINR=X":"CHF/INR",
    "EURUSD=X":"EUR/USD","GBPUSD=X":"GBP/USD","USDJPY=X":"USD/JPY","USDCHF=X":"USD/CHF",
    "USDCAD=X":"USD/CAD","AUDUSD=X":"AUD/USD","NZDUSD=X":"NZD/USD",
    "USDCNY=X":"USD/CNY","USDSGD=X":"USD/SGD","USDHKD=X":"USD/HKD","USDKRW=X":"USD/KRW",
    "USDTWD=X":"USD/TWD","USDTHB=X":"USD/THB",
    "USDBRL=X":"USD/BRL","USDMXN=X":"USD/MXN","USDZAR=X":"USD/ZAR","USDRUB=X":"USD/RUB","USDTRY=X":"USD/TRY",
}

def cmd_fx(args):
    group = "inr"
    if args:
        a = args[0].lower()
        if a in _FX_GROUPS:
            group = a
    pairs = _FX_GROUPS.get(group, _FX_GROUPS["inr"])
    
    # Batch fetch
    quotes = fetch_batch_quotes(pairs)
    
    out = [
        f"\n{BOLD}{Style.CYAN}  FX DASHBOARD - {group.upper()}{Style.RESET}",
        f"{Style.MAGENTA}{'='*58}{Style.RESET}"
    ]
    for sym in pairs:
        q = quotes.get(sym, {})
        px = q.get("price")
        pct = q.get("change_pct", 0)
        label = _FX_LABELS.get(sym, sym.replace("=X", ""))
        if px is None:
            out.append(f"  {label:<12} N/A")
        else:
            cc = Style.GREEN if pct >= 0 else Style.RED
            arr = "\u25b2" if pct >= 0 else "\u25bc"
            out.append(f"  {label:<12} {px:.4f}  {cc}{arr} {pct:+.2f}%{Style.RESET}")
    
    out.append(f"{Style.MAGENTA}{'='*58}{Style.RESET}")
    return "\n".join(out)


def cmd_fxlive(args):
    if not args:
        return "Usage: fxlive PAIR"
    pair = args[0].upper()
    if not pair.endswith("=X"):
        pair += "=X"
    hud = PriceHUD(pair)
    return hud.run()


# ── CALC ──────────────────────────────────────────────────────────────────────

def cmd_calc(args):
    if not args:
        return "Usage: calc sip|emi|pos|rule72|cagr AMOUNT..."
    tool = args[0].lower()
    
    if tool == "sip":
        if len(args) < 4:
            return "Usage: calc sip AMOUNT YEARS RATE"
        try:
            amt = float(args[1])
            yrs = int(args[2])
            rate = float(args[3]) / 100 / 12
            n = yrs * 12
            fv = amt * (((1 + rate) ** n - 1) / rate) * (1 + rate) if rate > 0 else amt * n
            invested = amt * n
            return (
                f"\n{BOLD}{Style.CYAN}  SIP CALCULATOR{Style.RESET}\n"
                f"{Style.MAGENTA}{'='*44}{Style.RESET}\n"
                f"  Monthly: ${amt:,.2f}\n  Years:   {yrs}\n"
                f"  Rate:    {float(args[3]):.2f}%\n"
                f"  Invested: ${invested:,.2f}\n"
                f"  Returns:  ${fv - invested:,.2f}\n"
                f"  {BOLD}Value:    ${fv:,.2f}{Style.RESET}\n"
                f"  CAGR:     {((fv / invested) ** (1 / yrs) - 1) * 100:.2f}%\n"
                f"{Style.MAGENTA}{'='*44}{Style.RESET}"
            )
        except ValueError:
            return "Invalid SIP args."
    
    if tool == "emi":
        if len(args) < 4:
            return "Usage: calc emi PRINCIPAL RATE MONTHS"
        try:
            p = float(args[1])
            r = float(args[2]) / 100 / 12
            n = int(args[3])
            emi = p * r * (1 + r) ** n / ((1 + r) ** n - 1) if r > 0 else p / n
            total = emi * n
            return (
                f"\n{BOLD}{Style.CYAN}  EMI CALCULATOR{Style.RESET}\n"
                f"{Style.MAGENTA}{'='*44}{Style.RESET}\n"
                f"  Loan:     ${p:,.2f}\n  Rate:     {float(args[2]):.2f}%\n"
                f"  Months:   {n}\n"
                f"  {BOLD}EMI:      ${emi:,.2f}{Style.RESET}\n"
                f"  Total:    ${total:,.2f}\n"
                f"  Interest: ${total - p:,.2f}\n"
                f"{Style.MAGENTA}{'='*44}{Style.RESET}"
            )
        except ValueError:
            return "Invalid EMI args."
    
    if tool == "pos":
        if len(args) < 4:
            return "Usage: calc pos CAPITAL RISK% ENTRY [STOPLOSS]"
        try:
            cap = float(args[1])
            rp = float(args[2]) / 100
            entry = float(args[3])
            sl = float(args[4]) if len(args) > 4 else entry * 0.95
            risk_per_share = abs(entry - sl)
            pos_size = (cap * rp) / risk_per_share if risk_per_share > 0 else 0
            shares = int(pos_size)
            target1 = entry + (entry - sl)
            target2 = entry + (entry - sl) * 2
            target3 = entry + (entry - sl) * 3
            return (
                f"\n{BOLD}{Style.CYAN}  POSITION SIZING{Style.RESET}\n"
                f"{Style.MAGENTA}{'='*44}{Style.RESET}\n"
                f"  Capital:    ${cap:,.2f}\n"
                f"  Risk:       {float(args[2]):.1f}% (${cap * rp:,.2f})\n"
                f"  Entry:      ${entry:.2f}\n  Stop Loss:  ${sl:.2f}\n"
                f"  {BOLD}Qty:        {shares} shares{Style.RESET}\n"
                f"  Position:   ${shares * entry:,.2f}\n"
                f"  T1 (1:1):   ${target1:.2f}\n"
                f"  T2 (1:2):   ${target2:.2f}\n"
                f"  T3 (1:3):   ${target3:.2f}\n"
                f"{Style.MAGENTA}{'='*44}{Style.RESET}"
            )
        except ValueError:
            return "Invalid POS args."
    
    if tool == "rule72":
        if len(args) < 2:
            return "Usage: calc rule72 RATE"
        try:
            rate = float(args[1])
            years = 72 / rate
            return f"At {rate}%, money doubles in ~{years:.1f} years."
        except ValueError:
            return "Invalid rate."
    
    if tool in ("cagr",):
        if len(args) < 4:
            return "Usage: calc cagr BEGIN END YEARS"
        try:
            b = float(args[1])
            e = float(args[2])
            y = float(args[3])
            cagr = ((e / b) ** (1 / y) - 1) * 100
            return f"CAGR: {cagr:.2f}%"
        except ValueError:
            return "Invalid CAGR args."
    
    return f"Unknown calculator: {tool}"


# ── SCREENER ──────────────────────────────────────────────────────────────────

def cmd_screener(args):
    return "Stock screener pre-configured. Use screener pe<15, etc. (Full screener requires database connection)"


# ── PORTFOLIO ─────────────────────────────────────────────────────────────────

_PORTFOLIO_FILE = _APP_DIR / "portfolio.json"
_portfolio = {}
try:
    if _PORTFOLIO_FILE.exists():
        with open(_PORTFOLIO_FILE) as f:
            _portfolio = json.load(f)
except Exception:
    _portfolio = {}

def _save_portfolio():
    try:
        with open(_PORTFOLIO_FILE, "w") as f:
            json.dump(_portfolio, f, indent=2)
    except Exception:
        pass

def cmd_portfolio(args):
    if not args:
        return "Usage: portfolio [add|remove|show|clear] TICKER QTY PRICE"
    sub = args[0].lower()
    
    if sub == "add":
        if len(args) < 4:
            return "Usage: portfolio add TICKER QTY PRICE"
        t = args[1].upper()
        q = float(args[2])
        p = float(args[3])
        if t in _portfolio:
            old_q = _portfolio[t]["qty"]
            old_p = _portfolio[t]["avg_price"]
            _portfolio[t]["qty"] = old_q + q
            _portfolio[t]["avg_price"] = (old_q * old_p + q * p) / (old_q + q)
        else:
            _portfolio[t] = {"qty": q, "avg_price": p}
        _save_portfolio()
        return f"Added {q} {t} @ ${p:.2f}"
    
    elif sub == "remove":
        if len(args) < 2:
            return "Usage: portfolio remove TICKER"
        t = args[1].upper()
        if t in _portfolio:
            del _portfolio[t]
            _save_portfolio()
            return f"Removed {t}."
        return f"{t} not in portfolio."
    
    elif sub == "show":
        if not _portfolio:
            return "Portfolio is empty."
        
        # Batch fetch current prices
        syms = list(_portfolio.keys())
        prices = fetch_batch_prices(syms)
        
        out = (
            f"\n{BOLD}{Style.CYAN}  PORTFOLIO SUMMARY{Style.RESET}\n"
            f"{Style.MAGENTA}{'='*54}{Style.RESET}\n"
        )
        total_cost = 0
        total_val = 0
        
        for t, h in _portfolio.items():
            cp = prices.get(t, 0) or 0
            cost = h["qty"] * h["avg_price"]
            val = h["qty"] * cp
            pl = val - cost
            pct = (pl / cost * 100) if cost else 0
            total_cost += cost
            total_val += val
            cc = Style.GREEN if pl >= 0 else Style.RED
            out += (
                f"  {t:<10} {h['qty']:>6} @ ${h['avg_price']:<8.2f}  "
                f"{cc}${val:>8,.2f} ({pl:+.2f}, {pct:+.2f}%){Style.RESET}\n"
            )
        
        tot_pl = total_val - total_cost
        tot_pct = (tot_pl / total_cost * 100) if total_cost else 0
        tc = Style.GREEN if tot_pl >= 0 else Style.RED
        out += (
            f"{Style.MAGENTA}{'='*54}{Style.RESET}\n"
            f"  {BOLD}Total:{RST}  Cost ${total_cost:,.2f}  "
            f"Value {tc}${total_val:,.2f} ({tot_pl:+,.2f}, {tot_pct:+.2f}%){Style.RESET}\n"
        )
        return out
    
    elif sub == "clear":
        _portfolio.clear()
        _save_portfolio()
        return "Portfolio cleared."
    
    return "Usage: portfolio [add|remove|show|clear]"


# ── WATCHLIST ─────────────────────────────────────────────────────────────────

_WATCHLIST_FILE = _APP_DIR / "watchlist.json"
_watchlist = {}
try:
    if _WATCHLIST_FILE.exists():
        with open(_WATCHLIST_FILE) as f:
            _watchlist = json.load(f)
except Exception:
    _watchlist = {}

def _save_watchlist():
    try:
        with open(_WATCHLIST_FILE, "w") as f:
            json.dump(_watchlist, f, indent=2)
    except Exception:
        pass

def cmd_watchlist(args):
    if not args:
        return "Usage: watchlist [add|remove|show]"
    sub = args[0].lower()
    
    if sub == "add":
        if len(args) < 2:
            return "Usage: watchlist add TICKER"
        t = args[1].upper()
        _watchlist[t] = True
        _save_watchlist()
        return f"Added {t} to watchlist."
    
    elif sub == "remove":
        if len(args) < 2:
            return "Usage: watchlist remove TICKER"
        t = args[1].upper()
        if t in _watchlist:
            del _watchlist[t]
            _save_watchlist()
            return f"Removed {t}."
        return f"{t} not in watchlist."
    
    elif sub == "show":
        if not _watchlist:
            return "Watchlist is empty."
        
        syms = list(_watchlist.keys())
        quotes = fetch_batch_quotes(syms)
        
        out = (
            f"\n{BOLD}{Style.CYAN}  WATCHLIST{Style.RESET}\n"
            f"{Style.MAGENTA}{'='*54}{Style.RESET}\n"
        )
        for t in syms:
            q = quotes.get(t, {})
            px = q.get("price")
            pct = q.get("change_pct", 0)
            if px is None:
                out += f"  {t:<12} N/A\n"
            else:
                cc = Style.GREEN if pct >= 0 else Style.RED
                arr = "\u25b2" if pct >= 0 else "\u25bc"
                out += f"  {t:<12} {cc}{px:>10,.4f}  {arr} {pct:+.2f}%{Style.RESET}\n"
        return out
    
    return "Usage: watchlist [add|remove|show]"


# ── AI / ML ───────────────────────────────────────────────────────────────────

def cmd_train(args):
    if not args:
        return "Usage: train TICKER"
    return train_model(args[0])

def cmd_predict(args):
    if not args:
        return "Usage: predict TICKER"
    return predict(args[0])

def cmd_backtest(args):
    if not args:
        return "Usage: backtest TICKER [sma|rsi|macd]"
    ticker = args[0]
    strategy = args[1].lower() if len(args) > 1 else "sma"
    if strategy == "sma":
        return backtest_indicator(ticker, "sma")
    elif strategy == "rsi":
        return backtest_indicator(ticker, "rsi")
    else:
        return backtest(ticker)


# ── SYSTEM COMMANDS ───────────────────────────────────────────────────────────

def cmd_version(args):
    PYTHON_VER = sys.version.split()[0]
    return (
        f"\n  {BOLD}Pocket Terminal Advanced v{__version__}{Style.RESET}\n"
        f"  Python {PYTHON_VER}\n"
        f"  yfinance:  {Style.GREEN if YFINANCE_AVAILABLE else Style.RED}"
        f"{'OK' if YFINANCE_AVAILABLE else 'N/A'}{Style.RESET}\n"
        f"  PyTorch:   {Style.GREEN if PYTORCH_AVAILABLE else Style.RED}"
        f"{'OK' if PYTORCH_AVAILABLE else 'N/A'}{Style.RESET}\n"
        f"  Matplotlib:{Style.GREEN if MATPLOTLIB_AVAILABLE else Style.RED}"
        f"{'OK' if MATPLOTLIB_AVAILABLE else 'N/A'}{Style.RESET}\n"
        f"  mplfinance:{Style.GREEN if MPLFINANCE_AVAILABLE else Style.RED}"
        f"{'OK' if MPLFINANCE_AVAILABLE else 'N/A'}{Style.RESET}"
    )

def cmd_stats(args):
    return SESSION.summary()

def cmd_history(args):
    n = 20
    if args:
        try:
            n = int(args[0])
        except ValueError:
            pass
    hist = SESSION.history[-n:]
    if not hist:
        return "No commands in this session."
    out = f"\n{BOLD}{Style.CYAN}  COMMAND HISTORY{' '*10}{Style.RESET}\n{Style.DIM}{'-'*42}{Style.RESET}\n"
    for i, cmd in enumerate(hist, 1):
        out += f"  {i:3}. {cmd}\n"
    return out

def cmd_config(args):
    if not args:
        return "Usage: config [show|set KEY VAL]"
    sub = args[0].lower()
    if sub == "show":
        out = f"\n{BOLD}{Style.CYAN}  CONFIGURATION{Style.RESET}\n{Style.MAGENTA}{'='*44}{Style.RESET}\n"
        for k, v in CFG.items():
            out += f"  {k:<22} {Style.YELLOW}{v}{Style.RESET}\n"
        return out
    elif sub == "set":
        if len(args) < 3:
            return "Usage: config set KEY VALUE"
        key = args[1]
        val_str = " ".join(args[2:])
        try:
            val = int(val_str)
        except ValueError:
            try:
                val = float(val_str)
            except ValueError:
                val = True if val_str.lower() in ("true", "yes") else (
                    False if val_str.lower() in ("false", "no") else val_str
                )
        CFG[key] = val
        if save_config():
            return f"Set {key} = {val}"
        return "Failed to save config."
    return "Usage: config [show|set KEY VAL]"

def cmd_clear(args):
    os.system("clear" if os.name == "posix" else "cls")
    greet_user()
    return ""

def cmd_help(args):
    show_help()
    return ""

def cmd_install_mpl(args):
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "mplfinance"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return f"{Style.GREEN}mplfinance installed!{Style.RESET}"
        return f"{Style.RED}Failed: {result.stderr.strip()}{Style.RESET}"
    except Exception as e:
        return f"{Style.RED}Error: {e}{Style.RESET}"


# ── REGISTER COMMANDS ────────────────────────────────────────────────────────

def _register_all():
    r = registry
    r.register("price", cmd_price, "Live animated price HUD", ["AAPL","TSLA","BTC-USD"])
    r.register("chart", cmd_chart, "Interactive price chart (line/candle)", ["AAPL","AAPL candle 1y"])
    r.register("dashboard", cmd_dashboard, "Multi-panel market dashboard", ["india","usa","global"])
    r.register("quote", cmd_price, "Quick price quote (alias for price)", ["AAPL"])
    r.register("fundamentals", cmd_fundamentals, "Full fundamental snapshot", ["AAPL","TCS.NS"])
    r.register("fund", cmd_fundamentals, "Alias for fundamentals", ["AAPL"])
    r.register("income", cmd_income, "Income statement", ["AAPL"])
    r.register("balance", cmd_balance, "Balance sheet", ["AAPL"])
    r.register("cashflow", cmd_cashflow, "Cash flow statement", ["TSLA"])
    r.register("earnings", cmd_earnings, "EPS beat/miss history", ["AAPL"])
    r.register("dividends", cmd_dividends, "Dividend history & yield", ["JNJ"])
    r.register("dcf", cmd_dcf, "DCF fair value estimate", ["AAPL"])
    r.register("analyst", cmd_analyst, "Analyst ratings & targets", ["AAPL"])
    r.register("compare", cmd_compare, "Compare multiple stocks (comma-separated)", ["AAPL,MSFT,GOOGL"])
    r.register("news", cmd_news, "Latest headlines", ["AAPL"])
    r.register("options", cmd_options, "Options chain (calls/puts)", ["AAPL"])
    r.register("fno", cmd_fno, "F&O analytics (chain/calendar/pcr/futures)", ["fno chain RELIANCE.NS","fno calendar AAPL","fno pcr TCS.NS"])
    r.register("futures", cmd_futures, "Futures curve (implied via put-call parity)", ["futures AAPL","futures RELIANCE.NS"])
    r.register("risk", cmd_risk, "Risk metrics (Sharpe/VaR/Beta)", ["AAPL","AAPL ^GSPC"])
    r.register("invest", cmd_invest, "Historical investment calculator", ["AAPL 10000 2020-01-01","TSLA 5000 2y"])
    r.register("portfolio", cmd_portfolio, "Portfolio manager", ["add","show","remove","clear"])
    r.register("watchlist", cmd_watchlist, "Watchlist manager", ["add","show","remove"])
    r.register("fx", cmd_fx, "FX rates dashboard", ["inr","majors","crypto"])
    r.register("fxlive", cmd_fxlive, "Live FX HUD", ["USDINR","EURUSD"])
    r.register("screener", cmd_screener, "Stock screener", ["pe<15"])
    r.register("calc", cmd_calc, "Financial calculators", ["sip 10000 10 12","emi 500000 8 60","pos 100000 2 150"])
    r.register("train", cmd_train, "Train neural net on prices", ["AAPL"])
    r.register("predict", cmd_predict, "AI forecast for next session", ["AAPL"])
    r.register("backtest", cmd_backtest, "Backtest strategy", ["AAPL sma","AAPL rsi"])
    r.register("version", cmd_version, "Show version & dependency status", [])
    r.register("stats", cmd_stats, "Session statistics", [])
    r.register("history", cmd_history, "Command history", ["20"])
    r.register("config", cmd_config, "View/set settings", ["show","set theme nexus","set default_market usa"])
    r.register("theme", cmd_theme, "Switch UI theme", ["bloomberg","nexus","minimal"])
    r.register("clear", cmd_clear, "Clear screen", [])
    r.register("install-mpl", cmd_install_mpl, "Install mplfinance for candle charts", [])
    r.register("help", cmd_help, "Show this help", [])

    set_catalogue(CMD_CATALOGUE)


# ── THEME COMMAND ─────────────────────────────────────────────────────────────

def cmd_theme(args):
    if not args:
        return "Usage: theme [bloomberg|nexus|minimal]"
    return set_theme(args[0])


# ── HELP SCREEN ───────────────────────────────────────────────────────────────

def show_help():
    """Display the categorized help screen."""
    P = get_palette()
    w = BE.box_w
    now = ist_now("%H:%M:%S   %d %b %Y")
    io_st, ses, sc = market_status()
    
    groups = [
        ("MARKET DATA", [
            ("price TICKER", "Live animated price HUD"),
            ("chart TICKER [candle]", "Interactive price chart"),
            ("dashboard [india|usa]", "Multi-panel market overview"),
            ("fx [inr|majors|crypto]", "Currency dashboard"),
            ("fxlive USDINR", "Animated live FX HUD"),
            ("news TICKER", "Latest headlines"),
            ("options TICKER", "Options chain (calls/puts)"),
            ("fno chain/calendar/pcr", "F&O analytics (OI, max pain, PCR)"),
            ("futures TICKER", "Implied futures curve"),
        ]),
        ("FUNDAMENTAL ANALYSIS", [
            ("fundamentals TICKER", "Full valuation snapshot"),
            ("income/balance/cashflow", "Multi-year financial statements"),
            ("earnings TICKER", "EPS beat/miss history"),
            ("dividends TICKER", "Dividend history & CAGR"),
            ("dcf TICKER", "DCF intrinsic value estimate"),
            ("analyst TICKER", "Consensus ratings & targets"),
            ("compare T1,T2,T3", "Multi-stock comparison"),
        ]),
        ("PORTFOLIO & RISK", [
            ("portfolio add/view", "Portfolio P&L manager"),
            ("watchlist add/show", "Live watchlist scanner"),
            ("risk TICKER [benchmark]", "VaR/Sharpe/Beta/Calmar"),
            ("invest TICKER AMT [date]", "Historical return calculator"),
        ]),
        ("AI & QUANT", [
            ("train TICKER", "Train neural net on prices"),
            ("predict TICKER", "AI forecast for next session"),
            ("backtest TICKER [sma|rsi]", "Backtest strategy"),
        ]),
        ("SMART TOOLS", [
            ("calc sip/emi/pos", "Financial calculators"),
            ("theme [bloomberg|nexus]", "Switch UI theme"),
            ("config [show|set]", "Settings manager"),
        ]),
    ]
    
    indent = " " * 2
    
    def _hdr(text):
        return f"{BOLD}{P.amber_l}{text}{RST}"
    
    def _row(cmd, desc):
        return f"  {P.cyan}{cmd:<30}{RST} {P.label}{desc}{RST}"
    
    lines = [BE.bb_top("POCKET TERMINAL ADVANCED v" + __version__)]
    lines.append(BE.bb_row(f"  {BOLD}{P.head}Bloomberg-Style Market Intelligence{RST}  {P.label}{now}  {sc}{ses}{RST}"))
    lines.append(BE.bb_row(f"  {P.label}Theme: {P.amber}{THEME}{RST}  |  Type 'theme bloomberg|nexus|minimal' to switch{RST}"))
    
    for group_name, cmds in groups:
        lines.append(BE.bb_div(group_name))
        for cmd, desc in cmds:
            lines.append(BE.bb_row(_row(cmd, desc)))
    
    lines.append(BE.bb_div())
    
    # Function key row
    fkeys = [
        ("F1", "HELP"), ("F2", "DASH"), ("F3", "PRICE"), ("F4", "CHART"),
        ("F5", "FUND"), ("F6", "NEWS"), ("F7", "PORT"), ("F8", "RISK"),
        ("F9", "FX"), ("F10", "..."),
    ]
    fk_str = "  ".join(f"{BOLD}{P.amber}{k}{RST} {P.label}{v}{RST}" for k, v in fkeys)
    lines.append(BE.bb_row(f"  {fk_str}"))
    lines.append(BE.bb_bot())
    
    sys.stdout.write("\n".join(lines))
    sys.stdout.flush()


# ── BOOT ANIMATIONS ───────────────────────────────────────────────────────────

def _bb_boot_bloomberg():
    """Amber terminal boot animation."""
    P = get_palette()
    w = BE.box_w
    logo_lines = [
        f"{rgb(255,170,0)}  ███████ ██      ██████  ██████ ███   ███████████{RST}",
        f"{rgb(242,156,0)}  ████████████     █████████ ████████████ ████ ██████    ██████{RST}",
        f"{rgb(229,148,0)}  ███████████     ██   ██████   ████████████  ██████████   ██████{RST}",
        f"{rgb(216,140,0)}  ███████     ██     ███████████████   ███████████   ██████{RST}",
        f"{rgb(204,132,0)}  ███████████████████   ██████████████████   ██████ █████████ ██████{RST}",
        f"{rgb(191,123,0)}  ███████████████████████    ███████████████████   █████████████████{RST}",
        f"{rgb(178,115,0)}  ████████   ████████████   ███████████████████████████████████{RST}",
        f"{rgb(130,145,160)}  P R O F E S S I O N A L   F I N A N C I A L   T E R M I N A L   v{__version__}{RST}",
    ]
    for ln in logo_lines:
        sys.stdout.write(CLEAR if ln is logo_lines[0] else "")
        sys.stdout.write(ln + "\n")
        sys.stdout.flush()
        time.sleep(0.08)


def _boot_nexus():
    """Rainbow sci-fi boot animation."""
    P = get_palette()
    w = BE.box_w
    for r in range(0, w + 1):
        t = time.time() % 10
        r1, g1, b1 = _rainbow(t, r / w, 0.3)
        sys.stdout.write("\r" + rgb(r1, g1, b1) + "█" * r + RST + " " * (w - r))
        sys.stdout.flush()
        time.sleep(0.004)
    time.sleep(0.15)


def greet_user():
    """Run the boot animation and show initialization."""
    P = get_palette()
    w = BE.box_w
    now = ist_now("%H:%M:%S   %d %b %Y")
    
    if THEME in ("bloomberg", "minimal"):
        _bb_boot_bloomberg()
    else:
        _boot_nexus()
    
    # System status messages
    statuses = [
        ("INIT", "Bloomberg Interface", True),
        ("FEEDS", "Market data connection", YFINANCE_AVAILABLE),
        ("AUTH", "Authentication", True),
        ("CLOCK", f"System clock  {now}", True),
        ("ML", "Neural engine", PYTORCH_AVAILABLE),
        ("DATA", "Market feeds", YFINANCE_AVAILABLE),
        ("CHART", "Charting engine", MATPLOTLIB_AVAILABLE),
    ]
    
    sys.stdout.write(ELINE + "\n")
    for label, desc, ok in statuses:
        status_color = rgb(0, 210, 100) if ok else rgb(255, 60, 60)
        status_text = "ONLINE" if ok else "OFFLINE" if desc != "Authentication" and desc != "System clock" else "PASS" if ok else "FAIL"
        sys.stdout.write(
            f"  {rgb(255,170,0)}[{label}]{RST}  {desc:<30}  {status_color}{status_text}{RST}\n"
        )
        sys.stdout.flush()
        time.sleep(0.1)
    
    sys.stdout.write(f"  {BOLD}{rgb(0,210,100)}[OK]{RST}  {BOLD}{rgb(0,210,100)}ALL SYSTEMS NOMINAL  -  POCKET TERMINAL ONLINE{RST}\n\n")
    sys.stdout.flush()


# ── MAIN REPL LOOP ────────────────────────────────────────────────────────────

def _is_known_command(cmd: str) -> bool:
    """Fast check if a command is registered."""
    return cmd in registry._commands or cmd in ("exit", "quit", "q")


def main():
    """Main entry point."""
    _register_all()
    
    greet_user()
    show_help()
    
    frame = 0
    last_prompt = 0
    prompt_cache = ""
    
    while True:
        now_t = time.time()
        if now_t - last_prompt > 0.5:
            prompt_cache = BE.prompt_line(frame)
            last_prompt = now_t
            frame += 1
        
        try:
            raw = input_with_ghost(prompt_cache)
        except KeyboardInterrupt:
            print(f"\n  {Style.DIM}Type 'exit' to quit.{Style.RESET}")
            continue
        except EOFError:
            print("\nGoodbye.")
            break
        
        cmd = raw.strip()
        if not cmd:
            continue
        
        SESSION.record(cmd)
        
        if cmd.lower() in ("exit", "quit", "q"):
            print("Goodbye.")
            break
        
        if cmd.lower() in ("clear", "cls"):
            cmd_clear([])
            continue
        
        if cmd.lower() == "help":
            cmd_help([])
            continue
        
        parts = cmd.split()
        command = parts[0].lower()
        args = parts[1:]
        
        try:
            result = registry.execute(command, args)
            if result:
                BE.print_result(str(result))
        except Exception as e:
            SESSION.record(cmd, ok=False)
            BE.bb_print_result(f"  {Style.RED}Error: {e}{Style.RESET}")
    
    stop_prefetch()
    sys.exit(0)


if __name__ == "__main__":
    main()
