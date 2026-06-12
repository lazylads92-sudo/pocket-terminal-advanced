from ..data.market import YFINANCE_AVAILABLE, _get_ticker, get_info
from ..core.style import Style

def _fmt(val, prefix="", suffix="", decimals=2, billions=False, millions=False):
    try:
        if val is None or (isinstance(val, float) and val != val):
            return f"{Style.DIM}N/A{Style.RESET}"
        v = float(val)
        if billions: return f"{prefix}{v/1e9:.{decimals}f}B{suffix}"
        if millions: return f"{prefix}{v/1e6:.{decimals}f}M{suffix}"
        return f"{prefix}{v:.{decimals}f}{suffix}"
    except Exception: return f"{Style.DIM}N/A{Style.RESET}"

def _score_color(value, good_above=None):
    try:
        v = float(value)
        if good_above is not None and v >= good_above: return f"{Style.GREEN}{value}{Style.RESET}"
        if good_above is not None and v >= good_above * 0.7: return f"{Style.YELLOW}{value}{Style.RESET}"
        return f"{Style.YELLOW}{value}{Style.RESET}"
    except Exception: return str(value)

_CSYMS = {"USD":"$","INR":"Rs","EUR":"Eu","GBP":"Pound","JPY":"Y","CNY":"Y","CAD":"CA$","AUD":"A$","HKD":"HK$","SGD":"S$","KRW":"W","BRL":"R$","CHF":"Fr","TWD":"NT$"}

def _cs(info): return _CSYMS.get(info.get("currency", "USD"), "$")

def _fmtc(info, key, billions=False, decimals=2):
    cs = _cs(info)
    val = info.get(key)
    return _fmt(val, prefix=cs, billions=billions, decimals=decimals)

def get_fundamentals(ticker):
    if not YFINANCE_AVAILABLE: return f"{Style.RED}yfinance required.{Style.RESET}"
    try:
        info = get_info(ticker, ttl=300)
        name = info.get("longName", ticker.upper())
        sector = info.get("sector", "N/A")
        industry = info.get("industry", "N/A")
        pe = _fmt(info.get("trailingPE"), decimals=1)
        fpe = _fmt(info.get("forwardPE"), decimals=1)
        pb = _fmt(info.get("priceToBook"), decimals=2)
        ev_ebitda = _fmt(info.get("enterpriseToEbitda"), decimals=1)
        mktcap = _fmt(info.get("marketCap"), prefix="$", billions=True)
        gross_m = _fmt((info.get("grossMargins") or 0)*100, suffix="%", decimals=1)
        net_m = _fmt((info.get("profitMargins") or 0)*100, suffix="%", decimals=1)
        roe = _fmt((info.get("returnOnEquity") or 0)*100, suffix="%", decimals=1)
        rev_growth = _fmt((info.get("revenueGrowth") or 0)*100, suffix="%", decimals=1)
        de_ratio = _fmt(info.get("debtToEquity"), decimals=2)
        eps = _fmt(info.get("trailingEps"), prefix="$")
        tgt = _fmt(info.get("targetMeanPrice"), prefix="$")
        w52_hi = _fmt(info.get("fiftyTwoWeekHigh"), prefix="$")
        w52_lo = _fmt(info.get("fiftyTwoWeekLow"), prefix="$")
        return (
            f"\n{Style.MAGENTA}{'='*58}{Style.RESET}\n"
            f"{Style.BOLD}{Style.CYAN}  FUNDAMENTAL ANALYSIS - {name} ({ticker.upper()}){Style.RESET}\n"
            f"{Style.DIM}  {sector} - {industry}{Style.RESET}\n"
            f"{Style.MAGENTA}{'='*58}{Style.RESET}\n"
            f"  {'P/E (TTM)':<22} {pe:<12}  {'Fwd P/E':<18} {fpe}\n"
            f"  {'P/B':<22} {pb:<12}  {'EV/EBITDA':<18} {ev_ebitda}\n"
            f"  {'Market Cap':<22} {mktcap}\n"
            f"  {'Gross Margin':<22} {gross_m:<12}  {'Net Margin':<18} {net_m}\n"
            f"  {'ROE':<22} {roe:<12}  {'Rev Growth':<18} {rev_growth}\n"
            f"  {'Debt/Equity':<22} {de_ratio:<12}  {'EPS (TTM)':<18} {eps}\n"
            f"  {'52W High':<22} {w52_hi:<12}  {'52W Low':<18} {w52_lo}\n"
            f"  {'Analyst Target':<22} {tgt}\n"
            f"{Style.MAGENTA}{'='*58}{Style.RESET}"
        )
    except Exception as e: return f"{Style.RED}Error: {e}{Style.RESET}"

