"""Vibe-Trading SaaS — Telegram Bot (aiogram 3.x)"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Optional

import httpx
import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BotCommand
)
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage, DefaultKeyBuilder
from aiogram.types import FSInputFile, BufferedInputFile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


# ============================================================================
# Config
# ============================================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:9000")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
ALLOWED_USERS = os.getenv("TELEGRAM_ALLOWED_USERS", "")

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is required")


# ============================================================================
# Gateway Client
# ============================================================================

class GatewayClient:
    """Async HTTP client for the SaaS Gateway."""

    def __init__(self, base_url: str):
        self.base_url = base_url

    async def request(self, method: str, path: str, token: str = None, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.request(method, url, headers=headers, **kwargs)
                if resp.status_code == 429:
                    detail = resp.json().get("detail", "سقف درخواست تمام شده")
                    return {"error": detail, "status": 429}
                if resp.status_code >= 400:
                    try:
                        return {"error": resp.json().get("detail", "خطا"), "status": resp.status_code}
                    except Exception:
                        return {"error": resp.text, "status": resp.status_code}
                return resp.json()
        except httpx.ConnectError:
            return {"error": "سرور در دسترس نیست", "status": 503}
        except Exception as e:
            return {"error": str(e), "status": 500}

    async def register(self, username: str, password: str, telegram_id: int = None, device_id: str = None) -> dict:
        data = {"username": username, "password": password}
        if telegram_id:
            data["telegram_id"] = telegram_id
        if device_id:
            data["device_id"] = device_id
        return await self.request("POST", "/api/v1/auth/register", json=data)

    async def login(self, username: str, password: str) -> dict:
        return await self.request("POST", "/api/v1/auth/login", json={"username": username, "password": password})

    async def create_session(self, token: str) -> dict:
        return await self.request("POST", "/api/v1/vibe/sessions", token=token)

    async def send_message(self, token: str, session_id: str, message: str) -> dict:
        return await self.request("POST", f"/api/v1/vibe/sessions/{session_id}/messages", token=token, json={"content": message})

    async def get_messages(self, token: str, session_id: str) -> dict:
        return await self.request("GET", f"/api/v1/vibe/sessions/{session_id}/messages", token=token)

    async def get_runs(self, token: str) -> dict:
        return await self.request("GET", "/api/v1/vibe/runs", token=token)

    async def get_run_detail(self, token: str, run_id: str) -> dict:
        return await self.request("GET", f"/api/v1/vibe/runs/{run_id}", token=token)

    async def list_sessions(self, token: str) -> dict:
        return await self.request("GET", "/api/v1/vibe/sessions", token=token)

    async def session_history(self, token: str, session_id: str) -> dict:
        return await self.request("GET", f"/api/v1/vibe/sessions/{session_id}/history", token=token)

    async def get_swarm_presets(self, token: str) -> dict:
        return await self.request("GET", "/api/v1/vibe/swarm/presets", token=token)

    async def create_swarm_run(self, token: str, preset_name: str, user_vars: dict) -> dict:
        return await self.request("POST", "/api/v1/vibe/swarm/runs", token=token, json={"preset_name": preset_name, "user_vars": user_vars})

    async def get_swarm_run(self, token: str, run_id: str) -> dict:
        return await self.request("GET", f"/api/v1/vibe/swarm/runs/{run_id}", token=token)

    async def get_swarm_runs(self, token: str) -> dict:
        return await self.request("GET", "/api/v1/vibe/swarm/runs", token=token)

    async def alpha_list(self, token: str, zoo: str = None) -> dict:
        path = "/api/v1/vibe/alpha/list"
        if zoo:
            path += f"?zoo={zoo}"
        return await self.request("GET", path, token=token)

    async def alpha_detail(self, token: str, alpha_id: str) -> dict:
        return await self.request("GET", f"/api/v1/vibe/alpha/{alpha_id}", token=token)

    async def alpha_bench(self, token: str, zoo: str, universe: str, period: str, top: int = 10) -> dict:
        return await self.request("POST", "/api/v1/vibe/alpha/bench", token=token, json={
            "zoo": zoo, "universe": universe, "period": period, "top": top,
        })

    async def get_task_status(self, token: str, task_id: str) -> dict:
        return await self.request("GET", f"/api/v1/tasks/{task_id}", token=token)


gateway = GatewayClient(GATEWAY_URL)

# PDF report builders (container layout: /app/app/pdf_report.py)
try:
    from app.pdf_report import build_backtest_pdf, build_swarm_pdf
except ImportError:  # host/test layout
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "pdf_report",
        os.path.join(os.path.dirname(__file__), "pdf_report.py"),
    )
    _m = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_m)
    build_backtest_pdf = _m.build_backtest_pdf
    build_swarm_pdf = _m.build_swarm_pdf


# ============================================================================
# FSM States
# ============================================================================

class ChatState(StatesGroup):
    main_menu = State()
    chat_active = State()
    awaiting_register_user = State()
    awaiting_register_pass = State()
    awaiting_login_user = State()
    awaiting_login_pass = State()
    # Dynamic swarm form — one state per active variable
    swarm_var_input = State()


# ============================================================================
# User Token/Session Store (Redis-backed)
# ============================================================================

_redis = None

async def get_redis():
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


async def get_user_token(user_id: int) -> Optional[str]:
    r = await get_redis()
    return await r.get(f"tg:{user_id}:token")


async def set_user_token(user_id: int, token: str):
    r = await get_redis()
    await r.set(f"tg:{user_id}:token", token, ex=60 * 60 * 24 * 30)


async def get_user_session(user_id: int) -> Optional[str]:
    r = await get_redis()
    return await r.get(f"tg:{user_id}:session")


async def add_session_to_history(user_id: int, session_id: str, name: str = ""):
    """Track session IDs per user in a Redis list (for the chat hub)."""
    r = await get_redis()
    key = f"tg:{user_id}:sessions"
    # Avoid duplicates
    existing = await r.lrange(key, 0, -1)
    for item in existing:
        try:
            if json.loads(item).get("id") == session_id:
                return
        except Exception:
            continue
    await r.lpush(key, json.dumps({"id": session_id, "name": name[:40]}))
    await r.ltrim(key, 0, 49)  # keep last 50


async def get_session_history(user_id: int) -> list[dict]:
    r = await get_redis()
    items = await r.lrange(f"tg:{user_id}:sessions", 0, 49)
    out = []
    for item in items:
        try:
            out.append(json.loads(item))
        except Exception:
            continue
    return out


async def set_user_session(user_id: int, session_id: str):
    r = await get_redis()
    await r.set(f"tg:{user_id}:session", session_id, ex=60 * 60 * 24 * 7)


# ============================================================================
# Keyboard Helpers
# ============================================================================

def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 چت با AI", callback_data="chat")],
        [InlineKeyboardButton(text="📊 بک‌تست", callback_data="backtest")],
        [InlineKeyboardButton(text="🤖 تیم‌های تحلیل (Swarm)", callback_data="swarm")],
        [InlineKeyboardButton(text="🧪 آزمایشگاه آلفا (Alpha Zoo)", callback_data="alphazoo")],
        [InlineKeyboardButton(text="📈 گزارش‌ها", callback_data="reports")],
        [InlineKeyboardButton(text="👤 اشتراک من", callback_data="subscription")],
    ])


def back_to_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« منوی اصلی", callback_data="menu")]
    ])


# ============================================================================
# Equity Curve Chart Rendering
# ============================================================================

def _parse_equity_points(equity):
    """Parse equity curve into (values, labels). Handles multiple shapes."""
    values = []
    labels = []
    for point in equity:
        if isinstance(point, (int, float)):
            values.append(float(point))
        elif isinstance(point, dict):
            v = point.get("equity") or point.get("value") or point.get("close")
            d = point.get("date") or point.get("time") or point.get("timestamp", "")
            if v is not None:
                try:
                    values.append(float(v))
                    labels.append(str(d))
                except (ValueError, TypeError):
                    pass
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            try:
                values.append(float(point[1]))
                labels.append(str(point[0]))
            except (ValueError, TypeError):
                pass
    return values, labels


def _sparkline_equity(equity) -> str:
    """Text-based sparkline of the equity curve (fallback)."""
    values, _ = _parse_equity_points(equity)
    if len(values) < 4:
        return ""
    # Sample down to ~30 columns
    step = max(1, len(values) // 30)
    sampled = values[::step][:30]
    lo, hi = min(sampled), max(sampled)
    rng = (hi - lo) or 1
    blocks = "▁▂▃▄▅▆▇█"
    line = "".join(blocks[min(7, int((v - lo) / rng * 7))] for v in sampled)
    return f"{line}\nکمترین: {lo:,.0f} | بیشترین: {hi:,.0f}"


def _render_equity_chart(run_id: str, equity, metrics: dict, benchmark=None, trade_markers=None) -> str | None:
    """Render pro report chart: equity + benchmark + drawdown + trade markers.

    Returns PNG path or None. All inputs optional beyond equity.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from datetime import datetime
    except ImportError:
        return None

    values, labels = _parse_equity_points(equity)
    if len(values) < 4:
        return None

    # Parse dates where possible
    dates = []
    for lab in labels:
        try:
            dates.append(datetime.strptime(str(lab)[:10], "%Y-%m-%d"))
        except Exception:
            dates.append(None)
    if not any(dates):
        dates = list(range(len(values)))

    # Downsample if huge
    if len(values) > 700:
        step = len(values) // 700
        idx = list(range(0, len(values), step))
        values = [values[i] for i in idx]
        dates = [dates[i] for i in idx]

    # Benchmark series (buy & hold normalized to same start)
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
                            bm_dates.append(datetime.strptime(str(d)[:10], "%Y-%m-%d"))
                        except Exception:
                            bm_dates.append(None)
                    except (ValueError, TypeError):
                        pass
        if bm_values:
            base = bm_values[0]
            scale = values[0] / base if base else 1
            bm_values = [v * scale for v in bm_values]

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11, 6.5), sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1]},
    )
    fig.patch.set_facecolor("#0d1117")
    for ax in (ax1, ax2):
        ax.set_facecolor("#0d1117")
        ax.tick_params(colors="#8b949e", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#30363d")
        ax.grid(True, alpha=0.15, color="#8b949e")

    # --- Equity + benchmark ---
    ax1.plot(dates, values, color="#2ea043", linewidth=1.7, label="Strategy")
    ax1.fill_between(dates, values, min(min(values), min(bm_values) if bm_values else min(values)),
                     color="#2ea043", alpha=0.10)
    if bm_values and len(bm_values) == len(bm_dates):
        ax1.plot(bm_dates, bm_values, color="#58a6ff", linewidth=1.2, alpha=0.85,
                 linestyle="--", label="Buy & Hold")
    ax1.set_ylabel("Equity ($)", color="#8b949e", fontsize=9)
    tr = metrics.get("total_return", 0)
    br = metrics.get("benchmark_return")
    title = f"Return: {tr:.1%}"
    if br is not None:
        title += f"  |  B&H: {br:.1%}  |  Excess: {metrics.get('excess_return', 0):+.1%}"
    title += f"  |  Sharpe: {metrics.get('sharpe', 0):.2f}"
    ax1.set_title(title, color="#e6edf3", fontsize=11, loc="left")
    if bm_values:
        leg = ax1.legend(loc="upper left", fontsize=8)
        for t in leg.get_texts():
            t.set_color("#e6edf3")

    # --- Trade markers ---
    if trade_markers:
        buys_x, buys_y, sells_x, sells_y = [], [], [], []
        for tmk in trade_markers:
            side = str(tmk.get("side", "")).lower()
            ts = str(tmk.get("timestamp", ""))[:10]
            price = tmk.get("price")
            try:
                px = float(price)
            except (TypeError, ValueError):
                continue
            try:
                dx = datetime.strptime(ts, "%Y-%m-%d")
            except Exception:
                continue
            if side == "buy":
                buys_x.append(dx)
                buys_y.append(px)
            elif side == "sell":
                sells_x.append(dx)
                sells_y.append(px)
        # Scale price axis to equity axis with a twin
        if buys_x or sells_x:
            ax1b = ax1.twinx()
            ax1b.set_facecolor("none")
            all_px = buys_y + sells_y
            eq_lo, eq_hi = min(values), max(values)
            px_lo, px_hi = (min(all_px), max(all_px)) if all_px else (0, 1)
            span = (px_hi - px_lo) or 1
            ax1b.set_ylim(px_lo - span * 0.2, px_hi + span * 0.2)
            ax1b.tick_params(colors="#30363d", labelsize=7)
            for spine in ax1b.spines.values():
                spine.set_color("#30363d")
            ax1b.set_ylabel("Price", color="#30363d", fontsize=8)
            if buys_x:
                ax1b.scatter(buys_x, buys_y, marker="^", color="#2ea043", s=45,
                             zorder=5, label="Buy", edgecolors="#0d1117")
            if sells_x:
                ax1b.scatter(sells_x, sells_y, marker="v", color="#f85149", s=45,
                             zorder=5, label="Sell", edgecolors="#0d1117")
            leg2 = ax1b.legend(loc="lower right", fontsize=8)
            for t in leg2.get_texts():
                t.set_color("#e6edf3")

    # --- Drawdown ---
    peak = values[0]
    dd = []
    for v in values:
        peak = max(peak, v)
        dd.append((v - peak) / peak if peak else 0)
    ax2.fill_between(dates, dd, 0, color="#f85149", alpha=0.35)
    ax2.plot(dates, dd, color="#f85149", linewidth=1)
    ax2.set_ylabel("Drawdown", color="#8b949e", fontsize=9)
    mdd = metrics.get("max_drawdown")
    if mdd is not None:
        ax2.annotate(
            f"MDD {mdd:.1%}", xy=(dates[dd.index(min(dd))], min(dd)),
            xytext=(10, -5), textcoords="offset points",
            color="#f85149", fontsize=8,
        )

    if isinstance(dates[0], datetime):
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax2.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))

    plt.tight_layout()
    path = f"/tmp/equity_{run_id[:20]}.png"
    fig.savefig(path, dpi=115, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def _format_trades_table(trade_log, max_rows: int = 8) -> str:
    """Format trade log as a monospace table (Telegram-friendly)."""
    if not trade_log:
        return ""
    rows = []
    for t in trade_log[:max_rows]:
        ts = str(t.get("timestamp", ""))[:10]
        side = str(t.get("side", "?")).lower()
        icon = "🟢" if side == "buy" else "🔴"
        try:
            price = float(t.get("price", 0))
            ret = float(t.get("return_pct", 0))
            pnl = float(t.get("pnl", 0))
        except (TypeError, ValueError):
            continue
        rows.append(f"{icon} {ts}  {price:>10,.0f}  {ret:>+7.1f}%  {pnl:>+10,.0f}$")
    header = " وقت        قیمت      بازده    سود/زیان"
    sep = "─" * 44
    return "📋 **معاملات:**\n```\n" + header + "\n" + sep + "\n" + "\n".join(rows) + "\n```"


# ============================================================================
# Handlers
# ============================================================================

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    token = await get_user_token(user_id)

    if token:
        await message.answer(
            "🟢 **خوش آمدید!**\n\n"
            "ربات Vibe-Trading آماده‌ست.\n"
            "از منوی زیر یکی را انتخاب کنید:",
            reply_markup=main_menu_kb(),
            parse_mode="Markdown",
        )
        await state.set_state(ChatState.main_menu)
    else:
        await message.answer(
            "👋 **به Vibe-Trading خوش آمدید!**\n\n"
            "برای شروع، لطفاً وارد شوید یا ثبت‌نام کنید:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📝 ثبت‌نام", callback_data="register")],
                [InlineKeyboardButton(text="🔑 ورود", callback_data="login")],
            ]),
            parse_mode="Markdown",
        )


