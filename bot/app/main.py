"""Vibe-Trading SaaS — Telegram Bot (aiogram 3.x)"""

from __future__ import annotations

import asyncio
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

    async def get_swarm_presets(self, token: str) -> dict:
        return await self.request("GET", "/api/v1/vibe/swarm/presets", token=token)

    async def create_swarm_run(self, token: str, preset_name: str, user_vars: dict) -> dict:
        return await self.request("POST", "/api/v1/vibe/swarm/runs", token=token, json={"preset_name": preset_name, "user_vars": user_vars})

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
        [InlineKeyboardButton(text="📈 گزارش‌ها", callback_data="reports")],
        [InlineKeyboardButton(text="👤 اشتراک من", callback_data="subscription")],
        [InlineKeyboardButton(text="⚙️ تنظیمات", callback_data="settings")],
    ])


def back_to_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« منوی اصلی", callback_data="menu")]
    ])


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
        if status_code == 429:
            await message.answer(f"⏳ {result['error']}", reply_markup=back_to_menu_kb())
        else:
            await message.answer(f"❌ خطا: {result['error']}")
        return

    # Poll for response
    thinking_msg = await message.answer("🔄 **در حال پردازش...**", parse_mode="Markdown")

    for _ in range(90):
        await asyncio.sleep(1)
        messages = await gateway.get_messages(token, session_id)
        if messages and isinstance(messages, list):
            for msg in reversed(messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    answer = msg["content"]
                    try:
                        await thinking_msg.delete()
                    except Exception:
                        pass
                    # Split long messages (Telegram limit ~4096)
                    for chunk_start in range(0, max(len(answer), 1), 4000):
                        chunk = answer[chunk_start:chunk_start + 4000]
                        if chunk:
                            await message.answer(chunk, parse_mode="Markdown")
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

    await callback.message.edit_text(
        f"🤖 **در حال راه‌اندازی تیم...**\n\n"
        f"پریست: `{preset_name}`\n"
        f"⏳ زمان تقریبی: ۱۵-۲۰ دقیقه\n"
        f"📊 گزارش فارسی تحویل داده خواهد شد.",
        parse_mode="Markdown",
    )

    result = await gateway.create_swarm_run(token, preset_name, {})

    if "error" in result:
        await callback.message.edit_text(f"❌ خطا: {result['error']}", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    run_id = result.get("id")
    await callback.message.edit_text(
        f"✅ **تیم شروع به کار کرد!**\n\n"
        f"شناسه: `{run_id}`\n"
        f"⏳ ۱۵-۲۰ دقیقه زمان می‌برد.\n"
        f"📊 با /status وضعیت را بررسی کنید.",
        parse_mode="Markdown",
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

    text = "📈 **گزارش‌های اخیر:**\n\n"
    for run in runs[:10]:
        status = run.get("status", "?")
        total = run.get("total_return")
        icon = "✅" if status == "success" else "⏳" if status == "running" else "❌"
        text += f"{icon} {str(run.get('prompt', 'N/A'))[:50]}"
        if total is not None:
            text += f" | بازده: {total:.1%}"
        text += "\n"

    await callback.message.edit_text(text, reply_markup=back_to_menu_kb(), parse_mode="Markdown")
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
