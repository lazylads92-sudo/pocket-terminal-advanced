"""Optimized market data layer with threaded prefetching, aggressive caching, and concurrent batch fetches."""

import sys, time, threading, concurrent.futures
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple, List, Callable
from functools import lru_cache

YFINANCE_AVAILABLE = False
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    yf = None

import pandas as pd
import numpy as np

# ──────────────────────────────────────────────────────────────────────────────
#  AGGRESSIVE CACHE with TTL
# ──────────────────────────────────────────────────────────────────────────────

class TTLCache:
    """Thread-safe TTL cache with background expiry."""
    
    def __init__(self, default_ttl: float = 2.0):
        self._data: Dict[str, Tuple[Any, float]] = {}
        self._default_ttl = default_ttl
        self._lock = threading.Lock()
    
    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            val, expiry = entry
            if time.monotonic() > expiry:
                del self._data[key]
                return None
            return val
    
    def set(self, key: str, value: Any, ttl: Optional[float] = None):
        ttl = ttl if ttl is not None else self._default_ttl
        with self._lock:
            self._data[key] = (value, time.monotonic() + ttl)
    
    def invalidate(self, key: str):
        with self._lock:
            self._data.pop(key, None)
    
    def clear(self):
        with self._lock:
            self._data.clear()
    
    @property
    def size(self) -> int:
        with self._lock:
            return len(self._data)


# ──────────────────────────────────────────────────────────────────────────────
#  GLOBAL CACHES
# ──────────────────────────────────────────────────────────────────────────────

_ticker_cache: Dict[str, "yf.Ticker"] = {}
_ticker_lock = threading.Lock()

# Fast price cache: TTL 1.5 seconds
_price_cache = TTLCache(default_ttl=1.5)

# Info cache: TTL 30 seconds for regular, 300s for fundamentals
_info_cache = TTLCache(default_ttl=30.0)

# History cache: TTL 60 seconds
_history_cache = TTLCache(default_ttl=60.0)

# Background prefetcher
_prefetch_thread: Optional[threading.Thread] = None
_prefetch_queue: List[str] = []
_prefetch_lock = threading.Lock()
_prefetch_stop = threading.Event()


def _get_ticker(sym: str) -> "yf.Ticker":
    """Get cached yfinance Ticker object."""
    sym = sym.upper()
    with _ticker_lock:
        t = _ticker_cache.get(sym)
        if t is None:
            t = yf.Ticker(sym)
            _ticker_cache[sym] = t
        return t


def get_info(sym: str, ttl: Optional[float] = None) -> dict:
    """Get cached ticker info."""
    sym = sym.upper()
    cached = _info_cache.get(sym)
    if cached is not None:
        return cached
    
    try:
        t = _get_ticker(sym)
        info = t.info or {}
        _info_cache.set(sym, info, ttl or 30.0)
        return info
    except Exception:
        return {}


def fetch_fast_price(sym: str) -> Optional[float]:
    """Ultra-fast price fetch with aggressive caching."""
    sym = sym.upper()
    cached = _price_cache.get(sym)
    if cached is not None:
        return cached
    
    try:
        t = _get_ticker(sym)
        # fast_info is cached by yfinance internally, but still has overhead
        fi = t.fast_info
        price = (getattr(fi, "last_price", None) or 
                 getattr(fi, "regular_market_price", None) or
                 getattr(fi, "regularMarketPrice", None))
        if price is not None:
            price = float(price)
            if price > 0:
                _price_cache.set(sym, price, 1.5)
                return price
        
        # Fallback: use history's last close
        hist = _get_cached_history(sym, period="1d", interval="1m")
        if hist is not None and not hist.empty:
            price = float(hist["Close"].iloc[-1])
            if price > 0:
                _price_cache.set(sym, price, 2.0)
                return price
    except Exception:
        pass
    
    return None


def _get_cached_history(sym: str, period: str = "1mo", interval: str = "1d") -> Optional[pd.DataFrame]:
    """Get cached history or fetch."""
    cache_key = f"{sym}:{period}:{interval}"
    cached = _history_cache.get(cache_key)
    if cached is not None:
        return cached
    
    try:
        t = _get_ticker(sym)
        hist = t.history(period=period, interval=interval)
        if hist is not None and not hist.empty:
            if hasattr(hist.index, "tz") and hist.index.tz is not None:
                hist.index = hist.index.tz_localize(None)
            _history_cache.set(cache_key, hist, 60.0)
            return hist
    except Exception:
        pass
    
    return None