@router.callback_query(F.data == "register")
async def cb_register(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📝 **ثبت‌نام**\n\n"
        "نام کاربری را وارد کنید (انگلیسی، حداقل ۳ حرف):",
        parse_mode="Markdown",
    )
    await state.set_state(ChatState.awaiting_register_user)
    await callback.answer()


@router.message(ChatState.awaiting_register_user)
async def process_register_user(message: Message, state: FSMContext):
    username = message.text.strip()
    if len(username) < 3:
        await message.answer("نام کاربری باید حداقل ۳ حرف باشد.")
        return
    await state.update_data(reg_username=username)
    await message.answer(
        f"👤 نام کاربری: `{username}`\n\n"
        "رمز عبور را وارد کنید:",
        parse_mode="Markdown",
    )
    await state.set_state(ChatState.awaiting_register_pass)


@router.message(ChatState.awaiting_register_pass)
async def process_register_pass(message: Message, state: FSMContext):
    password = message.text.strip()
    if len(password) < 6:
        await message.answer("رمز عبور باید حداقل ۶ حرف باشد.")
        return

    data = await state.get_data()
    username = data["reg_username"]
    telegram_id = message.from_user.id

    result = await gateway.register(username, password, telegram_id=telegram_id, device_id=f"tg:{telegram_id}")

    if "error" in result:
        await message.answer(f"❌ خطا: {result['error']}", reply_markup=back_to_menu_kb())
        await state.set_state(ChatState.main_menu)
        return

    token = result.get("access_token")
    if token:
        await set_user_token(telegram_id, token)
        await message.answer(
            f"✅ **ثبت‌نام موفق!**\n\n"
            f"👤 کاربر: `{username}`\n"
            f"📊 پلن: رایگان\n\n"
            f"حالا می‌توانید از منو استفاده کنید:",
            reply_markup=main_menu_kb(),
            parse_mode="Markdown",
        )
        await state.set_state(ChatState.main_menu)
    else:
        await message.answer("❌ خطا در ثبت‌نام", reply_markup=back_to_menu_kb())


