"""PDF report generator — Persian backtest & swarm reports via fpdf2.

Persian text needs shaping (arabic_reshaper + python-bidi) because fpdf2
does not do complex text layout. Vazirmatn TTF is embedded.
"""

from __future__ import annotations

import io
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

from fpdf import FPDF
import arabic_reshaper
from bidi.algorithm import get_display

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")
FONT_REGULAR = os.path.join(ASSETS_DIR, "Vazirmatn-Regular.ttf")
FONT_BOLD = os.path.join(ASSETS_DIR, "Vazirmatn-Bold.ttf")

# Colors (dark-on-light professional theme)
C_PRIMARY = (13, 17, 23)      # near-black
C_ACCENT = (46, 160, 67)      # green
C_RED = (248, 81, 73)         # red
C_BLUE = (88, 166, 255)       # blue
C_GRAY = (110, 119, 129)
C_LIGHT = (246, 248, 250)


def fa(text) -> str:
    """Shape + bidi-reorder Persian text for fpdf2 rendering."""
    if text is None:
        return ""
    text = str(text)
    # Strip emoji/symbols the embedded font lacks (fpdf2 would render garbage boxes)
    text = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]", "", text)
    try:
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


class PDFReport(FPDF):
    def __init__(self):
        super().__init__()
        self.add_font("Vazir", "", FONT_REGULAR)
        self.add_font("Vazir", "B", FONT_BOLD)
        self.set_auto_page_break(auto=True, margin=16)
        self._accent = C_ACCENT

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Vazir", "", 8)
        self.set_text_color(*C_GRAY)
        self.cell(0, 6, fa("Vibe-Trading SaaS — گزارش"), align="L", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-14)
        self.set_font("Vazir", "", 8)
        self.set_text_color(*C_GRAY)
        self.cell(0, 8, fa(f"صفحه {self.page_no()}"), align="C")

    # -- building blocks -----------------------------------------------------

    def cover_bar(self, title: str, subtitle: str = ""):
        self.set_fill_color(*C_PRIMARY)
        self.rect(0, 0, self.w, 34, style="F")
        self.set_xy(12, 8)
        self.set_font("Vazir", "B", 17)
        self.set_text_color(255, 255, 255)
        self.cell(0, 10, fa(title), align="R")
        if subtitle:
            self.set_xy(12, 20)
            self.set_font("Vazir", "", 10)
            self.set_text_color(180, 190, 200)
            self.cell(0, 8, fa(subtitle), align="R")
        self.ln(24)

    def section(self, title: str):
        self.ln(2)
        self.set_font("Vazir", "B", 12.5)
        self.set_text_color(*C_PRIMARY)
        self.set_fill_color(*C_LIGHT)
        self.set_draw_color(*C_ACCENT)
        self.set_line_width(0.8)
        y = self.get_y()
        self.line(12, y + 1, 15, y + 5)  # small accent tick
        self.cell(0, 8, fa(title), align="R", new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def kv_row(self, key: str, value: str, value_color=None):
        self.set_font("Vazir", "", 10.5)
        self.set_text_color(*C_GRAY)
        self.cell(90, 7, fa(key), align="R")
        self.set_font("Vazir", "B", 10.5)
        self.set_text_color(*(value_color or C_PRIMARY))
        self.cell(0, 7, fa(value), align="L", new_x="LMARGIN", new_y="NEXT")

    def body(self, text: str, size: float = 10.5):
        self.set_font("Vazir", "", size)
        self.set_text_color(*C_PRIMARY)
        self.multi_cell(0, 6.2, fa(text), align="R")

    def info_box(self, lines: list[tuple[str, str]], color=C_PRIMARY):
        self.set_fill_color(*C_LIGHT)
        self.set_draw_color(*C_GRAY)
        x0, y0 = self.l_margin, self.get_y()
        row_h = 7.5
        box_h = 6 + row_h * len(lines)
        self.rect(x0, y0, self.w - self.r_margin - self.l_margin, box_h, style="DF")
        self.set_xy(x0 + 3, y0 + 3)
        for k, v in lines:
            self.set_font("Vazir", "", 10)
            self.set_text_color(*C_GRAY)
            self.cell(85, row_h, fa(k), align="R")
            self.set_font("Vazir", "B", 10)
            self.set_text_color(*color)
            self.cell(0, row_h, fa(v), align="L", new_x="LMARGIN", new_y="NEXT")
        self.set_y(y0 + box_h + 4)

    def trades_table(self, trade_log: list[dict], max_rows: int = 30):
        if not trade_log:
            return
        self.set_font("Vazir", "B", 9.5)
        self.set_fill_color(*C_PRIMARY)
        self.set_text_color(255, 255, 255)
        self.cell(26, 8, fa("تاریخ"), border=1, fill=True, align="C")
        self.cell(14, 8, fa("سمت"), border=1, fill=True, align="C")
        self.cell(30, 8, fa("قیمت"), border=1, fill=True, align="C")
        self.cell(22, 8, fa("بازده"), border=1, fill=True, align="C")
        self.cell(30, 8, fa("سود/زیان"), border=1, fill=True, align="C")
        self.cell(0, 8, fa("دلیل"), border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")

        self.set_font("Vazir", "", 9)
        for t in trade_log[:max_rows]:
            side = str(t.get("side", "?")).lower()
            try:
                price = float(t.get("price", 0))
                ret = float(t.get("return_pct", 0))
                pnl = float(t.get("pnl", 0))
            except (TypeError, ValueError):
                continue
            fill = self.page % 2 == 0
            self.set_fill_color(*C_LIGHT)
            self.set_text_color(*C_PRIMARY)
            self.cell(26, 7, fa(str(t.get("timestamp", ""))[:10]), border=1, fill=fill, align="C")
            self.set_text_color(*(C_ACCENT if side == "buy" else C_RED))
            self.cell(14, 7, fa("خرید" if side == "buy" else "فروش"), border=1, fill=fill, align="C")
            self.set_text_color(*C_PRIMARY)
            self.cell(30, 7, f"{price:,.0f}", border=1, fill=fill, align="C")
            ret_c = C_ACCENT if ret >= 0 else C_RED
            self.set_text_color(*ret_c)
            self.cell(22, 7, f"{ret:+.1f}%", border=1, fill=fill, align="C")
            pnl_c = C_ACCENT if pnl >= 0 else C_RED
            self.set_text_color(*pnl_c)
            self.cell(30, 7, f"{pnl:+,.0f}", border=1, fill=fill, align="C")
            self.set_text_color(*C_GRAY)
            self.cell(0, 7, fa(str(t.get("reason", ""))[:12]), border=1, fill=fill, align="C",
                      new_x="LMARGIN", new_y="NEXT")


def _parse_equity(equity):
    """Return (dates, values) from various equity_curve shapes."""
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


def _equity_chart_png(equity, metrics: dict, benchmark=None, trade_markers=None) -> Optional[bytes]:
    """Render the equity chart standalone (matplotlib); return PNG bytes.

    Deliberately self-contained — does not import the bot main module
    (which requires TELEGRAM_BOT_TOKEN at import time).
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from datetime import datetime as _dt

        # Parse equity points
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

        # Benchmark (normalized to same start equity)
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

        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(11, 6.5), sharex=True,
            gridspec_kw={"height_ratios": [3.2, 1]},
        )
        fig.patch.set_facecolor("#ffffff")
        for ax in (ax1, ax2):
            ax.set_facecolor("#ffffff")
            ax.tick_params(colors="#555555", labelsize=8)
            for spine in ax.spines.values():
                spine.set_color("#cccccc")
            ax.grid(True, alpha=0.25, color="#999999")

        ax1.plot(dates, values, color="#1a7f37", linewidth=1.7, label="Strategy")
        ax1.fill_between(dates, values, min(min(values), min(bm_values) if bm_values else min(values)),
                         color="#1a7f37", alpha=0.08)
        if bm_values and len(bm_values) == len(bm_dates):
            ax1.plot(bm_dates, bm_values, color="#0969da", linewidth=1.2, alpha=0.85,
                     linestyle="--", label="Buy & Hold")
        ax1.set_ylabel("Equity ($)", color="#555555", fontsize=9)
        tr = metrics.get("total_return", 0)
        br = metrics.get("benchmark_return")
        title = f"Return: {tr:.1%}"
        if br is not None:
            title += f"  |  B&H: {br:.1%}  |  Excess: {metrics.get('excess_return', 0):+.1%}"
        title += f"  |  Sharpe: {metrics.get('sharpe', 0):.2f}"
        ax1.set_title(title, color="#111111", fontsize=11, loc="left")
        if bm_values:
            ax1.legend(loc="upper left", fontsize=8)

        # Trade markers
        if trade_markers:
            buys_x, buys_y, sells_x, sells_y = [], [], [], []
            for tmk in trade_markers:
                side = str(tmk.get("side", "")).lower()
                ts = str(tmk.get("timestamp", ""))[:10]
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
                ax1b.set_ylim(px_lo - span * 0.2, px_hi + span * 0.2)
                ax1b.tick_params(colors="#aaaaaa", labelsize=7)
                for spine in ax1b.spines.values():
                    spine.set_color("#cccccc")
                ax1b.set_ylabel("Price", color="#aaaaaa", fontsize=8)
                if buys_x:
                    ax1b.scatter(buys_x, buys_y, marker="^", color="#1a7f37", s=45, zorder=5, label="Buy")
                if sells_x:
                    ax1b.scatter(sells_x, sells_y, marker="v", color="#cf222e", s=45, zorder=5, label="Sell")
                ax1b.legend(loc="lower right", fontsize=8)

        # Drawdown
        peak = values[0]
        dd = []
        for v in values:
            peak = max(peak, v)
            dd.append((v - peak) / peak if peak else 0)
        ax2.fill_between(dates, dd, 0, color="#cf222e", alpha=0.3)
        ax2.plot(dates, dd, color="#cf222e", linewidth=1)
        ax2.set_ylabel("Drawdown", color="#555555", fontsize=9)
        mdd = metrics.get("max_drawdown")
        if mdd is not None:
            ax2.annotate(f"MDD {mdd:.1%}", xy=(dates[dd.index(min(dd))], min(dd)),
                         xytext=(10, -5), textcoords="offset points",
                         color="#cf222e", fontsize=8)
        if isinstance(dates[0], _dt):
            ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            ax2.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))

        plt.tight_layout()
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        fig.savefig(path, dpi=115, facecolor=fig.get_facecolor())
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
    metrics = detail.get("metrics", {})
    rx = detail.get("risk_xray") or {}
    ctx = detail.get("run_context") or {}
    trade_log = detail.get("trade_log") or []
    equity = detail.get("equity_curve") or []
    price_series = detail.get("price_series") or {}
    trade_markers = detail.get("trade_markers") or []

    pdf = PDFReport()
    pdf.add_page()

    prompt = str(detail.get("prompt", "بکتست"))[:80]
    pdf.cover_bar("گزارش بکتست", prompt)

    # Meta box
    codes = ctx.get("codes") or []
    pdf.info_box([
        ("دارایی‌ها", ", ".join(str(c) for c in codes[:4]) or "—"),
        ("بازه زمانی", f"{ctx.get('start_date', '—')} تا {ctx.get('end_date', '—')}"),
        ("تاریخ تولید", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
        ("شناسه اجرا", str(detail.get("run_id", "—"))[:24]),
    ])

    # Returns
    pdf.section("بازده")
    if metrics.get("final_value") is not None:
        pdf.kv_row("ارزش نهایی پرتفوی", f"${metrics['final_value']:,.0f}", C_ACCENT)
    if metrics.get("total_return") is not None:
        tr = metrics["total_return"]
        pdf.kv_row("بازده کل", f"{tr:+.1%}", C_ACCENT if tr >= 0 else C_RED)
    if metrics.get("annual_return") is not None:
        ar = metrics["annual_return"]
        pdf.kv_row("بازده سالانه", f"{ar:+.1%}", C_ACCENT if ar >= 0 else C_RED)

    # Risk
    pdf.section("ریسک")
    if metrics.get("max_drawdown") is not None:
        pdf.kv_row("حداکثر افت سرمایه", f"{metrics['max_drawdown']:.1%}", C_RED)
    vol = (rx.get("volatility") or {}).get("annualized_vol") or metrics.get("risk_xray_annualized_vol")
    if vol is not None:
        pdf.kv_row("نوسان سالانه", f"{vol:.1%}")
    if metrics.get("sharpe") is not None:
        pdf.kv_row("نسبت شارپ", f"{metrics['sharpe']:.2f}")
    if metrics.get("sortino") is not None:
        pdf.kv_row("نسبت سورتینو", f"{metrics['sortino']:.2f}")
    if metrics.get("calmar") is not None:
        pdf.kv_row("نسبت کالمار", f"{metrics['calmar']:.2f}")
    var95 = (rx.get("tail_risk") or {}).get("var_95")
    if var95 is not None:
        pdf.kv_row("VaR روزانه ۹۵٪", f"{var95:.2%}", C_RED)

    # Activity
    pdf.section("فعالیت معاملاتی")
    if metrics.get("win_rate") is not None:
        pdf.kv_row("نرخ برد", f"{metrics['win_rate']:.0%}")
    if metrics.get("trade_count") is not None:
        pdf.kv_row("تعداد معاملات کامل", str(metrics["trade_count"]))
    if metrics.get("avg_holding_days") is not None:
        pdf.kv_row("میانگین مدت نگهداری", f"{metrics['avg_holding_days']:.0f} روز")
    if metrics.get("total_turnover") is not None:
        pdf.kv_row("گردش کل", f"{metrics['total_turnover']:,.1f}")

    # Benchmark
    if metrics.get("benchmark_return") is not None:
        pdf.section("مقایسه با بنچمارک (خرید و نگهداری)")
        br = metrics["benchmark_return"]
        pdf.kv_row("بازده خرید و نگهداری", f"{br:+.1%}", C_ACCENT if br >= 0 else C_RED)
        if metrics.get("excess_return") is not None:
            ex = metrics["excess_return"]
            pdf.kv_row("بازده مازاد (آلفا)", f"{ex:+.1%}", C_ACCENT if ex >= 0 else C_RED)
        if metrics.get("information_ratio") is not None:
            pdf.kv_row("نسبت اطلاعات", f"{metrics['information_ratio']:.2f}")
        if metrics.get("tracking_error") is not None:
            pdf.kv_row("خطای ردیابی", f"{metrics['tracking_error']:.1%}")

    # Chart
    benchmark = None
    if isinstance(price_series, dict) and price_series:
        first = next(iter(price_series.values()))
        if isinstance(first, list):
            benchmark = first
    elif isinstance(price_series, list):
        benchmark = price_series

    chart = _equity_chart_png(equity, metrics, benchmark, trade_markers)
    if chart:
        pdf.add_page()
        pdf.section("نمودار عملکرد")
        x_w = pdf.w - pdf.l_margin - pdf.r_margin
        pdf.image(io.BytesIO(chart), x=pdf.l_margin, w=x_w)

    # Trades
    if trade_log:
        pdf.add_page()
        pdf.section(f"معاملات ({len(trade_log)} مورد)")
        pdf.trades_table(trade_log)

    pdf_bytes = pdf.output()
    return bytes(pdf_bytes)


def build_swarm_pdf(preset_name: str, preset_title: str, report: str,
                    tasks: list[dict] | None = None) -> bytes:
    """Build the swarm analysis report PDF."""
    pdf = PDFReport()
    pdf.add_page()

    pdf.cover_bar("گزارش تحلیل تیمی (Swarm)", preset_title or preset_name)

    pdf.info_box([
        ("تیم تحلیل", preset_title or preset_name),
        ("تعداد ایجنت‌ها", str(len(tasks or []))),
        ("تاریخ تولید", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
    ])

    if tasks:
        pdf.section("وضعیت ایجنت‌ها")
        status_fa = {"completed": "تکمیل شده", "in_progress": "در حال اجرا",
                     "blocked": "متوقف", "failed": "ناموفق"}
        for t in tasks:
            name = t.get("agent_name", "?")
            s = t.get("status", "?")
            color = C_ACCENT if s == "completed" else C_RED if s == "failed" else C_PRIMARY
            pdf.set_font("Vazir", "", 10.5)
            pdf.set_text_color(*color)
            pdf.cell(0, 7, fa(f"• {name} — {status_fa.get(s, s)}"),
                     align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.section("گزارش نهایی")
    # Report can be long; multi_cell handles pagination
    # Split by lines to keep RTL shaping per-paragraph
    for para in (report or "").split("\n"):
        para = para.strip()
        if para:
            pdf.body(para, size=10.5)
        else:
            pdf.ln(2)

    return bytes(pdf.output())