def fetch_history(sym: str, period: str = "1mo", interval: str = "1d") -> Optional[pd.DataFrame]:
    """Public history fetcher with caching."""
    # Also prefetch next period in background
    _prefetch_symbol(sym)
    return _get_cached_history(sym, period, interval)


def get_quote(sym: str) -> dict:
    """Get a full quote dict with price, change, volume in one shot."""
    sym = sym.upper()
    
    try:
        t = _get_ticker(sym)
        info = get_info(sym, ttl=10.0)
        
        price = fetch_fast_price(sym)
        if price is None:
            hist = fetch_history(sym, "5d", "1d")
            if hist is not None and not hist.empty:
                price = float(hist["Close"].iloc[-1])
        
        prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose") or price
        
        hist = _get_cached_history(sym, "5d", "1d")
        vol = 0
        if hist is not None and not hist.empty:
            vol = float(hist["Volume"].iloc[-1]) if "Volume" in hist.columns else 0
        
        change = (price - prev_close) if (price and prev_close) else 0
        pct = (change / prev_close * 100) if (prev_close and prev_close != 0) else 0
        
        return {
            "price": price,
            "prev_close": prev_close,
            "change": change,
            "change_pct": pct,
            "volume": vol,
            "currency": info.get("currency", "USD"),
            "name": info.get("shortName") or info.get("longName") or sym,
            "market_cap": info.get("marketCap"),
            "pe": info.get("trailingPE"),
            "high_52w": info.get("fiftyTwoWeekHigh"),
            "low_52w": info.get("fiftyTwoWeekLow"),
        }
    except Exception:
        return {"price": None, "change": 0, "change_pct": 0, "error": True}


# ──────────────────────────────────────────────────────────────────────────────
#  BATCH / CONCURRENT DATA FETCHING
# ──────────────────────────────────────────────────────────────────────────────

def fetch_batch_quotes(symbols: List[str]) -> Dict[str, dict]:
    """Fetch multiple quotes concurrently using ThreadPoolExecutor."""
    results: Dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(symbols), 8)) as ex:
        fut_map = {ex.submit(get_quote, s): s for s in symbols}
        for fut in concurrent.futures.as_completed(fut_map):
            sym = fut_map[fut]
            try:
                results[sym] = fut.result()
            except Exception:
                results[sym] = {"price": None, "error": True}
    return results


def fetch_batch_prices(symbols: List[str]) -> Dict[str, Optional[float]]:
    """Fetch multiple prices concurrently."""
    results: Dict[str, Optional[float]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(symbols), 8)) as ex:
        fut_map = {ex.submit(fetch_fast_price, s): s for s in symbols}
        for fut in concurrent.futures.as_completed(fut_map):
            sym = fut_map[fut]
            try:
                results[sym] = fut.result()
            except Exception:
                results[sym] = None
    return results


# ──────────────────────────────────────────────────────────────────────────────
#  BACKGROUND PREFETCHER
# ──────────────────────────────────────────────────────────────────────────────

def _prefetch_symbol(sym: str):
    """Queue a symbol for background prefetching."""
    with _prefetch_lock:
        if sym not in _prefetch_queue:
            _prefetch_queue.append(sym)


def _prefetch_worker():
    """Background thread that prefetches market data."""
    while not _prefetch_stop.is_set():
        sym = None
        with _prefetch_lock:
            if _prefetch_queue:
                sym = _prefetch_queue.pop(0)
        
        if sym is not None:
            try:
                # Warm up caches
                _ = fetch_fast_price(sym)
                _ = get_info(sym, ttl=30.0)
            except Exception:
                pass
        else:
            time.sleep(0.1)


def start_prefetch():
    """Start the background prefetch thread."""
    global _prefetch_thread
    if _prefetch_thread is not None and _prefetch_thread.is_alive():
        return
    _prefetch_stop.clear()
    _prefetch_thread = threading.Thread(target=_prefetch_worker, daemon=True)
    _prefetch_thread.start()


