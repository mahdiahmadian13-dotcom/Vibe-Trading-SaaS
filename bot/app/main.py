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
from aiogram.types import FSInputFile

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


def _render_equity_chart(run_id: str, equity, metrics: dict) -> str | None:
    """Render equity curve + drawdown chart to a PNG. Returns path or None."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    values, labels = _parse_equity_points(equity)
    if len(values) < 4:
        return None

    # Downsample if huge
    if len(values) > 500:
        step = len(values) // 500
        values = values[::step]
        labels = labels[::step]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True,
                                    gridspec_kw={"height_ratios": [3, 1]})
    fig.patch.set_facecolor("#0d1117")
    for ax in (ax1, ax2):
        ax.set_facecolor("#0d1117")
        ax.tick_params(colors="#8b949e")
        for spine in ax.spines.values():
            spine.set_color("#30363d")
        ax.grid(True, alpha=0.15, color="#8b949e")

    # Equity curve
    x = range(len(values))
    ax1.plot(x, values, color="#2ea043", linewidth=1.6)
    ax1.fill_between(x, values, min(values), color="#2ea043", alpha=0.12)
    ax1.set_ylabel("Equity ($)", color="#8b949e")
    ax1.set_title(
        f"Equity Curve — Return: {metrics.get('total_return', 0):.1%} | Sharpe: {metrics.get('sharpe', 0):.2f}",
        color="#e6edf3", fontsize=11,
    )

    # Drawdown
    peak = values[0]
    dd = []
    for v in values:
        peak = max(peak, v)
        dd.append((v - peak) / peak if peak else 0)
    ax2.fill_between(x, dd, 0, color="#f85149", alpha=0.4)
    ax2.plot(x, dd, color="#f85149", linewidth=1)
    ax2.set_ylabel("Drawdown", color="#8b949e")

    # X labels: show a few dates
    if labels and any(labels):
        tick_idx = [i for i in range(0, len(labels), max(1, len(labels) // 6))]
        ax2.set_xticks(tick_idx)
        ax2.set_xticklabels([labels[i][:10] for i in tick_idx], rotation=0, fontsize=7)

    plt.tight_layout()
    path = f"/tmp/equity_{run_id[:20]}.png"
    fig.savefig(path, dpi=110, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


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

    session_id = await get_user_session(callback.from_user.id)
    if not session_id:
        result = await gateway.create_session(token)
        if "error" in result:
            await callback.message.edit_text(f"❌ خطا: {result['error']}", reply_markup=back_to_menu_kb())
            await callback.answer()
            return
        session_id = result.get("session_id")
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

    result = await gateway.send_message(token, session_id, message.text)

    if "error" in result:
        status_code = result.get("status", 500)
        error_text = str(result.get("error", ""))
        if status_code == 429:
            await message.answer(f"⏳ {error_text}", reply_markup=back_to_menu_kb())
        elif status_code == 409 or "already has a run" in error_text:
            # Engine is still processing previous message — wait and retry
            await message.answer("⏳ درخواست قبلی هنوز در حال پردازش است. لطفاً چند ثانیه صبر کنید...")
            await asyncio.sleep(10)
            # Retry once
            result = await gateway.send_message(token, session_id, message.text)
            if "error" in result:
                await message.answer(f"❌ خطا: {result.get('error', 'خطای ناشناخته')}")
                return
        else:
            await message.answer(f"❌ خطا: {error_text}")
            return

    # Poll for response
    thinking_msg = await message.answer("🔄 در حال پردازش...")

    # Track last assistant message to avoid duplicates
    last_answer_hash = None

    for _ in range(90):
        await asyncio.sleep(1)
        messages = await gateway.get_messages(token, session_id)
        if messages and isinstance(messages, list):
            for msg in reversed(messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    answer = msg["content"]
                    answer_hash = hash(answer)
                    if answer_hash == last_answer_hash:
                        continue  # Skip duplicate
                    last_answer_hash = answer_hash
                    try:
                        await thinking_msg.delete()
                    except Exception:
                        pass
                    # Send answer as plain text (avoid Markdown parse errors)
                    for chunk_start in range(0, max(len(answer), 1), 4000):
                        chunk = answer[chunk_start:chunk_start + 4000]
                        if chunk:
                            try:
                                await message.answer(chunk)
                            except Exception:
                                pass
                    return

    try:
        await thinking_msg.delete()
    except Exception:
        pass
    await message.answer("⏰ پاسخ دریافت نشد. لطفاً دوباره تلاش کنید.")


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

    kb_lines = []
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


@router.callback_query(F.data.startswith("sw:"))
async def cb_swarm_run(callback: CallbackQuery, state: FSMContext):
    preset_name = callback.data.split(":", 1)[1]
    token = await get_user_token(callback.from_user.id)

    status_msg = await callback.message.edit_text(
        f"🤖 در حال راه‌اندازی تیم...\n\n"
        f"پریست: {preset_name}\n"
        f"⏳ زمان تقریبی: ۱۵-۲۰ دقیقه\n"
        f"📊 گزارش فارسی تحویل داده خواهد شد.",
    )

    result = await gateway.create_swarm_run(token, preset_name, {})

    if "error" in result:
        await callback.message.edit_text(f"❌ خطا: {result['error']}", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    run_id = result.get("id")

    # Live progress tracking — edit message every 30s
    last_text = ""
    for i in range(1200):  # max 20 min
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
        # Per-agent status
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
                pass  # Message unchanged or edit failed

        if current_status == "completed":
            report = status.get("final_report", "")
            kb = []
            # Report in chunks of 4000 chars
            for chunk_start in range(0, max(len(report), 1), 4000):
                chunk = report[chunk_start:chunk_start + 4000]
                if chunk:
                    await callback.message.answer(chunk)
            kb.append([InlineKeyboardButton(text="« منوی اصلی", callback_data="menu")])
            await callback.message.answer("✅ گزارش کامل بالا ارسال شد.", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
            return
        elif current_status == "failed":
            await callback.message.edit_text(
                "❌ اجرای تیم ناموفق بود. دوباره تلاش کنید.",
                reply_markup=back_to_menu_kb(),
            )
            return

    await callback.message.edit_text(
        "⏰ زمان انتظار تمام شد. بعداً از «🤖 تیم‌های تحلیل» وضعیت را چک کنید.",
        reply_markup=back_to_menu_kb(),
    )
    await callback.answer()


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
    """Show full backtest report for a specific run."""
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

    # Format full report
    metrics = detail.get("metrics", {})
    prompt = str(detail.get("prompt", "بکتست"))[:50]
    status = detail.get("status", "?")
    elapsed = detail.get("elapsed_seconds", 0)

    text = f"📊 **گزارش بکتست**\n\n"
    text += f"📝 {prompt}\n"
    text += f"⏱ زمان اجرا: {elapsed:.0f} ثانیه\n\n"

    # Key metrics
    text += "**📈 معیارهای کلیدی:**\n"
    if metrics.get("final_value"):
        text += f"💰 ارزش نهایی: ${metrics['final_value']:,.0f}\n"
    if metrics.get("total_return") is not None:
        text += f"📈 بازده کل: {metrics['total_return']:.1%}\n"
    if metrics.get("annual_return") is not None:
        text += f"📅 بازده سالانه: {metrics['annual_return']:.1%}\n"
    if metrics.get("sharpe") is not None:
        text += f"📊 شارپ: {metrics['sharpe']:.2f}\n"
    if metrics.get("max_drawdown") is not None:
        text += f"📉 حداکثر افت: {metrics['max_drawdown']:.1%}\n"
    if metrics.get("win_rate") is not None:
        text += f"🎯 نرخ برد: {metrics['win_rate']:.0%}\n"
    if metrics.get("trade_count") is not None:
        text += f"🔄 تعداد معاملات: {metrics['trade_count']}\n"
    if metrics.get("calmar") is not None:
        text += f"📊 کالمار: {metrics['calmar']:.2f}\n"
    if metrics.get("sortino") is not None:
        text += f"📊 سورتینو: {metrics['sortino']:.2f}\n"
    if metrics.get("avg_holding_days") is not None:
        text += f"⏱ مدت نگهداری: {metrics['avg_holding_days']:.0f} روز\n"

    # Benchmark comparison
    if metrics.get("benchmark_return") is not None:
        text += f"\n**📊 مقایسه با بنچمارک:**\n"
        text += f"📈 بازده بنچمارک: {metrics['benchmark_return']:.1%}\n"
        if metrics.get("excess_return") is not None:
            text += f"📊 بازده مازاد: {metrics['excess_return']:.1%}\n"
        if metrics.get("information_ratio") is not None:
            text += f"📊 نسبت اطلاعات: {metrics['information_ratio']:.2f}\n"

    # Trade log summary
    trade_log = detail.get("trade_log", [])
    if trade_log:
        text += f"\n**📋 خلاصه معاملات:** {len(trade_log)} معامله\n"

    # Strategy spec
    strategy = detail.get("strategy_spec", "")
    if strategy:
        text += f"\n**🧠 استراتژی:**\n{str(strategy)[:200]}\n"

    await callback.message.edit_text(text)
    await callback.answer()

    # Equity curve as an image chart (matplotlib) — sent as a photo
    equity = detail.get("equity_curve")
    if equity and isinstance(equity, list) and len(equity) > 2:
        try:
            chart_path = await _render_equity_chart(run_id, equity, metrics)
            if chart_path:
                photo = FSInputFile(chart_path)
                await callback.message.answer_photo(
                    photo,
                    caption=f"📈 نمودار سرمایه — بازده کل: {metrics.get('total_return', 0):.1%}",
                )
        except Exception as e:
            # Chart rendering failed — fall back to text sparkline
            spark = _sparkline_equity(equity)
            if spark:
                await callback.message.answer(f"📈 روند سرمایه:\n```\n{spark}\n```")


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