@router.callback_query(F.data == "login")
async def cb_login(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🔑 **ورود**\n\n"
        "نام کاربری را وارد کنید:",
        parse_mode="Markdown",
    )
    await state.set_state(ChatState.awaiting_login_user)
    await callback.answer()


@router.message(ChatState.awaiting_login_user)
async def process_login_user(message: Message, state: FSMContext):
    await state.update_data(login_username=message.text.strip())
    await message.answer("🔑 رمز عبور را وارد کنید:")
    await state.set_state(ChatState.awaiting_login_pass)


@router.message(ChatState.awaiting_login_pass)
async def process_login_pass(message: Message, state: FSMContext):
    data = await state.get_data()
    username = data["login_username"]
    password = message.text.strip()

    result = await gateway.login(username, password)

    if "error" in result:
        await message.answer(f"❌ خطا: {result['error']}", reply_markup=back_to_menu_kb())
        await state.set_state(ChatState.main_menu)
        return

    token = result.get("access_token")
    if token:
        await set_user_token(message.from_user.id, token)
        plan = result.get("plan", "free")
        await message.answer(
            f"✅ **ورود موفق!**\n\n"
            f"📊 پلن فعلی: {plan}\n\n"
            f"از منو استفاده کنید:",
            reply_markup=main_menu_kb(),
            parse_mode="Markdown",
        )
        await state.set_state(ChatState.main_menu)


# ============================================================================
# Chat Handler
# ============================================================================

