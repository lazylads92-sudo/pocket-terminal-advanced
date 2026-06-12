import numpy as np

try:
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpl_patches
    import matplotlib.dates as mpl_dates
    from ..analysis.technical import find_support_resistance, fibonacci_levels
except ImportError:
    plt = None; mpl_patches = None; mpl_dates = None


class AdvancedInteractiveChart:
    """Interactive chart engine with crosshair, zoom, drawing tools, overlays."""

    def __init__(self, fig, axes, df, ticker, has_volume=True):
        self.fig = fig
        self.ax_price = axes[0]
        self.ax_vol = axes[1] if has_volume else None
        self.ax_rsi = axes[2] if len(axes) > 2 else None
        self.ax_macd = axes[3] if len(axes) > 3 else None
        self.df = df
        self.ticker = ticker.upper()
        self.dates = df.index
        self.n = len(df)
        self._view_start = 0
        self._view_end = self.n - 1
        self._min_window = 20
        self.trendline_clicks = []
        self.drawn_lines = []
        self.drawn_points = []
        self.fib_lines = []
        self.sr_lines = []
        self.vp_bars = []
        self.measure_active = False
        self.measure_start = None
        self.measure_rect = None
        self.measure_text = None
        self.show_ma = True
        self.show_bb = True
        self.show_fib = False
        self.show_sr = False
        self.show_vp = False
        self._ch_vlines = []
        self._ch_hline = None
        self._ch_tooltip = None
        self._draw_overlays()
        self._connect_events()
        self._update_title()

    def _draw_overlays(self):
        df = self.df; ax = self.ax_price
        colors = {"MA_20": "#ffcc00", "MA_50": "#ff9900", "MA_200": "#ff3366"}
        self._ma_plots = []
        for col, clr in colors.items():
            if col in df.columns:
                ln, = ax.plot(df.index, df[col], color=clr, linewidth=0.9, linestyle="--", alpha=0.85)
                self._ma_plots.append(ln)
        self._bb_plots = []
        if "BB_Upper" in df.columns:
            ax.fill_between(df.index, df["BB_Upper"], df["BB_Lower"], alpha=0.06, color="cyan")
            u, = ax.plot(df.index, df["BB_Upper"], color="cyan", linewidth=0.6, linestyle=":", alpha=0.7)
            l, = ax.plot(df.index, df["BB_Lower"], color="cyan", linewidth=0.6, linestyle=":", alpha=0.7)
            self._bb_plots = [u, l]

    def _toggle_vis(self, plots, show):
        for p in plots:
            p.set_visible(show)
        self.fig.canvas.draw_idle()

    def _init_crosshair(self):
        kw = dict(color="#aaaaaa", linewidth=0.8, linestyle="--", alpha=0.75)
        for ax in [a for a in [self.ax_price, self.ax_vol, self.ax_rsi, self.ax_macd] if a]:
            vl = ax.axvline(x=0, **kw)
            self._ch_vlines.append(vl)
        self._ch_hline = self.ax_price.axhline(y=0, **kw)
        self._ch_tooltip = self.ax_price.text(
            0.01, 0.97, "", transform=self.ax_price.transAxes,
            fontsize=7.5, color="white", verticalalignment="top",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#1a1a2e", edgecolor="#00ffcc", alpha=0.88), zorder=10
        )

    def _on_mouse_move(self, event):
        if not self._ch_vlines: self._init_crosshair()
        if event.inaxes not in [a for a in [self.ax_price, self.ax_vol, self.ax_rsi, self.ax_macd] if a]:
            return
        if event.xdata is None: return
        try:
            md = mpl_dates.date2num(self.dates)
            idx = int(np.argmin(np.abs(md - event.xdata)))
            idx = max(0, min(idx, self.n - 1))
        except: return
        row = self.df.iloc[idx]
        date = str(self.dates[idx])[:10]
        snap_x = mpl_dates.date2num(self.dates[idx])
        for vl in self._ch_vlines: vl.set_xdata([snap_x, snap_x])
        if self._ch_hline: self._ch_hline.set_ydata([row["Close"], row["Close"]])
        rsi_v = f"{row['RSI']:.1f}" if "RSI" in row.index and row["RSI"] == row["RSI"] else "N/A"
        macd_v = f"{row['MACD']:.3f}" if "MACD" in row.index and row["MACD"] == row["MACD"] else "N/A"
        vol_f = f"{int(row['Volume']):,}" if row["Volume"] == row["Volume"] else "N/A"
        chg = row["Close"] - row["Open"]; chg_pct = chg / row["Open"] * 100 if row["Open"] else 0
        sign = "\u25b2" if chg >= 0 else "\u25bc"
        tip = (
            f" {date}\n O: ${row['Open']:.2f}  H: ${row['High']:.2f}\n"
            f" L: ${row['Low']:.2f}   C: ${row['Close']:.2f}\n"
            f" {sign} {chg:+.2f} ({chg_pct:+.2f}%)\n Vol: {vol_f}\n RSI: {rsi_v}  MACD: {macd_v}"
        )
        if self._ch_tooltip: self._ch_tooltip.set_text(tip)
        self.fig.canvas.draw_idle()

    def _on_scroll(self, event):
        if event.inaxes not in [a for a in [self.ax_price, self.ax_vol, self.ax_rsi, self.ax_macd] if a]:
            return
        zf = 0.12; span = self._view_end - self._view_start; delta = int(span * zf)
        try:
            md = mpl_dates.date2num(self.dates)
            idx = int(np.argmin(np.abs(md - event.xdata))) if event.xdata else (self._view_start + self._view_end)//2
        except: idx = (self._view_start + self._view_end)//2
        if event.button == "up":
            ns = max(0, self._view_start + delta); ne = min(self.n-1, self._view_end - delta)
        else:
            ns = max(0, self._view_start - delta); ne = min(self.n-1, self._view_end + delta)
        if ne - ns < self._min_window: return
        self._view_start, self._view_end = ns, ne
        self._apply_view()

    def _apply_view(self):
        vs, ve = self._view_start, self._view_end
        sub = self.df.iloc[vs:ve+1]
        if sub.empty: return
        lo, hi = sub["Low"].min(), sub["High"].max()
        pad = (hi - lo) * 0.05
        x0 = mpl_dates.date2num(sub.index[0]); x1 = mpl_dates.date2num(sub.index[-1])
        for ax in [a for a in [self.ax_price, self.ax_vol, self.ax_rsi, self.ax_macd] if a]:
            ax.set_xlim(x0, x1)
        self.ax_price.set_ylim(lo-pad, hi+pad)
        if self.ax_vol: self.ax_vol.set_ylim(0, sub["Volume"].max()*1.2)
        if self.ax_rsi: self.ax_rsi.set_ylim(0, 100)
        if self.ax_macd and "MACD" in self.df.columns:
            mr = max(abs(sub["MACD"].min()), abs(sub["MACD"].max())) * 1.3
            self.ax_macd.set_ylim(-mr, mr)
        self.fig.canvas.draw_idle()

    def _on_click(self, event):
        if event.inaxes != self.ax_price: return
        if self.measure_active:
            if event.button == 1:
                try:
                    md = mpl_dates.date2num(self.dates)
                    idx = int(np.argmin(np.abs(md - event.xdata)))
                    self.measure_start = (idx, event.ydata)
                except: pass
            return
        if event.button == 1:
            self.trendline_clicks.append((event.xdata, event.ydata))
            pt, = self.ax_price.plot(event.xdata, event.ydata, color="#ff00ff", marker="o", markersize=5, zorder=8)
            self.drawn_points.append(pt)
            if len(self.trendline_clicks) == 2:
                x0, y0 = self.trendline_clicks[0]; x1, y1 = self.trendline_clicks[1]
                if x1 != x0:
                    slope = (y1-y0)/(x1-x0); xlims = self.ax_price.get_xlim()
                    ln, = self.ax_price.plot([xlims[0], xlims[1]], [y0+slope*(xlims[0]-x0), y0+slope*(xlims[1]-x0)],
                                              color="#ff00ff", linewidth=1.5, zorder=7)
                else:
                    ln, = self.ax_price.plot([x0, x1], [y0, y1], color="#ff00ff", linewidth=1.5, zorder=7)
                self.drawn_lines.append(ln); self.trendline_clicks = []
            self.fig.canvas.draw_idle()
        elif event.button == 3:
            for ln in self.drawn_lines: ln.remove()
            for pt in self.drawn_points: pt.remove()
            self.drawn_lines, self.drawn_points, self.trendline_clicks = [], [], []
            self.fig.canvas.draw_idle()

    def _on_key(self, event):
        key = event.key.lower() if event.key else ""
        if key == "f": self._toggle_fib()
        elif key == "s": self._toggle_sr()
        elif key == "v": self._toggle_vp()
        elif key == "m":
            self.show_ma = not self.show_ma
            self._toggle_vis(self._ma_plots, self.show_ma)
            self._update_title()
        elif key == "b":
            self.show_bb = not self.show_bb
            self._toggle_vis(self._bb_plots, self.show_bb)
            self._update_title()
        elif key == "r":
            self._view_start, self._view_end = 0, self.n - 1
            self._apply_view()
        elif key == "x":
            fname = f"{self.ticker}_chart.png"
            self.fig.savefig(fname, dpi=150, bbox_inches="tight", facecolor="#0d0d1a")
            print(f"\n[Chart] Saved -> {fname}")

    def _toggle_fib(self):
        if self.show_fib:
            for ln in self.fib_lines:
                try: ln.remove()
                except: pass
            self.fib_lines = []; self.show_fib = False
        else:
            sub = self.df.iloc[self._view_start:self._view_end+1]
            levels = fibonacci_levels(sub["High"].max(), sub["Low"].min())
            colors = {"0.0%":"#ffffff","23.6%":"#ff9900","38.2%":"#ffcc00",
                      "50.0%":"#00ffcc","61.8%":"#00ff88","78.6%":"#ff6688","100.0%":"#ffffff"}
            for label, price in levels.items():
                ln = self.ax_price.axhline(y=price, color=colors.get(label,"#888888"), linewidth=0.9, linestyle=":", alpha=0.8)
                tx = self.ax_price.text(self.ax_price.get_xlim()[0], price, f"  Fib {label}  ${price:.2f}", fontsize=7,
                                         color=colors.get(label,"#888888"), va="bottom", alpha=0.9)
                self.fib_lines.extend([ln, tx])
            self.show_fib = True
        self.fig.canvas.draw_idle(); self._update_title()

    def _toggle_sr(self):
        if self.show_sr:
            for ln in self.sr_lines:
                try: ln.remove()
                except: pass
            self.sr_lines = []; self.show_sr = False
        else:
            resistances, supports = find_support_resistance(self.df)
            curr = self.df["Close"].iloc[-1]
            for level in resistances:
                clr = "#ff4466" if level > curr else "#ff9966"
                ln = self.ax_price.axhline(y=level, color=clr, linewidth=0.9, linestyle="--", alpha=0.75)
                tx = self.ax_price.text(self.ax_price.get_xlim()[1], level, f" R ${level:.2f}", fontsize=7,
                                         color=clr, va="center", ha="left", alpha=0.9)
                self.sr_lines.extend([ln, tx])
            for level in supports:
                clr = "#44ff88" if level < curr else "#88ffcc"
                ln = self.ax_price.axhline(y=level, color=clr, linewidth=0.9, linestyle="--", alpha=0.75)
                tx = self.ax_price.text(self.ax_price.get_xlim()[1], level, f" S ${level:.2f}", fontsize=7,
                                         color=clr, va="center", ha="left", alpha=0.9)
                self.sr_lines.extend([ln, tx])
            self.show_sr = True
        self.fig.canvas.draw_idle(); self._update_title()

    def _toggle_vp(self):
        if self.show_vp:
            for b in self.vp_bars:
                try: b.remove()
                except: pass
            self.vp_bars = []; self.show_vp = False
        else:
            sub = self.df.iloc[self._view_start:self._view_end+1]
            price_min, price_max = sub["Low"].min(), sub["High"].max()
            bins = 40; edges = np.linspace(price_min, price_max, bins+1)
            levels = 0.5 * (edges[:-1] + edges[1:]); vols = np.zeros(bins)
            for _, row in sub.iterrows():
                lo, hi, vol = row["Low"], row["High"], row["Volume"]
                for b in range(bins):
                    overlap = min(hi, edges[b+1]) - max(lo, edges[b])
                    if overlap > 0 and (hi-lo) > 0: vols[b] += vol * overlap / (hi-lo)
            max_vol = vols.max()
            if max_vol == 0: return
            xlims = self.ax_price.get_xlim()
            bar_max = (xlims[1]-xlims[0]) * 0.15
            poc = int(np.argmax(vols))
            for i, (price, vol) in enumerate(zip(levels, vols)):
                width = (vol/max_vol) * bar_max
                clr = "#ff9900" if i == poc else "#00aaff"
                bar = self.ax_price.barh(price, width, height=(levels[1]-levels[0])*0.85,
                                          left=xlims[1]-width, color=clr, alpha=0.35, zorder=2)
                self.vp_bars.append(bar)
            self.show_vp = True
        self.fig.canvas.draw_idle(); self._update_title()

    def _update_title(self):
        flags = []
        if self.show_ma: flags.append("MA")
        if self.show_bb: flags.append("BB")
        if self.show_fib: flags.append("FIB")
        if self.show_sr: flags.append("S/R")
        if self.show_vp: flags.append("VOL PROFILE")
        overlays = "  |  " + "  ".join(flags) if flags else ""
        hint = "[F]ib  [S]upport  [V]ol Profile  [M]A  [B]B  [R]eset  [X]Screenshot  Scroll=Zoom  L-Clickx2=Trendline  R-Click=Clear"
        self.ax_price.set_title(f"{self.ticker} - Advanced Interactive Chart{overlays}\n{hint}", fontsize=8, color="#cccccc", pad=6)
        self.fig.canvas.draw_idle()

    def _connect_events(self):
        self.fig.canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self.fig.canvas.mpl_connect("button_press_event", self._on_click)
        self.fig.canvas.mpl_connect("scroll_event", self._on_scroll)
        self.fig.canvas.mpl_connect("key_press_event", self._on_key)