def stop_prefetch():
    """Stop the background prefetch thread."""
    _prefetch_stop.set()


# ──────────────────────────────────────────────────────────────────────────────
#  OPTIONS CHAIN & F&O DATA
# ──────────────────────────────────────────────────────────────────────────────

def get_options_chain(sym: str, expiration: Optional[str] = None) -> Optional[dict]:
    """Get options chain for a symbol."""
    try:
        t = _get_ticker(sym)
        if expiration:
            chains = t.option_chain(expiration)
        else:
            exps = t.options
            if not exps:
                return None
            chains = t.option_chain(exps[0])
        
        calls = chains.calls
        puts = chains.puts
        
        if calls is not None and not calls.empty:
            calls = calls.head(20)[["strike", "lastPrice", "bid", "ask", "volume", "openInterest", "impliedVolatility"]]
        if puts is not None and not puts.empty:
            puts = puts.head(20)[["strike", "lastPrice", "bid", "ask", "volume", "openInterest", "impliedVolatility"]]
        
        return {
            "calls": calls.to_dict("records") if calls is not None else [],
            "puts": puts.to_dict("records") if puts is not None else [],
            "expiration": expiration or (t.options[0] if t.options else None),
            "expirations": t.options[:10] if t.options else [],
        }
    except Exception:
        return None


def get_fno_stocks() -> List[str]:
    """Return a list of common F&O stocks (NSE India + US)."""
    return [
        "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
        "HINDUNILVR.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS",
        "LT.NS", "WIPRO.NS", "AXISBANK.NS", "BAJFINANCE.NS", "MARUTI.NS",
        "TITAN.NS", "SUNPHARMA.NS", "NTPC.NS", "M&M.NS", "POWERGRID.NS",
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META",
    ]


# ──────────────────────────────────────────────────────────────────────────────
#  F&O ANALYSIS — Expiry Calendar / PCR / Max Pain / OI Concentration
# ──────────────────────────────────────────────────────────────────────────────

def get_fno_calendar(sym: str) -> Optional[list]:
    """Get F&O expiry calendar with days-to-expiry for a symbol."""
    try:
        t = _get_ticker(sym)
        exps = t.options
        if not exps:
            return None
        today = datetime.now().date()
        cal = []
        for e in exps[:20]:
            d = datetime.strptime(e, "%Y-%m-%d").date()
            cal.append({"date": e, "dte": (d - today).days, "day": d.strftime("%a")})
        return cal
    except Exception:
        return None


def compute_pcr(calls: "pd.DataFrame", puts: "pd.DataFrame") -> dict:
    """Compute Put-Call Ratio (OI and Volume)."""
    r = {"pcr_oi": 0.0, "pcr_vol": 0.0, "call_oi": 0, "put_oi": 0, "call_vol": 0, "put_vol": 0}
    if calls is None or puts is None:
        return r
    coi = calls["openInterest"].sum() if "openInterest" in calls else 0
    poi = puts["openInterest"].sum() if "openInterest" in puts else 0
    cvol = calls["volume"].sum() if "volume" in calls else 0
    pvol = puts["volume"].sum() if "volume" in puts else 0
    r.update({
        "call_oi": int(coi) if not pd.isna(coi) else 0,
        "put_oi": int(poi) if not pd.isna(poi) else 0,
        "call_vol": int(cvol) if not pd.isna(cvol) else 0,
        "put_vol": int(pvol) if not pd.isna(pvol) else 0,
        "pcr_oi": round(float(poi) / float(coi), 3) if float(coi) > 0 else 0.0,
        "pcr_vol": round(float(pvol) / float(cvol), 3) if float(cvol) > 0 else 0.0,
    })
    return r