@router.callback_query(F.data == "chat")
async def cb_chat(callback: CallbackQuery, state: FSMContext):
    token = await get_user_token(callback.from_user.id)
    if not token:
        await callback.message.edit_text("❌ ابتدا وارد شوید.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    # Chat Hub: new chat + previous sessions list
    hub_text = "💬 **چت‌هاب**\n\nیک چت جدید شروع کنید یا یکی از گفتگوهای قبلی را باز کنید:"
    kb_rows = [[InlineKeyboardButton(text="🆕 چت جدید", callback_data="newchat")]]
    history = await get_session_history(callback.from_user.id)
    if history:
        kb_rows.append([InlineKeyboardButton(text="📂 گفتگوهای قبلی:", callback_data="noop")])
        for i, s in enumerate(history[:8]):
            sid = s.get("id", "")
            name = s.get("name") or f"چت {i + 1}"
            kb_rows.append([InlineKeyboardButton(
                text=f"💬 {name}",
                callback_data=f"opensess:{sid}",
            )])
    kb_rows.append([InlineKeyboardButton(text="« منوی اصلی", callback_data="menu")])

    await callback.message.edit_text(
        hub_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "newchat")
async def cb_newchat(callback: CallbackQuery, state: FSMContext):
    token = await get_user_token(callback.from_user.id)
    if not token:
        await callback.message.edit_text("❌ ابتدا وارد شوید.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    result = await gateway.create_session(token)
    if "error" in result:
        await callback.message.edit_text(f"❌ خطا: {result['error']}", reply_markup=back_to_menu_kb())
        await callback.answer()
        return
    session_id = result.get("session_id")
    await set_user_session(callback.from_user.id, session_id)
    await add_session_to_history(callback.from_user.id, session_id, name="چت جدید")

    await callback.message.edit_text(
        "💬 **چت جدید شروع شد**\n\n"
        "پیام خود را بنویسید. برای خروج /menu بزنید.",
        parse_mode="Markdown",
    )
    await state.set_state(ChatState.chat_active)
    await state.update_data(session_id=session_id)
    await callback.answer()


@router.callback_query(F.data.startswith("opensess:"))
async def cb_opensess(callback: CallbackQuery, state: FSMContext):
    """Open a previous session: show its history, then resume chat."""
    session_id = callback.data.split(":", 1)[1]
    token = await get_user_token(callback.from_user.id)
    if not token:
        await callback.message.edit_text("❌ ابتدا وارد شوید.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    await callback.message.edit_text("📂 در حال بارگذاری تاریخچه...")

    msgs = await gateway.session_history(token, session_id)
    if "error" in msgs or not isinstance(msgs, list):
        text = "⚠️ تاریخچه‌ای یافت نشد. می‌توانید همین‌جا ادامه دهید."
    else:
        text = "📂 **تاریخچه گفتگو:**\n\n"
        # Last 6 messages, compact
        for m in msgs[-6:]:
            role = m.get("role", "?")
            content = str(m.get("content", ""))[:180]
            if role == "user":
                text += f"🧑 شما: {content}\n\n"
            elif role == "assistant":
                text += f"🤖 AI: {content}\n\n"
        if len(text) > 3800:
            text = text[:3800] + "\n..."

    await set_user_session(callback.from_user.id, session_id)
    kb = [
        [InlineKeyboardButton(text="✏️ ادامه این گفتگو", callback_data=f"resume:{session_id}")],
        [InlineKeyboardButton(text="« چت‌هاب", callback_data="chat")],
        [InlineKeyboardButton(text="« منوی اصلی", callback_data="menu")],
    ]
    try:
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")
    except Exception:
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()


@router.callback_query(F.data.startswith("resume:"))
async def cb_resume(callback: CallbackQuery, state: FSMContext):
    session_id = callback.data.split(":", 1)[1]
    await set_user_session(callback.from_user.id, session_id)

    await callback.message.edit_text(
        "💬 **حالت چت فعال شد**\n\n"
        "پیام خود را بنویسید. برای خروج /menu بزنید.",
        parse_mode="Markdown",
    )
    await state.set_state(ChatState.chat_active)
    await state.update_data(session_id=session_id)
    await callback.answer()


@router.message(ChatState.chat_active)
async def process_chat_message(message: Message, state: FSMContext):
    if message.text == "/menu":
        await message.answer("منوی اصلی:", reply_markup=main_menu_kb())
        await state.set_state(ChatState.main_menu)
        return

    token = await get_user_token(message.from_user.id)
    data = await state.get_data()
    session_id = data.get("session_id")

    if not token or not session_id:
        await message.answer("❌ جلسه منقضی شده. دوباره وارد شوید.", reply_markup=back_to_menu_kb())
        await state.set_state(ChatState.main_menu)
        return

    await message.chat.do("typing")

    # Snapshot BEFORE sending: only answers that arrive after our send are ours.
    # This is the fix for the "previous answer shown again" bug.
    pre = await gateway.get_messages(token, session_id)
    pre_count = len(pre) if isinstance(pre, list) else 0

    result = await gateway.send_message(token, session_id, message.text)

    if "error" in result:
        status_code = result.get("status", 500)
        error_text = str(result.get("error", ""))
        if status_code == 429:
            await message.answer(f"⏳ {error_text}", reply_markup=back_to_menu_kb())
            return
        if status_code == 409 or "already has a run" in error_text:
            # A previous run is still executing — do NOT resend (that 409s again).
            # Just wait for the pending run to produce its answer, then deliver it.
            thinking_msg = await message.answer(
                "⏳ پاسخ قبلی هنوز در حال تولید است، منتظر می‌مانم..."
            )
            answer = await _wait_for_new_answer(token, session_id, pre_count, max_wait=180)
            try:
                await thinking_msg.delete()
            except Exception:
                pass
            if answer:
                await _send_long(message, answer)
            else:
                await message.answer(
                    "⏰ پردازش قبلی طولانی شد. چند لحظه دیگر پیام بدهید یا از «📈 گزارش‌ها» چک کنید."
                )
            return
        await message.answer(f"❌ خطا: {error_text}")
        return

    # Poll for the NEW assistant answer (after pre_count)
    thinking_msg = await message.answer("🔄 در حال پردازش...")
    answer = await _wait_for_new_answer(token, session_id, pre_count, max_wait=180)
    try:
        await thinking_msg.delete()
    except Exception:
        pass
    if answer:
        await _send_long(message, answer)
    else:
        await message.answer("⏰ پاسخ دریافت نشد. لطفاً دوباره تلاش کنید.")


async def _wait_for_new_answer(token: str, session_id: str, pre_count: int,
                               max_wait: int = 180) -> Optional[str]:
    """Poll until a NEW assistant message (index >= pre_count) arrives."""
    for i in range(max_wait):
        await asyncio.sleep(1)
        messages = await gateway.get_messages(token, session_id)
        if not isinstance(messages, list):
            continue
        # Only consider messages added after our snapshot
        new_msgs = messages[pre_count:]
        for msg in reversed(new_msgs):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"]
    return None


async def _send_long(message: Message, answer: str):
    """Send an answer, chunked at Telegram's 4096-char limit (plain text)."""
    for chunk_start in range(0, max(len(answer), 1), 4000):
        chunk = answer[chunk_start:chunk_start + 4000]
        if chunk:
            try:
                await message.answer(chunk)
            except Exception:
                pass


# ============================================================================
# Backtest Handler
# ============================================================================

BACKTEST_PRESETS = {
    "pair": "جفت‌ارز (Pair Trading)",
    "momentum": "مومنتوم",
    "mean_reversion": "بازگشت به میانگین",
    "custom": "سفارشی",
}


@router.callback_query(F.data == "backtest")
async def cb_backtest(callback: CallbackQuery, state: FSMContext):
    token = await get_user_token(callback.from_user.id)
    if not token:
        await callback.message.edit_text("❌ ابتدا وارد شوید.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=v, callback_data=f"bt:{k}")] for k, v in BACKTEST_PRESETS.items()
    ] + [[InlineKeyboardButton(text="« منوی اصلی", callback_data="menu")]])

    await callback.message.edit_text(
        "📊 **نوع بک‌تست را انتخاب کنید:**",
        reply_markup=kb,
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bt:"))
async def cb_backtest_preset(callback: CallbackQuery, state: FSMContext):
    preset = callback.data.split(":")[1]
    await callback.message.edit_text(
        f"📊 **بک‌تست: {BACKTEST_PRESETS.get(preset, preset)}**\n\n"
        "پرامپت ساده بنویسید:\n"
        "«بک‌تست اپل و مایکروسافت از ۲۰۲۳ تا ۲۰۲۴»",
        parse_mode="Markdown",
    )
    await state.set_state(ChatState.chat_active)
    await callback.answer()


# ============================================================================
# Swarm (Multi-Agent Teams)
# ============================================================================

@router.callback_query(F.data == "swarm")
async def cb_swarm(callback: CallbackQuery, state: FSMContext):
    token = await get_user_token(callback.from_user.id)
    if not token:
        await callback.message.edit_text("❌ ابتدا وارد شوید.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    presets = await gateway.get_swarm_presets(token)
    if "error" in presets:
        await callback.message.edit_text(f"❌ خطا: {presets['error']}", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    kb_lines = [[InlineKeyboardButton(text="📜 اجراهای قبلی", callback_data="swhist:0")]]
    for p in (presets if isinstance(presets, list) else presets.get("presets", [])):
        name = p.get("name", "")
        title = p.get("title", name)
        agents = p.get("agent_count", "?")
        kb_lines.append([InlineKeyboardButton(
            text=f"🤖 {title} ({agents} agent)",
            callback_data=f"sw:{name}"
        )])
    kb_lines.append([InlineKeyboardButton(text="« منوی اصلی", callback_data="menu")])

    await callback.message.edit_text(
        "🤖 **تیم‌های تحلیل (Swarm)**\n\n"
        "یک تیم را انتخاب کنید:\n"
        "هر تیم چندین AI specialist دارد که با هم کار می‌کنند.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_lines),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("swhist:"))
async def cb_swarm_history(callback: CallbackQuery, state: FSMContext):
    """Previous swarm runs — paginated list; open a run → detail + PDF."""
    try:
        page = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        page = 0
    token = await get_user_token(callback.from_user.id)
    if not token:
        await callback.message.edit_text("❌ ابتدا وارد شوید.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    runs = await gateway.get_swarm_runs(token)
    if "error" in runs or not isinstance(runs, list):
        runs = []

    PER = 5
    total = len(runs)
    max_page = max(0, (total - 1) // PER)
    page = max(0, min(page, max_page))
    chunk = runs[page * PER:(page + 1) * PER]

    if not chunk:
        await callback.message.edit_text(
            "📜 **اجراهای قبلی**\n\n📭 هنوز اجرایی ندارید.\n"
            "از منوی Swarm یک تیم انتخاب و اجرا کنید.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« تیم‌های تحلیل", callback_data="swarm")],
                [InlineKeyboardButton(text="« منوی اصلی", callback_data="menu")],
            ]),
            parse_mode="Markdown",
        )
        await callback.answer()
        return

    text = f"📜 **اجراهای قبلی** (صفحه {page + 1}/{max_page + 1})\n\n"
    kb_rows = []
    for r in chunk:
        rid = r.get("id", "")
        preset = r.get("preset_name", "?")
        status = r.get("status", "?")
        icon = {"completed": "✅", "running": "🔄", "failed": "❌", "cancelled": "🚫"}.get(status, "⏳")
        done = r.get("completed_count", "?")
        task_count = r.get("task_count", "?")
        text += f"{icon} `{preset}` — {status} ({done}/{task_count})\n"
        kb_rows.append([InlineKeyboardButton(
            text=f"{icon} {preset[:26]} — {status}",
            callback_data=f"swrun:{rid[:56]}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀ قبلی", callback_data=f"swhist:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"📄 {page + 1}/{max_page + 1}", callback_data="noop"))
    if page < max_page:
        nav.append(InlineKeyboardButton(text="بعدی ▶", callback_data=f"swhist:{page + 1}"))
    kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text="« تیم‌های تحلیل", callback_data="swarm")])
    kb_rows.append([InlineKeyboardButton(text="« منوی اصلی", callback_data="menu")])

    try:
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="Markdown")
    except Exception:
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await callback.answer()


@router.callback_query(F.data.startswith("swrun:"))
async def cb_swarm_run_detail(callback: CallbackQuery, state: FSMContext):
    """Open a previous swarm run: status, report preview, PDF download."""
    run_id = callback.data.split(":", 1)[1]
    token = await get_user_token(callback.from_user.id)
    if not token:
        await callback.message.edit_text("❌ ابتدا وارد شوید.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    await callback.message.edit_text("📜 در حال بارگذاری اجرا...")

    status = await gateway.get_swarm_run(token, run_id)
    if "error" in status:
        await callback.message.edit_text(f"❌ {status['error']}", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    preset = status.get("preset_name", "?")
    run_status = status.get("status", "?")
    tasks = status.get("tasks", [])
    report = status.get("final_report", "")

    icon = {"completed": "✅", "running": "🔄", "failed": "❌", "cancelled": "🚫"}.get(run_status, "⏳")
    text = f"📜 **اجرای تیمی**\n\n🤖 {preset}\n{icon} وضعیت: {run_status}\n\n"
    for t in tasks:
        agent = str(t.get("agent_name", "?"))[:22]
        s = t.get("status", "?")
        aicon = {"completed": "✅", "in_progress": "🔄", "blocked": "⚠️", "failed": "❌"}.get(s, "⏳")
        text += f"{aicon} {agent}\n"

    kb = []
    if report:
        text += f"\n📝 **پیش‌نمایش گزارش:**\n{str(report)[:600]}{'...' if len(report) > 600 else ''}\n"
        kb.append([InlineKeyboardButton(text="📄 دانلود PDF کامل", callback_data=f"swpdf:{run_id[:56]}")])
    elif run_status == "running":
        kb.append([InlineKeyboardButton(text="🔄 رفرش وضعیت", callback_data=f"swrun:{run_id[:56]}")])
    kb.append([InlineKeyboardButton(text="« اجراهای قبلی", callback_data="swhist:0")])
    kb.append([InlineKeyboardButton(text="« منوی اصلی", callback_data="menu")])

    try:
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")
    except Exception:
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()


@router.callback_query(F.data.startswith("swpdf:"))
async def cb_swarm_pdf(callback: CallbackQuery):
    """Download a previous swarm run's report as PDF."""
    run_id = callback.data.split(":", 1)[1]
    token = await get_user_token(callback.from_user.id)
    if not token:
        await callback.answer("ابتدا وارد شوید", show_alert=True)
        return

    wait = await callback.message.answer("📄 در حال ساخت PDF...")
    try:
        status = await gateway.get_swarm_run(token, run_id)
        if "error" in status:
            await wait.edit_text(f"❌ {status['error']}")
            await callback.answer()
            return

        report = status.get("final_report", "")
        if not report:
            await wait.edit_text("⚠️ این اجرا هنوز گزارشی ندارد (تکمیل نشده).")
            await callback.answer()
            return

        tasks = status.get("tasks", [])
        preset = status.get("preset_name", "swarm")
        pdf_bytes = await asyncio.to_thread(build_swarm_pdf, preset, preset, report, tasks)

        doc = BufferedInputFile(pdf_bytes, filename=f"swarm_{run_id[:16]}.pdf")
        await wait.delete()
        await callback.message.answer_document(doc, caption=f"📄 گزارش {preset}")
    except Exception as e:
        await wait.edit_text(f"❌ خطا در ساخت PDF: {e}")
    await callback.answer()


@router.callback_query(F.data.startswith("sw:"))
async def cb_swarm_run(callback: CallbackQuery, state: FSMContext):
    """Start the dynamic form: fetch preset variables, ask them one by one."""
    preset_name = callback.data.split(":", 1)[1]
    token = await get_user_token(callback.from_user.id)
    if not token:
        await callback.message.edit_text("❌ ابتدا وارد شوید.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    presets = await gateway.get_swarm_presets(token)
    preset = None
    if isinstance(presets, list):
        preset = next((p for p in presets if p.get("name") == preset_name), None)
    if not preset:
        await callback.message.edit_text("❌ پریست یافت نشد.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    variables = preset.get("variables", [])
    # Filter: required vars + any optional ones (all asked, optional skippable)
    await state.update_data(sw_preset=preset_name, sw_vars=variables, sw_answers={})

    if not variables:
        # No variables — launch immediately
        user_vars = {"output_language": "Persian (Farsi) — write the ENTIRE final report in fluent Persian"}
        status = await callback.message.edit_text(
            f"🤖 در حال راه‌اندازی تیم...\n\nپریست: {preset_name}\n⏳ ۱۵-۲۰ دقیقه."
        )
        await state.set_state(ChatState.main_menu)
        result = await gateway.create_swarm_run(token, preset_name, user_vars)
        if "error" in result:
            await status.edit_text(f"❌ خطا: {result['error']}", reply_markup=back_to_menu_kb())
            await callback.answer()
            return
        await _track_swarm_progress(status, callback, state, token, preset_name, result.get("id", ""))
        return

    await _ask_next_swarm_var(callback, state, callback.from_user.id)


# ============================================================================
# Dynamic Swarm Form — smart keyboards per common variable, text input otherwise
# ============================================================================

VAR_SUGGESTIONS = {
    "target": ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"],
    "timeframe": ["کوتاه‌مدت ۱-۴ هفته", "میان‌مدت ۱-۳ ماه", "بلندمدت ۳-۱۲ ماه"],
    "market": ["کریپتو", "سهام آمریکا", "سهام چین", "بازار جهانی چند-دارایی"],
    "goal": ["چشم‌انداز ماه آینده", "کشف فرصت‌های کم‌ارزش", "تحلیل ریسک پرتفوی", "انتخاب سهم ماهانه"],
    "horizon": ["۱ ماه", "۳ ماه", "۶ ماه", "۱ سال"],
    "risk_profile": ["محافظه‌کار", "متعادل", "تهاجمی"],
    "risk_tolerance": ["محافظه‌کار", "متعادل", "تهاجمی"],
    "view": ["صعودی", "نزولی", "خنثی", "نوسانی"],
    "target_variable": ["بازده", "جهت حرکت", "نوسان"],
    "factor_type": ["ارزش", "مومنتوم", "کیفیت", "رشد"],
    "fund_type": ["سهامی", "درآمد ثابت", "مختلط", "شاخصی"],
    "sector": ["بانک", "انرژی", "نیمه‌هادی", "مصرفی"],
}


def _var_keyboard(var_name: str, page: int = 1, user_suggestions: list | None = None):
    """Keyboard for a swarm form variable with pagination (6 per page, 3 per row)."""
    suggestions = user_suggestions or VAR_SUGGESTIONS.get(var_name, [])
    rows = []

    PER_PAGE = 6
    total = len(suggestions)
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = max(1, min(page, pages))

    start = (page - 1) * PER_PAGE
    chunk = suggestions[start:start + PER_PAGE]

    for i in range(0, len(chunk), 3):
        rows.append([InlineKeyboardButton(text=s, callback_data=f"svar:{s[:56]}")
                     for s in chunk[i:i + 3]])

    # Pagination row (only if more than one page)
    if pages > 1:
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="◀ قبلی", callback_data=f"svpage:{var_name[:40]}:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"📄 {page}/{pages}", callback_data="noop"))
        if page < pages:
            nav.append(InlineKeyboardButton(text="بعدی ▶", callback_data=f"svpage:{var_name[:40]}:{page + 1}"))
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="✍️ نوشتن دستی", callback_data="svar:__text__")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _ask_next_swarm_var(callback_or_message, state: FSMContext, telegram_user_id: int):
    """Ask the next unset required variable; when all set → launch the run.

    Works for both CallbackQuery.message and Message senders.
    """
    data = await state.get_data()
    preset_name = data.get("sw_preset", "")
    variables = data.get("sw_vars", [])      # [{name, description, required}]
    answers = data.get("sw_answers", {})

    token = await get_user_token(telegram_user_id)
    if not token:
        return

    next_var = next((v for v in variables if v["name"] not in answers), None)

    if next_var is None:
        # All variables collected → launch
        user_vars = dict(answers)
        user_vars["output_language"] = "Persian (Farsi) — write the ENTIRE final report in fluent Persian"

        msg = (
            callback_or_message.message
            if hasattr(callback_or_message, "message") and not hasattr(callback_or_message, "chat")
            else callback_or_message
        )
        status = await msg.edit_text(
            f"🤖 در حال راه‌اندازی تیم...\n\n"
            f"پریست: {preset_name}\n"
            + "\n".join(f"• {k}: {v}" for k, v in answers.items())
            + "\n\n⏳ ۱۵-۲۰ دقیقه — پیشرفت لحظه‌ای همین‌جا نمایش داده می‌شود."
        )
        await state.set_state(ChatState.main_menu)

        result = await gateway.create_swarm_run(token, preset_name, user_vars)
        if "error" in result:
            await status.edit_text(f"❌ خطا: {result['error']}", reply_markup=back_to_menu_kb())
            return
        await _track_swarm_progress(status, callback_or_message, state, token, preset_name, result.get("id", ""))
        return

    # Ask the next variable
    name = next_var["name"]
    desc = next_var.get("description", "")
    required = next_var.get("required", False)
    optional_note = "" if required else " (اختیاری — بنویس «رد» تا خالی بماند)"

    ask_text = (
        f"🤖 **فرم تحلیل تیمی**\n\n"
        f"❓ {name}{optional_note}\n"
        f"_{desc}_\n\n"
        f"({len(answers) + 1} از {len(variables)})"
    )
    kb = _var_keyboard(name, page=1)

    msg = (
        callback_or_message.message
        if hasattr(callback_or_message, "message") and not hasattr(callback_or_message, "chat")
        else callback_or_message
    )
    await msg.edit_text(ask_text, reply_markup=kb, parse_mode="Markdown")
    await state.set_state(ChatState.swarm_var_input)
    await state.update_data(sw_current_var=name)

    if hasattr(callback_or_message, "answer"):
        await callback_or_message.answer()


@router.callback_query(F.data.startswith("svpage:"))
async def cb_svar_page(callback: CallbackQuery, state: FSMContext):
    """Pagination for a swarm form variable keyboard."""
    _, var_name, page_s = callback.data.split(":", 2)
    try:
        page = int(page_s)
    except ValueError:
        page = 1

    ask_text = f"❓ {var_name}\n(صفحه {page})"
    await callback.message.edit_text(
        ask_text,
        reply_markup=_var_keyboard(var_name, page=page),
    )
    await state.update_data(sw_current_var=var_name)
    await state.set_state(ChatState.swarm_var_input)
    await callback.answer()


async def _track_swarm_progress(status_msg, source, state: FSMContext, token: str,
                                preset_name: str, run_id: str):
    """Live per-agent progress loop (moved out of the old handler)."""
    last_text = ""
    for i in range(40):  # 40 * 30s = 20 min
        await asyncio.sleep(30)
        status = await gateway.get_swarm_run(token, run_id)
        if "error" in status:
            break
        current_status = status.get("status", "running")
        tasks = status.get("tasks", [])
        done = sum(1 for t in tasks if t.get("status") == "completed")
        total = len(tasks)

        progress_text = (
            f"🤖 تیم در حال کار...\n\n"
            f"پریست: {preset_name}\n"
            f"📊 پیشرفت: {done}/{total} ایجنت\n"
            f"⏳ {i // 2} دقیقه از ~۲۰ دقیقه\n\n"
        )
        for t in tasks:
            agent = t.get("agent_name", "?")[:20]
            s = t.get("status", "?")
            icon = {"completed": "✅", "in_progress": "🔄", "blocked": "⚠️", "failed": "❌"}.get(s, "⏳")
            progress_text += f"{icon} {agent}\n"

        if progress_text != last_text:
            try:
                await status_msg.edit_text(progress_text)
                last_text = progress_text
            except Exception:
                pass

        if current_status == "completed":
            report = status.get("final_report", "")
            for chunk_start in range(0, max(len(report), 1), 4000):
                chunk = report[chunk_start:chunk_start + 4000]
                if chunk:
                    await status_msg.answer(chunk)
            pdf_note = "✅ گزارش کامل بالا ارسال شد."
            try:
                pdf_bytes = await asyncio.to_thread(build_swarm_pdf, preset_name, preset_name, report, tasks)
                doc = BufferedInputFile(pdf_bytes, filename=f"swarm_{run_id[:16]}.pdf")
                await status_msg.answer_document(doc, caption="📄 نسخه PDF گزارش")
            except Exception as e:
                import logging
                logging.exception("swarm PDF build failed")
                pdf_note += f" (PDF ساخته نشد: {e})"
            kb = [[InlineKeyboardButton(text="« منوی اصلی", callback_data="menu")]]
            await status_msg.answer(pdf_note, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
            return
        elif current_status == "failed":
            await status_msg.edit_text("❌ اجرای تیم ناموفق بود.", reply_markup=back_to_menu_kb())
            return

    await status_msg.edit_text(
        "⏰ زمان انتظار تمام شد. بعداً وضعیت را چک کنید.",
        reply_markup=back_to_menu_kb(),
    )


@router.callback_query(F.data.startswith("svar:"))
async def cb_svar_pick(callback: CallbackQuery, state: FSMContext):
    """User picked a suggestion (or 'type manually')."""
    choice = callback.data.split(":", 1)[1]
    if choice == "__text__":
        await callback.message.edit_text("✍️ مقدار را بنویسید:")
        await state.set_state(ChatState.swarm_var_input)
        await callback.answer()
        return

    data = await state.get_data()
    var_name = data.get("sw_current_var", "")
    answers = data.get("sw_answers", {})
    answers[var_name] = choice
    await state.update_data(sw_answers=answers)
    await state.set_state(ChatState.main_menu)  # exit input state
    await _ask_next_swarm_var(callback, state, callback.from_user.id)


@router.message(ChatState.swarm_var_input)
async def process_swarm_var(message: Message, state: FSMContext):
    """Free-text answer for a swarm variable."""
    text = (message.text or "").strip()
    data = await state.get_data()
    var_name = data.get("sw_current_var", "")
    variables = data.get("sw_vars", [])
    answers = data.get("sw_answers", {})

    var_meta = next((v for v in variables if v["name"] == var_name), {})
    optional = not var_meta.get("required", False)

    if optional and text.lower() in ("رد", "skip", "-"):
        pass  # leave unset
    else:
        answers[var_name] = text

    await state.update_data(sw_answers=answers)
    await state.set_state(ChatState.main_menu)
    await _ask_next_swarm_var(message, state, message.from_user.id)


# ============================================================================
# Menu & Reports
# ============================================================================

@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🟢 **منوی اصلی**",
        reply_markup=main_menu_kb(),
        parse_mode="Markdown",
    )
    await state.set_state(ChatState.main_menu)
    await callback.answer()


# ============================================================================
# Alpha Zoo — factor research lab
# ============================================================================

ALPHA_ZOOS = {
    "qlib158": {"title": "Qlib 158", "count": 154, "desc": "فاکتورهای مایکروسافت Qlib"},
    "alpha101": {"title": "Alpha 101", "count": 101, "desc": "فاکتورهای WorldQuant"},
    "gtja191": {"title": "GTJA 191", "count": 191, "desc": "فاکتورهای گوه‌ژان"},
    "academic": {"title": "Academic", "count": 10, "desc": "فاکتورهای مقالات معروف"},
    "fundamental": {"title": "Fundamental", "count": 4, "desc": "فاکتورهای بنیادی"},
}

ALPHA_UNIVERSES = {
    "sp500": "S&P 500 (سهام آمریکا)",
    "csi300": "CSI 300 (سهام چین)",
}

ALPHA_PERIODS = {
    "2023-01-01/2023-12-31": "سال ۲۰۲۳",
    "2024-01-01/2024-12-31": "سال ۲۰۲۴",
    "2022-01-01/2024-12-31": "۳ سال اخیر",
}


@router.callback_query(F.data == "alphazoo")
async def cb_alphazoo(callback: CallbackQuery):
    token = await get_user_token(callback.from_user.id)
    if not token:
        await callback.message.edit_text("❌ ابتدا وارد شوید.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🧪 {v['title']} ({v['count']} فاکتور)",
            callback_data=f"az:zoo:{k}",
        )] for k, v in ALPHA_ZOOS.items()
    ] + [[InlineKeyboardButton(text="« منوی اصلی", callback_data="menu")]])

    await callback.message.edit_text(
        "🧪 **آزمایشگاه آلفا (Alpha Zoo)**\n\n"
        "کتابخانه ۴۶۰+ فاکتور کمی。\n"
        "یک مجموعه را انتخاب کنید تا فاکتورها را ببینید\n"
        "یا بک‌تست IC/IR بگیرید:",
        reply_markup=kb,
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("az:zoo:"))
async def cb_az_zoo(callback: CallbackQuery):
    zoo = callback.data.split(":")[2]
    token = await get_user_token(callback.from_user.id)

    info = ALPHA_ZOOS.get(zoo, {})
    result = await gateway.alpha_list(token, zoo=zoo)

    if "error" in result:
        await callback.message.edit_text(f"❌ {result['error']}", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    alphas = result.get("alphas", []) if isinstance(result, dict) else result
    total = result.get("total", len(alphas)) if isinstance(result, dict) else len(alphas)

    text = (
        f"🧪 **{info.get('title', zoo)}**\n"
        f"_{info.get('desc', '')}_\n\n"
        f"📊 تعداد فاکتورها: {total}\n\n"
        "**نمونه فاکتورها:**\n"
    )
    for a in alphas[:8]:
        aid = a.get("id", "?")
        nick = str(a.get("nickname", ""))[:45]
        themes = ",".join(a.get("theme", [])[:2])
        text += f"• `{aid}`\n  {nick}" + (f" [{themes}]" if themes else "") + "\n"

    if total > 8:
        text += f"\n... و {total - 8} فاکتور دیگر\n"

    kb = [
        [InlineKeyboardButton(text="🔬 اجرای بک‌تست IC/IR روی این مجموعه", callback_data=f"az:bench:{zoo}")],
        [InlineKeyboardButton(text="« آزمایشگاه آلفا", callback_data="alphazoo")],
        [InlineKeyboardButton(text="« منوی اصلی", callback_data="menu")],
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("az:bench:"))
async def cb_az_bench(callback: CallbackQuery, state: FSMContext):
    zoo = callback.data.split(":")[2]
    await state.update_data(az_zoo=zoo)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=v, callback_data=f"az:uni:{k}")] for k, v in ALPHA_UNIVERSES.items()
    ] + [[InlineKeyboardButton(text="« بازگشت", callback_data=f"az:zoo:{zoo}")]])

    await callback.message.edit_text(
        f"🔬 **بک‌تست فاکتورها — {ALPHA_ZOOS.get(zoo, {}).get('title', zoo)}**\n\n"
        "دسته‌دارایی را انتخاب کنید:\n\n"
        "⚠️ توجه: بک‌تست IC/IR به چندین دارایی نیاز دارد،\n"
        "پس فقط شاخص‌ها (S&P500/CSI300) قابل انتخاب هستند.",
        reply_markup=kb,
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("az:uni:"))
async def cb_az_universe(callback: CallbackQuery, state: FSMContext):
    universe = callback.data.split(":")[2]
    await state.update_data(az_universe=universe)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=v, callback_data=f"az:period:{k}")] for k, v in ALPHA_PERIODS.items()
    ])

    await callback.message.edit_text(
        "📅 **بازه زمانی را انتخاب کنید:**",
        reply_markup=kb,
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("az:period:"))
async def cb_az_period(callback: CallbackQuery, state: FSMContext):
    period = callback.data.split(":", 2)[2]
    data = await state.get_data()
    zoo = data.get("az_zoo", "academic")
    universe = data.get("az_universe", "sp500")
    token = await get_user_token(callback.from_user.id)

    status_msg = await callback.message.edit_text(
        f"🔬 بک‌تست شروع شد...\n\n"
        f"🧪 مجموعه: {ALPHA_ZOOS.get(zoo, {}).get('title', zoo)}\n"
        f"📊 دسته‌دارایی: {ALPHA_UNIVERSES.get(universe, universe)}\n"
        f"📅 بازه: {ALPHA_PERIODS.get(period, period)}\n\n"
        "⏳ ممکن است چند دقیقه طول بکشد\n(داده‌های شاخص دانلود می‌شود)...",
    )

    result = await gateway.alpha_bench(token, zoo=zoo, universe=universe, period=period, top=10)

    if "error" in result:
        await callback.message.edit_text(f"❌ {result['error']}", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    job_id = result.get("job_id", "")

    # Poll via the SSE stream — collect events until done
    import httpx
    settings_gw = GATEWAY_URL
    stream_url = f"{settings_gw}/api/v1/vibe/alpha/bench/{job_id}/stream"

    result_data = None
    error_msg = None
    try:
        async with httpx.AsyncClient(timeout=900.0) as client:
            async with client.stream(
                "GET",
                stream_url,
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                event_name = ""
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        payload = line[5:].strip()
                        try:
                            d = json.loads(payload)
                        except Exception:
                            d = {}
                        if event_name == "result":
                            result_data = d
                        elif event_name == "error":
                            error_msg = d.get("message", "خطای نامشخص")
                        elif event_name == "done":
                            break
    except Exception as e:
        error_msg = str(e)

    if error_msg:
        await callback.message.edit_text(f"❌ خطا در بک‌تست: {error_msg}", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    if not result_data:
        await callback.message.edit_text("⏰ نتیجه دریافت نشد. بعداً دوباره تلاش کنید.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    # Format the bench result
    alive = result_data.get("alive", 0)
    reversed_n = result_data.get("reversed", 0)
    dead = result_data.get("dead", 0)

    text = (
        f"🧪 **نتایج بک‌تست آلفا**\n\n"
        f"🧪 مجموعه: {ALPHA_ZOOS.get(zoo, {}).get('title', zoo)}\n"
        f"📊 دسته‌دارایی: {ALPHA_UNIVERSES.get(universe, universe)}\n"
        f"📅 بازه: {ALPHA_PERIODS.get(period, period)}\n\n"
        f"**📉 وضعیت فاکتورها:**\n"
        f"✅ زنده (IC>0): {alive}\n"
        f"🔄 معکوس (IC<0): {reversed_n}\n"
        f"💀 مرده: {dead}\n\n"
    )

    top5 = result_data.get("top5_by_ir", [])
    if top5:
        text += "**🏆 برترین فاکتورها (بر اساس IR):**\n\n"
        for i, row in enumerate(top5[:5], 1):
            aid = row.get("id", "?")
            ir = row.get("ir", 0)
            ic = row.get("ic_mean", 0)
            text += f"{i}. `{aid}`\n   IR: {ir:.3f} | IC: {ic:.3f}\n"

    by_theme = result_data.get("by_theme", {})
    if by_theme:
        text += "\n**🏷 بر اساس موضوع:**\n"
        for theme, stats in list(by_theme.items())[:6]:
            t_alive = stats.get("alive", 0)
            text += f"• {theme}: {t_alive} زنده\n"

    kb = [[InlineKeyboardButton(text="« آزمایشگاه آلفا", callback_data="alphazoo")],
          [InlineKeyboardButton(text="« منوی اصلی", callback_data="menu")]]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "reports")
async def cb_reports(callback: CallbackQuery):
    token = await get_user_token(callback.from_user.id)
    if not token:
        await callback.message.edit_text("❌ ابتدا وارد شوید.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    runs = await gateway.get_runs(token)
    if "error" in runs:
        await callback.message.edit_text(f"❌ {runs['error']}", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    if not isinstance(runs, list) or not runs:
        await callback.message.edit_text("📭 هنوز گزارشی ندارید.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    # Show all runs with detail buttons
    text = "📈 **گزارش‌های اخیر:**\n\n"
    kb_rows = []
    for i, run in enumerate(runs[:10]):
        status = run.get("status", "?")
        total = run.get("total_return")
        sharpe = run.get("sharpe")
        prompt = str(run.get("prompt", "بکتست"))[:35]
        run_id = run.get("run_id", "")

        if total is not None or sharpe is not None:
            icon = "✅" if status == "success" else "⏳" if status == "running" else "❌"
            text += f"{icon} {prompt}"
            if total is not None:
                text += f" | بازده: {total:.1%}"
            if sharpe is not None:
                text += f" | شارپ: {sharpe:.2f}"
        else:
            icon = "💬" if status == "success" else "⏳" if status == "running" else "❌"
            text += f"{icon} {prompt}"
        text += "\n"

        # Add detail button for backtests with metrics
        if run_id and (total is not None or sharpe is not None):
            kb_rows.append([InlineKeyboardButton(text=f"📊 جزئیات #{i+1}", callback_data=f"detail:{run_id}")])

    kb_rows.append([InlineKeyboardButton(text="« منوی اصلی", callback_data="menu")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("detail:"))
async def cb_run_detail(callback: CallbackQuery):
    """Show full backtest report: metrics + risk + chart + trades."""
    run_id = callback.data.split(":", 1)[1]
    token = await get_user_token(callback.from_user.id)
    if not token:
        await callback.message.edit_text("❌ ابتدا وارد شوید.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    await callback.message.edit_text("📊 در حال بارگذاری گزارش...")

    detail = await gateway.get_run_detail(token, run_id)
    if "error" in detail:
        await callback.message.edit_text(f"❌ {detail['error']}", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    metrics = detail.get("metrics", {})
    prompt = str(detail.get("prompt", "بکتست"))[:60]
    elapsed = detail.get("elapsed_seconds", 0)
    rx = detail.get("risk_xray") or {}

    # ---------- Text report ----------
    text = f"📊 **گزارش بکتست**\n\n📝 {prompt}\n"
    codes = (detail.get("run_context") or {}).get("codes") or []
    if codes:
        text += f"🏷 دارایی‌ها: {', '.join(str(c) for c in codes[:4])}\n"
    ctx = (detail.get("run_context") or {})
    if ctx.get("start_date") or ctx.get("end_date"):
        text += f"📅 بازه: {ctx.get('start_date', '?')} → {ctx.get('end_date', '?')}\n"

    # Returns & risk
    text += "\n**📈 بازده:**\n"
    if metrics.get("final_value"):
        text += f"💰 ارزش نهایی: ${metrics['final_value']:,.0f}\n"
    if metrics.get("total_return") is not None:
        text += f"📈 بازده کل: {metrics['total_return']:+.1%}\n"
    if metrics.get("annual_return") is not None:
        text += f"📅 سالانه: {metrics['annual_return']:+.1%}\n"

    text += "\n**⚠️ ریسک:**\n"
    if metrics.get("max_drawdown") is not None:
        text += f"📉 حداکثر افت: {metrics['max_drawdown']:.1%}\n"
    vol = (rx.get("volatility") or {}).get("annualized_vol") or metrics.get("risk_xray_annualized_vol")
    if vol is not None:
        text += f"🌊 نوسان سالانه: {vol:.1%}\n"
    if metrics.get("sharpe") is not None:
        text += f"📊 شارپ: {metrics['sharpe']:.2f}\n"
    if metrics.get("sortino") is not None:
        text += f"📊 سورتینو: {metrics['sortino']:.2f}\n"
    if metrics.get("calmar") is not None:
        text += f"📊 کالمار: {metrics['calmar']:.2f}\n"
    var95 = (rx.get("tail_risk") or {}).get("var_95")
    if var95 is not None:
        text += f"🎲 VaR 95% روزانه: {var95:.1%}\n"

    # Trades
    text += "\n**🔄 فعالیت:**\n"
    if metrics.get("win_rate") is not None:
        text += f"🎯 نرخ برد: {metrics['win_rate']:.0%}\n"
    if metrics.get("trade_count") is not None:
        text += f"🔁 معاملات کامل: {metrics['trade_count']}\n"
    if metrics.get("avg_holding_days") is not None:
        text += f"⏱ میانگین نگهداری: {metrics['avg_holding_days']:.0f} روز\n"
    turnover = metrics.get("total_turnover")
    if turnover is not None:
        text += f"💳 گردش کل: {turnover:,.1f}\n"

    # Benchmark
    if metrics.get("benchmark_return") is not None:
        text += "\n**📊 بنچمارک (خرید و نگهداری):**\n"
        text += f"📈 بازده B&H: {metrics['benchmark_return']:+.1%}\n"
        if metrics.get("excess_return") is not None:
            emoji = "🟢" if metrics["excess_return"] >= 0 else "🔴"
            text += f"{emoji} بازده مازاد: {metrics['excess_return']:+.1%}\n"
        if metrics.get("information_ratio") is not None:
            text += f"📊 نسبت اطلاعات: {metrics['information_ratio']:.2f}\n"
        if metrics.get("tracking_error") is not None:
            text += f"🌊 خطای ردیابی: {metrics['tracking_error']:.1%}\n"

    await callback.message.edit_text(text)

    # ---------- Chart (PNG) ----------
    equity = detail.get("equity_curve")
    price_series = detail.get("price_series") or {}
    # First symbol's price series doubles as the buy & hold benchmark
    benchmark = None
    if isinstance(price_series, dict) and price_series:
        first = next(iter(price_series.values()))
        if isinstance(first, list):
            benchmark = first
    elif isinstance(price_series, list) and price_series:
        benchmark = price_series
    trade_markers = detail.get("trade_markers") or []

    chart_sent = False
    if equity and isinstance(equity, list) and len(equity) > 4:
        try:
            # NOTE: sync function — run in a thread to avoid blocking the event loop
            import asyncio as _aio
            chart_path = await _aio.to_thread(
                _render_equity_chart, run_id, equity, metrics, benchmark, trade_markers
            )
            if chart_path:
                photo = FSInputFile(chart_path)
                await callback.message.answer_photo(
                    photo,
                    caption=(
                        f"📈 استراتژی: {metrics.get('total_return', 0):+.1%}   |   "
                        f"B&H: {metrics.get('benchmark_return', 0):+.1%}   |   "
                        f"MDD: {metrics.get('max_drawdown', 0):.1%}"
                    ),
                )
                chart_sent = True
        except Exception:
            pass

    if not chart_sent and equity:
        spark = _sparkline_equity(equity)
        if spark:
            await callback.message.answer(f"📈 روند سرمایه:\n{spark}")

    # ---------- Trades table ----------
    trade_log = detail.get("trade_log") or []
    if trade_log:
        table = _format_trades_table(trade_log)
        if table:
            try:
                await callback.message.answer(table, parse_mode="Markdown")
            except Exception:
                await callback.message.answer(table.replace("**", "").replace("```", ""))

    # ---------- PDF download button ----------
    kb = [
        [InlineKeyboardButton(text="📄 دانلود گزارش PDF", callback_data=f"pdfrun:{run_id}")],
        [InlineKeyboardButton(text="« گزارش‌ها", callback_data="reports")],
        [InlineKeyboardButton(text="« منوی اصلی", callback_data="menu")],
    ]
    await callback.message.answer(
        "گزارش کامل بالا نمایش داده شد. برای دریافت نسخه PDF دکمه زیر را بزنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pdfrun:"))
async def cb_pdf_run(callback: CallbackQuery):
    """Generate and send the backtest report as a PDF document."""
    run_id = callback.data.split(":", 1)[1]
    token = await get_user_token(callback.from_user.id)
    if not token:
        await callback.answer("ابتدا وارد شوید", show_alert=True)
        return

    wait = await callback.message.answer("📄 در حال ساخت PDF...")
    try:
        import asyncio as _aio
        detail = await gateway.get_run_detail(token, run_id)
        if "error" in detail:
            await wait.edit_text(f"❌ {detail['error']}")
            await callback.answer()
            return

        pdf_bytes = await _aio.to_thread(build_backtest_pdf, detail)

        filename = f"backtest_{run_id[:16]}.pdf"
        doc = BufferedInputFile(pdf_bytes, filename=filename)
        await wait.delete()
        await callback.message.answer_document(
            doc,
            caption=f"📊 گزارش بکتست — بازده: {detail.get('metrics', {}).get('total_return', 0):+.1%}",
        )
    except Exception as e:
        await wait.edit_text(f"❌ خطا در ساخت PDF: {e}")
    await callback.answer()


@router.callback_query(F.data == "subscription")
async def cb_subscription(callback: CallbackQuery):
    token = await get_user_token(callback.from_user.id)
    if not token:
        await callback.message.edit_text("❌ ابتدا وارد شوید.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    result = await gateway.request("GET", "/api/v1/subscription/current", token=token)
    plan = result.get("plan", "free")
    limits = result.get("limits", {})

    text = (
        f"👤 **اشتراک شما:**\n\n"
        f"📊 پلن: **{plan}**\n"
        f"💬 جلسات روزانه: {limits.get('sessions_per_day', '?')}\n"
        f"📨 پیام‌ها: {limits.get('messages_per_day', '?')}\n"
        f"📊 بک‌تست: {limits.get('backtests_per_day', '?')}\n"
        f"🤖 Swarm: {limits.get('swarm_per_day', '?')}\n"
    )

    await callback.message.edit_text(text, reply_markup=back_to_menu_kb(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "settings")
async def cb_settings(callback: CallbackQuery):
    await callback.message.edit_text(
        "⚙️ **تنظیمات**\n\n"
        "برای تغییر پلن با پشتیبانی تماس بگیرید.",
        reply_markup=back_to_menu_kb(),
        parse_mode="Markdown",
    )
    await callback.answer()


# ============================================================================
# Main
# ============================================================================

async def main():
    bot = Bot(token=BOT_TOKEN)

    # Use Redis-backed FSM storage (persists across restarts)
    storage = RedisStorage.from_url(
        REDIS_URL,
        key_builder=DefaultKeyBuilder(with_bot_id=True),
    )
    dp = Dispatcher(storage=storage)
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="start", description="شروع / منوی اصلی"),
        BotCommand(command="menu", description="منوی اصلی"),
    ])

    print("[Bot] Starting Telegram bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