def get_dcf(ticker):
    if not YFINANCE_AVAILABLE: return f"{Style.RED}yfinance required.{Style.RESET}"
    try:
        t = _get_ticker(ticker)
        info = get_info(ticker, ttl=300)
        cf = t.cashflow
        name = info.get("longName", ticker.upper())
        curr_price = info.get("currentPrice") or info.get("regularMarketPrice", 0)
        shares_out = info.get("sharesOutstanding", 1)
        total_debt = info.get("totalDebt", 0) or 0
        total_cash = info.get("totalCash", 0) or 0
        fcf = None
        if cf is not None and not cf.empty and "Free Cash Flow" in cf.index:
            fcf = float(cf.loc["Free Cash Flow", cf.columns[0]])
        if fcf is None or fcf <= 0: fcf = info.get("freeCashflow")
        if not fcf or fcf <= 0: return f"{Style.YELLOW}DCF requires positive FCF.{Style.RESET}"
        g1 = info.get("earningsGrowth") or info.get("revenueGrowth") or 0.10
        g1 = max(min(float(g1), 0.40), -0.05)
        g2 = g1 * 0.60
        g_terminal = 0.025
        dr = 0.10
        projected = [fcf * ((1 + g1) ** yr) for yr in range(1, 6)]
        projected += [projected[-1] * ((1 + g2) ** yr) for yr in range(1, 6)]
        tv = projected[-1] * (1 + g_terminal) / (dr - g_terminal)
        pv = sum(f / (1 + dr) ** i for i, f in enumerate(projected, 1))
        pv_tv = tv / (1 + dr) ** 10
        ev = pv + pv_tv
        eq_v = ev - total_debt + total_cash
        fv = eq_v / shares_out if shares_out > 0 else 0
        mos = ((fv - curr_price) / fv * 100) if fv > 0 else 0
        if fv > curr_price * 1.1: verdict = f"{Style.GREEN}UNDERVALUED ({mos:.1f}% upside){Style.RESET}"
        elif fv < curr_price * 0.9: verdict = f"{Style.RED}OVERVALUED ({abs(mos):.1f}% downside){Style.RESET}"
        else: verdict = f"{Style.YELLOW}FAIRLY VALUED{Style.RESET}"
        return (
            f"\n{Style.MAGENTA}{'='*58}{Style.RESET}\n"
            f"{Style.BOLD}{Style.CYAN}  DCF FAIR VALUE - {name} ({ticker.upper()}){Style.RESET}\n"
            f"{Style.MAGENTA}{'='*58}{Style.RESET}\n"
            f"  Current Price:       {Style.CYAN}${curr_price:.2f}{Style.RESET}\n"
            f"  Base FCF:            {_fmt(fcf, prefix='$', billions=True)}\n"
            f"  Growth Rate (5yr):   {Style.YELLOW}{g1*100:.1f}%{Style.RESET}\n"
            f"  Discount Rate:       {dr*100:.1f}%\n"
            f"  Intrinsic Value:     {Style.MAGENTA}${fv:.2f}/share{Style.RESET}\n"
            f"  {verdict}\n"
            f"{Style.MAGENTA}{'='*58}{Style.RESET}"
        )
    except Exception as e: return f"{Style.RED}DCF Error: {e}{Style.RESET}"