def compute_max_pain(calls: "pd.DataFrame", puts: "pd.DataFrame") -> Optional[float]:
    """Compute max pain strike — strike where option buyers lose the most."""
    if calls is None or puts is None or calls.empty or puts.empty:
        return None
    if "openInterest" not in calls.columns or "openInterest" not in puts.columns:
        return None
    if calls["openInterest"].sum() == 0 and puts["openInterest"].sum() == 0:
        return None
    strikes = sorted(set(calls["strike"].tolist()) | set(puts["strike"].tolist()))
    if not strikes:
        return None
    min_pain = float("inf")
    mp_strike = None
    for s in strikes:
        call_sub = calls[calls["strike"] < s]
        put_sub = puts[puts["strike"] > s]
        call_p = (call_sub["openInterest"] * (s - call_sub["strike"])).sum() if not call_sub.empty else 0.0
        put_p = (put_sub["openInterest"] * (put_sub["strike"] - s)).sum() if not put_sub.empty else 0.0
        total = float(call_p) + float(put_p)
        if total < min_pain:
            min_pain = total
            mp_strike = s
    return mp_strike


def get_fno_chain_analysis(sym: str, expiration: Optional[str] = None) -> Optional[dict]:
    """Full F&O chain analysis: calls, puts, PCR, max pain, top OI strikes."""
    try:
        t = _get_ticker(sym)
        exps = t.options
        if not exps:
            return None
        if expiration is None or expiration not in exps:
            expiration = exps[0]
        chain = t.option_chain(expiration)
        calls, puts = chain.calls, chain.puts
        pcr = compute_pcr(calls, puts)
        mp = compute_max_pain(calls, puts)
        top_c = calls.nlargest(5, "openInterest")[["strike", "openInterest"]] if "openInterest" in calls.columns else pd.DataFrame()
        top_p = puts.nlargest(5, "openInterest")[["strike", "openInterest"]] if "openInterest" in puts.columns else pd.DataFrame()
        price = fetch_fast_price(sym)
        if price is None:
            info = get_info(sym, ttl=30)
            price = info.get("regularMarketPrice") or info.get("regular_market_price")
        return {
            "symbol": sym, "price": price, "expiration": expiration,
            "expirations": exps[:10], "calls": calls, "puts": puts,
            "pcr": pcr, "max_pain": mp,
            "top_call_oi": top_c.to_dict("records") if not top_c.empty else [],
            "top_put_oi": top_p.to_dict("records") if not top_p.empty else [],
        }
    except Exception:
        return None


def get_pcr_series(sym: str, count: int = 6) -> Optional[list]:
    """PCR trend across multiple expiries."""
    try:
        t = _get_ticker(sym)
        exps = t.options
        if not exps:
            return None
        series = []
        for e in exps[:count]:
            chain = t.option_chain(e)
            pcr = compute_pcr(chain.calls, chain.puts)
            series.append({"expiration": e, "pcr_oi": pcr["pcr_oi"], "pcr_vol": pcr["pcr_vol"],
                           "call_oi": pcr["call_oi"], "put_oi": pcr["put_oi"]})
        return series
    except Exception:
        return None


def get_implied_futures(sym: str) -> Optional[list]:
    """Derive implied futures prices across expiries via put-call parity: F ≈ K + C − P."""
    try:
        t = _get_ticker(sym)
        exps = t.options
        if not exps:
            return None
        spot = fetch_fast_price(sym)
        if spot is None:
            return None
        futures = []
        for e in exps[:6]:
            chain = t.option_chain(e)
            calls, puts = chain.calls, chain.puts
            if calls.empty or puts.empty:
                continue
            nearest = min(calls["strike"].unique(), key=lambda x: abs(x - spot))
            c_row = calls[calls["strike"] == nearest]
            p_row = puts[puts["strike"] == nearest]
            if c_row.empty or p_row.empty:
                continue
            cp = c_row.iloc[0].get("lastPrice")
            pp = p_row.iloc[0].get("lastPrice")
            if cp is None or pp is None or pd.isna(cp) or pd.isna(pp):
                continue
            implied = float(nearest) + float(cp) - float(pp)
            exp_date = datetime.strptime(e, "%Y-%m-%d").date()
            dte = (exp_date - datetime.now().date()).days
            futures.append({
                "expiration": e, "dte": dte, "implied_price": round(implied, 2),
                "atm_strike": int(nearest), "call_price": round(float(cp), 2), "put_price": round(float(pp), 2),
            })
        return futures
    except Exception:
        return None

if YFINANCE_AVAILABLE:
    start_prefetch()
