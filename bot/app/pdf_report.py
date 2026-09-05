"""PDF report generator — Persian backtest & swarm reports via fpdf2.

WebUI-grade layout: brand cover band, KPI summary cards, grouped metric
tables, styled equity/drawdown charts with benchmark & trade markers,
monthly-returns heatmap and a zebra trades table.

Persian text needs shaping (arabic_reshaper + python-bidi) because fpdf2
does not do complex text layout. Vazirmatn TTF is embedded.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Optional

from fpdf import FPDF
import arabic_reshaper
from bidi.algorithm import get_display

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")
FONT_REGULAR = os.path.join(ASSETS_DIR, "Vazirmatn-Regular.ttf")
FONT_BOLD = os.path.join(ASSETS_DIR, "Vazirmatn-Bold.ttf")

# ---------------------------------------------------------------------------
# Palette — matches the official WebUI light report theme
# ---------------------------------------------------------------------------
C_PRIMARY = (15, 18, 25)        # ink
C_MUTED = (100, 108, 122)       # gray text
C_FAINT = (148, 155, 166)       # faint text
C_ACCENT = (79, 70, 229)        # indigo 600 — brand
C_ACCENT_SOFT = (238, 240, 255) # indigo 50
C_GREEN = (5, 128, 76)          # success
C_GREEN_BG = (232, 247, 238)
C_RED = (200, 38, 45)           # danger
C_RED_BG = (253, 236, 236)
C_BLUE = (9, 105, 121)          # info
C_LINE = (226, 230, 236)        # border
C_ZEBRA = (248, 249, 252)       # row stripe
C_LIGHT = (245, 246, 250)


def fa(text) -> str:
    """Shape + bidi-reorder Persian text for fpdf2 rendering."""
    if text is None:
        return ""
    text = str(text)
    text = re.sub(
        r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u2190-\u21FF\u2500-\u257F\u2580-\u259F\u25A0-\u25FF]",
        "", text)
    try:
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def _fmt_pct(v, digits=1, signed=True):
    if v is None:
        return "—"
    return f"{v:+.{digits}%}" if signed else f"{v:.{digits}%}"


def _fmt_num(v, digits=2):
    if v is None:
        return "—"
    return f"{v:,.{digits}f}"


def _fmt_money(v):
    if v is None:
        return "—"
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:,.2f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:,.1f}K"
    return f"${v:,.0f}"


# ---------------------------------------------------------------------------
# PDF shell
# ---------------------------------------------------------------------------
MARGIN = 14


class PDFReport(FPDF):
    def __init__(self):
        super().__init__()
        self.add_font("Vazir", "", FONT_REGULAR)
        self.add_font("Vazir", "B", FONT_BOLD)
        self.set_auto_page_break(auto=True, margin=16)
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_title("Vibe-Trading Report")

    # -- chrome --------------------------------------------------------------
    def header(self):
        if self.page_no() == 1:
            return
        # slim brand line
        self.set_fill_color(*C_ACCENT)
        self.rect(0, 0, self.w, 2.2, style="F")
        self.set_y(7)
        # two explicit halves — date left, title right — so they can never overlap
        half = (self.w - 2 * MARGIN) / 2
        self.set_font("Vazir", "", 8)
        self.set_x(MARGIN)
        self.set_text_color(*C_FAINT)
        self.cell(half, 5, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), align="L")
        self.set_x(MARGIN + half)
        self.cell(half, 5, fa("گزارش بک‌تست — Vibe Trading"), align="R",
                  new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*C_LINE)
        self.set_line_width(0.2)
        self.line(MARGIN, 14, self.w - MARGIN, 14)
        self.set_y(19)

    def footer(self):
        self.set_y(-13)
        self.set_draw_color(*C_LINE)
        self.set_line_width(0.2)
        self.line(MARGIN, self.get_y(), self.w - MARGIN, self.get_y())
        self.set_y(-11)
        self.set_font("Vazir", "", 7.5)
        self.set_text_color(*C_FAINT)
        half = (self.w - 2 * MARGIN) / 2
        self.set_x(MARGIN)
        self.cell(half, 6, fa(f"صفحه {self.page_no()}"), align="L")
        self.set_x(MARGIN + half)
        self.cell(half, 6, fa("تولید شده توسط پلتفرم Vibe Trading"), align="R",
                  new_x="LMARGIN", new_y="NEXT")

    # -- building blocks -----------------------------------------------------
    def cover_band(self, title: str, subtitle: str = "", status: str = "موفق"):
        """Full-width branded cover band with big title + status chip."""
        h = 40
        self.set_fill_color(*C_PRIMARY)
        self.rect(0, 0, self.w, h, style="F")
        # accent stripe
        self.set_fill_color(*C_ACCENT)
        self.rect(0, 0, self.w, 2.6, style="F")
        # status chip
        chip_w = 22
        self.set_fill_color(30, 36, 50)
        self.set_draw_color(60, 70, 92)
        self.set_line_width(0.2)
        xx = self.w - MARGIN - chip_w
        self.rect(xx, 8, chip_w, 8.5, style="DF", round_corners=True, corner_radius=3)
        self.set_xy(xx, 8.6)
        self.set_font("Vazir", "B", 8.5)
        self.set_text_color(140, 220, 170)
        self.cell(chip_w, 7.5, fa(status), align="C")
        # title
        self.set_xy(MARGIN, 10)
        self.set_font("Vazir", "B", 19)
        self.set_text_color(255, 255, 255)
        self.cell(self.w - 2 * MARGIN - chip_w - 4, 11, fa(title), align="R")
        if subtitle:
            self.set_xy(MARGIN, 23)
            self.set_font("Vazir", "", 9.5)
            self.set_text_color(168, 178, 194)
            self.cell(self.w - 2 * MARGIN, 7, fa(subtitle), align="R")
        self.set_y(h + 7)

    def section(self, title: str, num: str = ""):
        self.ln(3)
        y = self.get_y()
        self.set_fill_color(*C_ACCENT)
        self.rect(MARGIN, y + 1.2, 1.8, 6.2, style="F", round_corners=True, corner_radius=0.9)
        self.set_xy(MARGIN + 4.5, y)
        self.set_font("Vazir", "B", 12)
        self.set_text_color(*C_PRIMARY)
        label = f"{num}. {title}" if num else title
        self.cell(0, 8.5, fa(label), align="R", new_x="LMARGIN", new_y="NEXT")
        self.ln(0.5)

    def kpi_cards(self, cards: list[tuple[str, str, Optional[tuple], Optional[tuple]]], per_row: int = 4):
        """cards: (label, value, value_color, bg_color). RTL horizontal cards:
        label on the RIGHT, value to its LEFT."""
        gap = 3.2
        total_w = self.w - 2 * MARGIN
        card_w = (total_w - gap * (per_row - 1)) / per_row
        card_h = 13
        y0 = self.get_y()
        for i, (label, value, vcol, bg) in enumerate(cards):
            row, col = divmod(i, per_row)
            x = MARGIN + col * (card_w + gap)
            y = y0 + row * (card_h + gap)
            if y + card_h > self.h - 18:  # page-break guard
                self.add_page()
                y0 = self.get_y()
                y = y0
            self.set_fill_color(*(bg or (252, 252, 254)))
            self.set_draw_color(*C_LINE)
            self.set_line_width(0.2)
            self.rect(x, y, card_w, card_h, style="DF", round_corners=True, corner_radius=2.4)
            mid = y + card_h / 2
            # label — right zone, right-aligned
            self.set_xy(x + card_w * 0.42, mid - 3.2)
            self.set_font("Vazir", "", 8.5)
            self.set_text_color(*C_MUTED)
            self.cell(card_w * 0.55, 6.4, fa(label), align="R")
            # value — left zone, centred in its own lane
            self.set_xy(x + 2.5, mid - 3.2)
            self.set_font("Vazir", "B", 11.5)
            self.set_text_color(*(vcol or C_PRIMARY))
            self.cell(card_w * 0.40, 6.4, fa(str(value)), align="C")
        rows = (len(cards) + per_row - 1) // per_row
        self.set_y(y0 + rows * (card_h + gap) + 2)

    def metric_table(self, rows: list[tuple[str, str, Optional[tuple]]]):
        """Single-column key/value rows, full width: value zone (left) + label (right).

        One metric per row — a value can never collide with a neighbour column.
        """
        if not rows:
            return
        row_h = 8.0
        total_w = self.w - 2 * MARGIN
        vw = 52.0  # fixed value zone
        for key, value, vcol in rows:
            if self.get_y() + row_h > self.h - 18:
                self.add_page()
            y = self.get_y()
            x = self.l_margin
            self.set_draw_color(*C_LINE)
            self.set_line_width(0.2)
            self.set_fill_color(*C_ZEBRA)
            self.rect(x, y, total_w, row_h, style="D")
            # value — centred in its own fixed lane at the left
            self.set_xy(x + 3, y + 1.3)
            self.set_font("Vazir", "B", 10)
            self.set_text_color(*(vcol or C_PRIMARY))
            self.cell(vw, 5.4, fa(str(value)), align="C")
            # label — right side, remaining width
            self.set_xy(x + 3 + vw + 2, y + 1.3)
            self.set_font("Vazir", "", 9.5)
            self.set_text_color(*C_MUTED)
            self.cell(total_w - vw - 5 - 2, 5.4, fa(key), align="R")
            self.set_y(y + row_h)

    def _metric_cell(self, w: float, row: tuple[str, str, Optional[tuple]], left: bool = False):
        # Kept for backward compatibility; metric_table no longer uses it.
        return

    def info_strip(self, lines: list[tuple[str, str]]):
        """Fixed-grid meta strip — every item owns a fixed cell, no stacking, no overlap."""
        cols = 2
        total_w = self.w - 2 * MARGIN
        cell_w = total_w / cols
        row_h = 8.2
        rows = (len(lines) + cols - 1) // cols
        y0 = self.get_y()
        self.set_fill_color(*C_LIGHT)
        self.set_draw_color(*C_LINE)
        self.set_line_width(0.2)
        self.rect(MARGIN, y0, total_w, rows * row_h, style="DF", round_corners=True, corner_radius=1.8)
        for i, (k, v) in enumerate(lines):
            row, col = divmod(i, cols)
            # RTL: col 0 renders at the right side; label RIGHT, value LEFT
            x = self.w - MARGIN - (col + 1) * cell_w
            y = y0 + row * row_h
            # value lane at the left of the cell
            self.set_xy(x + 3, y + 1.5)
            self.set_font("Vazir", "B", 8.5)
            self.set_text_color(*C_PRIMARY)
            self.cell(cell_w * 0.45, 5.4, fa(str(v))[:30], align="C")
            # label to the right of the value
            self.set_xy(x + 3 + cell_w * 0.45, y + 1.5)
            self.set_font("Vazir", "", 8)
            self.set_text_color(*C_FAINT)
            self.cell(cell_w * 0.55 - 3, 5.4, fa(k + ":"), align="R")
        self.set_y(y0 + rows * row_h + 4)

    def body(self, text: str, size: float = 10):
        self.set_font("Vazir", "", size)
        self.set_text_color(*C_PRIMARY)
        self.set_x(self.l_margin)
        def _wrap(tok: str, limit: int = 48) -> str:
            return " ".join(tok[i:i + limit] for i in range(0, len(tok), limit)) if len(tok) > limit else tok
        text = " ".join(_wrap(tok) for tok in text.split())
        try:
            self.multi_cell(0, 6, fa(text), align="R")
        except Exception:
            chunk = fa(text)
            for i in range(0, len(chunk), 900):
                self.set_x(self.l_margin)
                try:
                    self.multi_cell(0, 6, chunk[i:i + 900], align="R")
                except Exception:
                    break

    def trades_table(self, trade_log: list[dict], max_rows: int = 40):
        if not trade_log:
            return
        show = trade_log[:max_rows]
        # RTL table — explicit right-anchored x positions:
        # تاریخ | سمت | قیمت | بازده | سود/زیان | دلیل (right → left)
        total_w = self.w - 2 * MARGIN
        col_ws = [24, 13, 26, 18, 26, 0]  # date..reason; reason takes the rest
        col_ws[-1] = total_w - sum(col_ws[:-1])
        # x of each column's LEFT edge (fpdf draws from x leftward→rightward)
        right_edge = self.w - MARGIN
        col_x = {}
        acc = right_edge
        keys = ["date", "side", "price", "ret", "pnl", "reason"]
        for w, k in zip(col_ws, keys):
            col_x[k] = acc - w
            acc -= w
        order = ["date", "side", "price", "ret", "pnl", "reason"]  # visual right→left
        self.set_fill_color(*C_PRIMARY)
        self.set_text_color(255, 255, 255)
        self.set_font("Vazir", "B", 8.5)
        self.set_draw_color(*C_PRIMARY)
        self.set_line_width(0.2)
        row_h = 7.5
        y = self.get_y()
        headers = {"date": "تاریخ", "side": "سمت", "price": "قیمت", "ret": "بازده", "pnl": "سود/زیان", "reason": "دلیل"}
        for k in order:
            self.set_xy(col_x[k], y)
            self.cell(col_ws[keys.index(k)], row_h, fa(headers[k]), border=1, fill=True, align="C")
        self.set_y(y + row_h)
        # rows
        for idx, t in enumerate(show):
            if self.get_y() > self.h - 24:
                self.add_page()
            side = str(t.get("side", "?")).lower()
            try:
                price = float(t.get("price", 0) or 0)
                ret = float(t.get("return_pct", 0) or 0)
                pnl = float(t.get("pnl", 0) or 0)
            except (TypeError, ValueError):
                continue
            fill = idx % 2 == 1
            y = self.get_y()
            self.set_draw_color(*C_LINE)
            values = {
                "date": (str(t.get("timestamp", ""))[:10], C_PRIMARY, ""),
                "side": ("خرید" if side == "buy" else "فروش", C_GREEN if side == "buy" else C_RED, "B"),
                "price": (f"{price:,.0f}", C_PRIMARY, ""),
                "ret": (f"{ret:+.1f}%", C_GREEN if ret >= 0 else C_RED, "B"),
                "pnl": (f"{pnl:+,.0f}", C_GREEN if pnl >= 0 else C_RED, ""),
                "reason": (str(t.get("reason", ""))[:16], C_MUTED, ""),
            }
            for k in order:
                val, col, weight = values[k]
                w = col_ws[keys.index(k)]
                self.set_xy(col_x[k], y)
                self.set_fill_color(*C_ZEBRA)
                self.set_text_color(*col)
                self.set_font("Vazir", weight, 8 if k != "reason" else 7.5)
                self.cell(w, 6.6, fa(val), border=1, fill=fill, align="C")
            self.set_y(y + 6.6)
        # footnote
        if len(trade_log) > max_rows:
            self.ln(1.5)
            self.set_font("Vazir", "", 7.5)
            self.set_text_color(*C_FAINT)
            self.cell(0, 5, fa(f"نمایش {len(show)} از {len(trade_log)} معامله"), align="C",
                      new_x="LMARGIN", new_y="NEXT")


# ---------------------------------------------------------------------------
# Charts (matplotlib)
# ---------------------------------------------------------------------------
def _parse_equity(equity):
    dates, values = [], []
    for p in equity or []:
        if isinstance(p, dict):
            v = p.get("equity") or p.get("value") or p.get("close")
            d = p.get("time") or p.get("date") or p.get("timestamp", "")
            if v is not None:
                try:
                    values.append(float(v))
                    dates.append(str(d)[:10])
                except (TypeError, ValueError):
                    pass
        elif isinstance(p, (int, float)):
            values.append(float(p))
            dates.append("")
    return dates, values


def _charts_png(equity, metrics: dict, benchmark=None, trade_markers=None) -> Optional[bytes]:
    """Render (equity+benchmark+markers, drawdown) as one tall PNG."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from datetime import datetime as _dt

        values, labels = [], []
        for p in equity or []:
            if isinstance(p, dict):
                v = p.get("equity") or p.get("value") or p.get("close")
                d = p.get("time") or p.get("date") or p.get("timestamp", "")
                if v is not None:
                    try:
                        values.append(float(v))
                        labels.append(str(d)[:10])
                    except (TypeError, ValueError):
                        pass
        if len(values) < 4:
            return None

        dates = []
        for lab in labels:
            try:
                dates.append(_dt.strptime(lab, "%Y-%m-%d"))
            except Exception:
                dates.append(None)
        if not any(dates):
            dates = list(range(len(values)))

        if len(values) > 700:
            step = len(values) // 700
            idx = list(range(0, len(values), step))
            values = [values[i] for i in idx]
            dates = [dates[i] for i in idx]

        # benchmark normalized
        bm_values, bm_dates = [], []
        if benchmark and isinstance(benchmark, list) and len(benchmark) > 4:
            for point in benchmark:
                if isinstance(point, dict):
                    v = point.get("close") or point.get("price") or point.get("value")
                    d = point.get("time") or point.get("date") or point.get("timestamp")
                    if v is not None:
                        try:
                            bm_values.append(float(v))
                            try:
                                bm_dates.append(_dt.strptime(str(d)[:10], "%Y-%m-%d"))
                            except Exception:
                                bm_dates.append(None)
                        except (TypeError, ValueError):
                            pass
            if bm_values:
                base = bm_values[0]
                scale = values[0] / base if base else 1
                bm_values = [v * scale for v in bm_values]

        # style — large fonts: the figure is ~10.5in wide but printed at
        # ~7.2in page width, so everything must be oversized to stay legible
        plt.rcParams.update({
            "font.size": 10.5, "axes.edgecolor": "#d8dce3", "axes.linewidth": 0.9,
            "xtick.color": "#565d68", "ytick.color": "#565d68",
            "grid.color": "#e8ebf0", "grid.linewidth": 0.7,
        })
        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(10.5, 7.6), sharex=True,
            gridspec_kw={"height_ratios": [3, 1.3], "hspace": 0.10}, layout="constrained",
        )
        fig.patch.set_facecolor("#ffffff")
        for ax in (ax1, ax2):
            ax.set_facecolor("#ffffff")
            ax.grid(True, alpha=0.6)
            for s in ("top", "right"):
                ax.spines[s].set_visible(False)

        start_val = values[0]
        ax1.plot(dates, values, color="#4f46e5", linewidth=2.2, label="Strategy", solid_capstyle="round", zorder=3)
        ax1.fill_between(dates, values, start_val,
                         where=[v >= start_val for v in values], color="#4f46e5", alpha=0.07, interpolate=True)
        ax1.fill_between(dates, values, start_val,
                         where=[v < start_val for v in values], color="#c8262d", alpha=0.06, interpolate=True)
        ax1.axhline(start_val, color="#b7bdc9", linewidth=0.9, linestyle=":", zorder=1)
        if bm_values and len(bm_values) == len(bm_dates):
            ax1.plot(bm_dates, bm_values, color="#0aa2a8", linewidth=1.6, alpha=0.9,
                     linestyle="--", label="Buy & Hold", zorder=2)
        ax1.set_ylabel("Equity", color="#565d68", fontsize=11, fontweight="bold")
        ax1.yaxis.set_major_formatter(lambda x, _: f"{x / 1000:,.0f}K" if abs(x) >= 1000 else f"{x:,.0f}")
        ax1.tick_params(axis="both", labelsize=10)
        # stats title — so the chart is interpretable on its own
        _tr = metrics.get("total_return")
        _br = metrics.get("benchmark_return")
        _sh = metrics.get("sharpe")
        _ttl = f"Return: {_tr:+.1%}" if _tr is not None else "Return: n/a"
        if _br is not None:
            _ttl += f"   |   B&H: {_br:+.1%}   |   Excess: {metrics.get('excess_return', 0):+.1%}"
        if _sh is not None:
            _ttl += f"   |   Sharpe: {_sh:.2f}"
        ax1.set_title(_ttl, color="#1a1d24", fontsize=12, fontweight="bold", loc="left", pad=10)

        # trade markers on price axis
        if trade_markers:
            buys_x, buys_y, sells_x, sells_y = [], [], [], []
            for tmk in trade_markers:
                side = str(tmk.get("side", "")).lower()
                # engine markers use "time"; be tolerant of both keys
                ts = str(tmk.get("timestamp") or tmk.get("time") or "")[:10]
                try:
                    px = float(tmk.get("price"))
                    dx = _dt.strptime(ts, "%Y-%m-%d")
                except (TypeError, ValueError):
                    continue
                (buys_x if side == "buy" else sells_x).append(dx)
                (buys_y if side == "buy" else sells_y).append(px)
            if buys_x or sells_x:
                ax1b = ax1.twinx()
                ax1b.set_facecolor("none")
                all_px = buys_y + sells_y
                px_lo, px_hi = (min(all_px), max(all_px)) if all_px else (0, 1)
                span = (px_hi - px_lo) or 1
                ax1b.set_ylim(px_lo - span * 0.25, px_hi + span * 0.25)
                ax1b.tick_params(colors="#8a919e", labelsize=9)
                for s in ("top", "left"):
                    ax1b.spines[s].set_visible(False)
                ax1b.spines["right"].set_color("#d8dce3")
                ax1b.set_ylabel("Price", color="#8a919e", fontsize=10, fontweight="bold")
                if buys_x:
                    ax1b.scatter(buys_x, buys_y, marker="^", color="#0a8541", s=64, zorder=5,
                                 edgecolors="white", linewidths=0.8, label="Buy")
                if sells_x:
                    ax1b.scatter(sells_x, sells_y, marker="v", color="#c8262d", s=64, zorder=5,
                                 edgecolors="white", linewidths=0.8, label="Sell")
                ax1b.legend(loc="upper left", fontsize=9, frameon=True, facecolor="white", edgecolor="#d8dce3")

        ax1.legend(loc="lower left", fontsize=9.5, frameon=True, facecolor="white", edgecolor="#d8dce3")

        # drawdown
        peak = values[0]
        dd = []
        for v in values:
            peak = max(peak, v)
            dd.append((v - peak) / peak if peak else 0)
        ax2.fill_between(dates, dd, 0, color="#c8262d", alpha=0.25, interpolate=True)
        ax2.plot(dates, dd, color="#c8262d", linewidth=1.4)
        ax2.set_ylabel("Drawdown", color="#565d68", fontsize=11, fontweight="bold")
        ax2.tick_params(axis="both", labelsize=10)
        ax2.yaxis.set_major_formatter(lambda x, _: f"{x:.0%}")
        mdd = metrics.get("max_drawdown")
        if mdd is not None and dd:
            ax2.annotate(f"Max DD {mdd:.1%}", xy=(dates[dd.index(min(dd))], min(dd)),
                         xytext=(8, 10), textcoords="offset points",
                         color="#ffffff", fontsize=10, fontweight="bold",
                         bbox=dict(boxstyle="round,pad=0.3", fc="#c8262d", ec="none"))
        if isinstance(dates[0], _dt):
            ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            ax2.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))
            for ax in (ax1, ax2):
                ax.tick_params(axis="x", labelsize=10)

        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        fig.savefig(path, dpi=140, facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close(fig)
        with open(path, "rb") as f:
            data = f.read()
        os.unlink(path)
        return data
    except Exception:
        import traceback
        traceback.print_exc()
        return None


def _monthly_heatmap_png(equity) -> Optional[bytes]:
    """Monthly returns heatmap (year x month) — the signature WebUI visual."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        from datetime import datetime as _dt
        from collections import OrderedDict

        # group equity by month
        monthly = OrderedDict()
        last_val_by_month = OrderedDict()
        prev_end = None
        for p in equity or []:
            if not isinstance(p, dict):
                continue
            v = p.get("equity") or p.get("value") or p.get("close")
            d = str(p.get("time") or p.get("date") or p.get("timestamp", ""))[:7]  # YYYY-MM
            if v is None or len(d) != 7:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            monthly.setdefault(d, [])
            monthly[d].append(v)
        if len(monthly) < 2:
            return None

        rets = {}
        prev_last = None
        for month, vals in monthly.items():
            last = vals[-1]
            first = vals[0]
            base = prev_last if prev_last is not None else first
            if base:
                rets[month] = (last - base) / base
            prev_last = last
        if len(rets) < 2:
            return None

        years = sorted({m[:4] for m in rets})
        # NOTE: matplotlib's default font has no Arabic glyphs, so month
        # labels must be Latin — Persian names would render as tofu boxes.
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        grid = np.full((12, len(years)), np.nan)
        for m, r in rets.items():
            yi = years.index(m[:4])
            grid[int(m[5:7]) - 1, yi] = r

        # Skip near-empty grids (e.g. a 2-month backtest) — they look broken.
        filled = int(np.count_nonzero(~np.isnan(grid)))
        if filled < 3:
            return None

        fig, ax = plt.subplots(figsize=(10.5, 0.5 * 12 + 1.2), layout="constrained")
        fig.patch.set_facecolor("#ffffff")
        masked = np.ma.masked_invalid(grid)
        cmap = matplotlib.colors.LinearSegmentedColormap.from_list(
            "rg", ["#c8262d", "#e8746f", "#f6d5d3", "#f7f7f8", "#cdeeda", "#69bf7f", "#0a8541"])
        vmax = max(0.08, float(np.nanmax(np.abs(grid))) * 0.9)
        ax.imshow(masked, cmap=cmap, vmin=-vmax, vmax=vmax, aspect="auto")
        ax.set_xticks(range(len(years)))
        ax.set_xticklabels(years, fontsize=11, color="#565d68", fontweight="bold")
        ax.set_yticks(range(12))
        ax.set_yticklabels(month_names, fontsize=10.5, color="#3a3f4a")
        ax.tick_params(length=0)
        for s in ax.spines.values():
            s.set_visible(False)
        for yi in range(len(years)):
            for mi in range(12):
                val = grid[mi, yi]
                if not np.isnan(val):
                    ax.text(yi, mi, f"{val:+.1%}", ha="center", va="center",
                            fontsize=10, fontweight="bold",
                            color="#ffffff" if abs(val) > vmax * 0.55 else "#333a46")
        # subtle cell borders
        ax.set_xticks(np.arange(-0.5, len(years), 1), minor=True)
        ax.set_yticks(np.arange(-0.5, 12, 1), minor=True)
        ax.grid(which="minor", color="#ffffff", linewidth=2)
        ax.tick_params(which="minor", length=0)

        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        fig.savefig(path, dpi=140, facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close(fig)
        with open(path, "rb") as f:
            data = f.read()
        os.unlink(path)
        return data
    except Exception:
        import traceback
        traceback.print_exc()
        return None


# ============================================================================
# Public API
# ============================================================================

def build_backtest_pdf(detail: dict) -> bytes:
    """Build the full backtest report PDF from an engine run detail dict."""
    metrics = detail.get("metrics") or {}
    rx = detail.get("risk_xray") or {}
    ctx = detail.get("run_context") or {}
    trade_log = detail.get("trade_log") or []
    equity = detail.get("equity_curve") or []
    price_series = detail.get("price_series") or {}
    trade_markers = detail.get("trade_markers") or []

    tr = metrics.get("total_return")
    pdf = PDFReport()
    pdf.add_page()

    prompt = str(detail.get("prompt", "بک‌تست"))[:90]
    status = "موفق" if detail.get("status") == "success" else str(detail.get("status", ""))
    pdf.cover_band("گزارش بک‌تست", prompt, status)

    # --- meta strip ---------------------------------------------------------
    codes = ctx.get("codes") or []
    if not codes and trade_markers:
        seen = []
        for tm in trade_markers:
            c = tm.get("code") or tm.get("symbol")
            if c and c not in seen:
                seen.append(c)
        codes = seen

    start_d = ctx.get("start_date")
    end_d = ctx.get("end_date")
    if not start_d and equity and isinstance(equity[0], dict):
        start_d = str(equity[0].get("time") or equity[0].get("date") or "")[:10]
    if not end_d and equity and isinstance(equity[-1], dict):
        end_d = str(equity[-1].get("time") or equity[-1].get("date") or "")[:10]

    pdf.info_strip([
        ("دارایی", ", ".join(str(c) for c in codes[:4]) or "بک‌تست استراتژی"),
        ("بازه", f"{start_d or '—'} تا {end_d or '—'}"),
        ("شناسه", str(detail.get("run_id", "—"))[:22]),
        ("تاریخ", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
    ])

    # --- KPI cards -----------------------------------------------------------
    pdf.section("خلاصه عملکرد", "۱")
    tr_color = C_GREEN if (tr or 0) >= 0 else C_RED
    sharpe = metrics.get("sharpe")
    mdd = metrics.get("max_drawdown")
    wr = metrics.get("win_rate")
    pdf.kpi_cards([
        ("بازده کل", _fmt_pct(tr), tr_color, C_GREEN_BG if (tr or 0) >= 0 else C_RED_BG),
        ("بازده سالانه", _fmt_pct(metrics.get("annual_return")),
         C_GREEN if (metrics.get("annual_return") or 0) >= 0 else C_RED, None),
        ("نسبت شارپ", _fmt_num(sharpe), C_ACCENT, C_ACCENT_SOFT),
        ("حداکثر افت", _fmt_pct(mdd), C_RED, C_RED_BG),
        ("ارزش نهایی", _fmt_money(metrics.get("final_value")), None, None),
        ("نرخ برد", _fmt_pct(wr, signed=False), C_GREEN if (wr or 0) >= 0.5 else C_MUTED, None),
        ("تعداد معاملات", str(int(metrics.get("trade_count", 0) or 0)), None, None),
        ("پروفیت فاکتور", _fmt_num(metrics.get("profit_factor")), None, None),
    ])

    # --- returns & benchmark --------------------------------------------------
    pdf.section("بازده و بنچمارک", "۲")
    rows = []
    if metrics.get("benchmark_return") is not None:
        br = metrics["benchmark_return"]
        rows.append(("خرید و نگهداری", _fmt_pct(br), C_GREEN if br >= 0 else C_RED))
    if metrics.get("excess_return") is not None:
        ex = metrics["excess_return"]
        rows.append(("بازده مازاد", _fmt_pct(ex), C_GREEN if ex >= 0 else C_RED))
    if metrics.get("information_ratio") is not None:
        rows.append(("نسبت اطلاعات", _fmt_num(metrics["information_ratio"]), None))
    if metrics.get("tracking_error") is not None:
        rows.append(("خطای ردیابی", _fmt_pct(metrics["tracking_error"]), None))
    if metrics.get("benchmark_beta") is not None:
        rows.append(("بتا نسبت به بنچمارک", _fmt_num(metrics["benchmark_beta"]), None))
    pdf.metric_table(rows)

    # --- risk ---------------------------------------------------------------
    pdf.section("ریسک", "۳")
    vol = (rx.get("volatility") or {}).get("annualized_vol") or metrics.get("risk_xray_annualized_vol")
    risk_rows = []
    if metrics.get("sortino") is not None:
        risk_rows.append(("نسبت سورتینو", _fmt_num(metrics["sortino"]), None))
    if metrics.get("calmar") is not None:
        risk_rows.append(("نسبت کالمار", _fmt_num(metrics["calmar"]), None))
    if vol is not None:
        risk_rows.append(("نوسان سالانه", _fmt_pct(vol, signed=False), None))
    var95 = (rx.get("tail_risk") or {}).get("var_95")
    if var95 is not None:
        risk_rows.append(("VaR روزانه ۹۵٪", _fmt_pct(var95, signed=False), C_RED))
    if metrics.get("max_consecutive_loss") is not None:
        risk_rows.append(("بیشترین ضرر متوالی", str(int(metrics["max_consecutive_loss"])), None))
    pdf.metric_table(risk_rows)

    # --- activity -------------------------------------------------------------
    pdf.section("فعالیت معاملاتی", "۴")
    act_rows = []
    if metrics.get("avg_holding_days") is not None:
        act_rows.append(("میانگین مدت نگهداری", f"{metrics['avg_holding_days']:.1f} روز", None))
    if metrics.get("total_turnover") is not None:
        act_rows.append(("گردش کل", f"{metrics['total_turnover']:,.1f}", None))
    if metrics.get("rebalance_count") is not None:
        act_rows.append(("تعداد بازتوازن", str(int(metrics["rebalance_count"])), None))
    if metrics.get("avg_turnover") is not None:
        act_rows.append(("میانگین گردش هر بازتوازن", f"{metrics['avg_turnover']:,.3f}", None))
    pdf.metric_table(act_rows)

    # --- performance charts -----------------------------------------------------
    benchmark = None
    if isinstance(price_series, dict) and price_series:
        first = next(iter(price_series.values()))
        if isinstance(first, list):
            benchmark = first
    elif isinstance(price_series, list):
        benchmark = price_series

    chart = _charts_png(equity, metrics, benchmark, trade_markers)
    if chart:
        pdf.add_page()
        pdf.section("نمودار عملکرد و افت سرمایه", "۵")
        x_w = pdf.w - pdf.l_margin - pdf.r_margin
        pdf.image(io.BytesIO(chart), x=pdf.l_margin, w=x_w)

    heat = _monthly_heatmap_png(equity)
    if heat:
        if pdf.get_y() > pdf.h - 120:
            pdf.add_page()
        pdf.section("بازده ماهانه", "۶")
        x_w = pdf.w - pdf.l_margin - pdf.r_margin
        pdf.image(io.BytesIO(heat), x=pdf.l_margin, w=x_w)

    # --- trades -----------------------------------------------------------------
    if trade_log:
        pdf.add_page()
        wins = sum(1 for t in trade_log if float(t.get("return_pct", 0) or 0) > 0)
        losses = len(trade_log) - wins
        pdf.section(f"معاملات ({len(trade_log)} مورد)", "۷")
        pdf.info_strip([
            ("سودده", str(wins)),
            ("زیان‌ده", str(losses)),
        ])
        pdf.trades_table(trade_log)

    return bytes(pdf.output())


def build_swarm_pdf(preset_name: str, preset_title: str, report: str,
                    tasks: list[dict] | None = None) -> bytes:
    """Build the swarm analysis report PDF."""
    pdf = PDFReport()
    pdf.add_page()

    pdf.cover_band("گزارش تحلیل تیمی", preset_title or preset_name, "گزارش")

    pdf.info_strip([
        ("تیم", preset_title or preset_name),
        ("ایجنت‌ها", str(len(tasks or []))),
        ("تاریخ", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
    ])

    if tasks:
        pdf.section("وضعیت ایجنت‌ها", "۱")
        status_fa = {"completed": "تکمیل شده", "in_progress": "در حال اجرا",
                     "blocked": "متوقف", "failed": "ناموفق"}
        done = sum(1 for t in tasks if t.get("status") == "completed")
        pdf.kpi_cards([
            ("تکمیل شده", fa(f"{done}/{len(tasks)}"), C_GREEN, C_GREEN_BG),
            ("ایجنت‌ها", str(len(tasks)), None, None),
        ], per_row=2)
        pdf.ln(2)
        for t in tasks:
            name = t.get("agent_name", "?")
            s = t.get("status", "?")
            color = C_GREEN if s == "completed" else C_RED if s == "failed" else C_PRIMARY
            pdf.set_font("Vazir", "", 10)
            pdf.set_text_color(*color)
            pdf.cell(0, 6.8, fa(f"• {name} — {status_fa.get(s, s)}"),
                     align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.section("گزارش نهایی", "۲")
    for para in (report or "").split("\n"):
        para = para.strip()
        if para:
            pdf.body(para, size=10)
        else:
            pdf.ln(2)

    return bytes(pdf.output())