def get_analyst_ratings(ticker):
    if not YFINANCE_AVAILABLE: return f"{Style.RED}yfinance required.{Style.RESET}"
    try:
        info = get_info(ticker, ttl=300)
        n = info.get("numberOfAnalystOpinions", "N/A")
        rec = info.get("recommendationMean", None)
        tgt_hi = _fmt(info.get("targetHighPrice"), prefix="$")
        tgt_lo = _fmt(info.get("targetLowPrice"), prefix="$")
        tgt_mn = _fmt(info.get("targetMeanPrice"), prefix="$")
        curr = info.get("currentPrice") or info.get("regularMarketPrice", 0)
        upside = ((info.get("targetMeanPrice", 0) / curr) - 1) * 100 if curr else None
        rec_str = ""
        if rec:
            s = float(rec)
            if s <= 1.5: clr, lbl = Style.GREEN, "STRONG BUY"
            elif s <= 2.5: clr, lbl = Style.GREEN, "BUY"
            elif s <= 3.5: clr, lbl = Style.YELLOW, "HOLD"
            elif s <= 4.5: clr, lbl = Style.RED, "SELL"
            else: clr, lbl = Style.RED, "STRONG SELL"
            rec_str = f"{clr}{lbl} ({s:.1f}/5){Style.RESET}"
        return (
            f"\n{Style.MAGENTA}{'='*58}{Style.RESET}\n"
            f"{Style.BOLD}{Style.CYAN}  ANALYST RATINGS - {ticker.upper()}{Style.RESET}\n"
            f"{Style.MAGENTA}{'='*58}{Style.RESET}\n"
            f"  Consensus:       {rec_str}\n"
            f"  Analysts:        {Style.CYAN}{n}{Style.RESET}\n"
            f"  Target Hi:       {tgt_hi}    Lo: {tgt_lo}\n"
            f"  Target Mean:     {tgt_mn}\n"
            f"  Upside:          {_fmt(upside, suffix='%', decimals=1)}\n"
            f"{Style.MAGENTA}{'='*58}{Style.RESET}"
        )
    except Exception as e: return f"{Style.RED}Error: {e}{Style.RESET}"

def get_compare(tickers_str):
    if not YFINANCE_AVAILABLE: return f"{Style.RED}yfinance required.{Style.RESET}"
    tickers = [t.strip().upper() for t in tickers_str.split(",") if t.strip()]
    if len(tickers) < 2: return "Provide at least 2 tickers."
    if len(tickers) > 5: return "Max 5 tickers."
    infos = {}
    for tk in tickers:
        try: infos[tk] = _get_ticker(tk).info
        except Exception: infos[tk] = {}
    metrics = [
        ("Price", lambda i, t: _fmtc(i, "currentPrice" or "regularMarketPrice", decimals=2) or _fmtc(i, "regularMarketPrice", decimals=2)),
        ("Mkt Cap", lambda i, t: _fmtc(i, "marketCap", billions=True)),
        ("P/E", lambda i, t: _fmt(i.get("trailingPE"), decimals=1)),
        ("Fwd P/E", lambda i, t: _fmt(i.get("forwardPE"), decimals=1)),
        ("P/B", lambda i, t: _fmt(i.get("priceToBook"), decimals=2)),
        ("EV/EBITDA", lambda i, t: _fmt(i.get("enterpriseToEbitda"), decimals=1)),
        ("Rev Growth", lambda i, t: _fmt((i.get("revenueGrowth") or 0)*100, suffix="%", decimals=1)),
        ("Net Margin", lambda i, t: _fmt((i.get("profitMargins") or 0)*100, suffix="%", decimals=1)),
        ("ROE", lambda i, t: _fmt((i.get("returnOnEquity") or 0)*100, suffix="%", decimals=1)),
        ("D/E", lambda i, t: _fmt(i.get("debtToEquity"), decimals=2)),
        ("Div Yield", lambda i, t: _fmt((i.get("dividendYield") or 0)*100, suffix="%", decimals=2)),
        ("Target", lambda i, t: _fmtc(i, "targetMeanPrice", decimals=2) or "N/A"),
    ]
    col_w, label_w = 15, 14
    header = f"  {'':<{label_w}} " + "  ".join(f"{tk:>{col_w}}" for tk in tickers)
    out = (
        f"\n{Style.CYAN}{'='*max(60, len(header))}{Style.RESET}\n"
        f"{Style.BOLD}  STOCK COMPARISON: {' vs '.join(tickers)}{Style.RESET}\n"
        f"{Style.CYAN}{'='*max(60, len(header))}{Style.RESET}\n"
        f"{Style.BOLD}{header}{Style.RESET}\n"
    )
    for label, fn in metrics:
        row = f"  {Style.CYAN}{label:<{label_w}}{Style.RESET}"
        for tk in tickers:
            val = fn(infos.get(tk, {}), tk)
            row += f"  {Style.GREEN if 'N/A' not in val else Style.DIM}{val:>{col_w}}{Style.RESET}"
        out += row + "\n"
    out += f"{Style.CYAN}{'='*max(60, len(header))}{Style.RESET}"
    return out
