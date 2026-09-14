"""Vibe-Trading SaaS — Telegram Bot v2 (aiogram 3.x)

A professional, minimal hub bot. The Web App is the primary UI:
  • /start      → hero welcome card + [🚀 ورود به پلتفرم] Web App button
  • My Channel  → configurable channel link
  • 🎁 Referral → personal deep-link + live invite stats
Legacy chat/register flows were removed — everything happens in the Web App.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

import httpx
import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BotCommand, WebAppInfo,
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.storage.redis import RedisStorage, DefaultKeyBuilder

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# ============================================================================
# Config
# ============================================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:9000")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
WEBAPP_URL = os.getenv("TELEGRAM_WEBAPP_URL", "")            # https://…/app/
BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "")        # without @
CHANNEL_URL = os.getenv("TELEGRAM_CHANNEL_URL", "")          # https://t.me/…

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

BRAND = "Vibe Trading"
EMOJI = "📊"


# ============================================================================
# Gateway Client (read-only: referral stats + auto-login token)
# ============================================================================

class GatewayClient:
    def __init__(self, base_url: str):
        self.base_url = base_url

    async def request(self, method: str, path: str, token: str = None, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.request(method, url, headers=headers, **kwargs)
                if resp.status_code >= 400:
                    try:
                        return {"error": resp.json().get("detail", "خطا"), "status": resp.status_code}
                    except Exception:
                        return {"error": resp.text, "status": resp.status_code}
                return resp.json()
        except Exception as e:
            return {"error": str(e), "status": 503}

    async def auth_telegram(self, init_data: str, start_param: str = None) -> dict:
        body = {"init_data": init_data}
        if start_param:
            body["start_param"] = start_param
        return await self.request("POST", "/api/v1/auth/telegram", json=body)

    async def referrals_me(self, token: str) -> dict:
        return await self.request("GET", "/api/v1/referrals/me", token=token)


gateway = GatewayClient(GATEWAY_URL)


# ============================================================================
# Redis token store (tg:<id>:token → gateway JWT)
# ============================================================================

_redis = None


async def get_redis():
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


async def get_user_token(user_id: int) -> str | None:
    r = await get_redis()
    return await r.get(f"tg:{user_id}:token")


async def set_user_token(user_id: int, token: str):
    r = await get_redis()
    await r.set(f"tg:{user_id}:token", token, ex=60 * 60 * 24 * 30)


async def del_user_token(user_id: int):
    r = await get_redis()
    await r.delete(f"tg:{user_id}:token")


# ============================================================================
# Keyboards
# ============================================================================

def webapp_kb(ref_code: str = "") -> InlineKeyboardMarkup:
    """Hero keyboard — Web App first, always visible.

    ref_code: when the user arrived via a ?start=<code> deep link, we thread
    the code into the WebApp URL (?ref=) so the gateway sees the attribution
    even on first open (browser WebApp URL params, not initData start_param).
    """
    if WEBAPP_URL:
        url = WEBAPP_URL
        if ref_code:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}ref={ref_code}"
        rows = [[InlineKeyboardButton(
            text="🚀 ورود به پلتفرم",
            web_app=WebAppInfo(url=url),
        )]]
    else:
        # Fallback when no HTTPS WebApp URL is configured: plain link button
        rows = [[InlineKeyboardButton(
            text="🚀 ورود به پلتفرم",
            url=f"https://t.me/{BOT_USERNAME}" if BOT_USERNAME else "https://t.me",
        )]]
    if CHANNEL_URL:
        rows.append([InlineKeyboardButton(text="📢 کانال ما", url=CHANNEL_URL)])
    rows.append([InlineKeyboardButton(text="🎁 دعوت دوستان", callback_data="referral")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« بازگشت", callback_data="menu")],
    ])


def share_kb(ref_link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📤 ارسال برای دوستان",
            switch_inline_query=f"بیا با {BRAND} استراتژی‌هات رو قبل از ریسک واقعی بک‌تست کن 👇\n{ref_link}",
        )],
        [InlineKeyboardButton(text="🚀 ورود به پلتفرم", callback_data="menu")],
    ])


def home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« منوی اصلی", callback_data="menu")],
    ])


# ============================================================================
# Messages (professional Persian copy)
# ============================================================================

WELCOME_NEW = (
    f"{EMOJI} **به {BRAND} خوش اومدی!**\n\n"
    "🧪 بک‌تست حرفه‌ای استراتژی‌ها با داده واقعی بازار\n"
    "🤖 تیم‌های هوش مصنوعی چندعاملی\n"
    "📈 گزارش‌های کامل با نمودار و PDF\n"
    "🎟️ با ۳ کوپن خوش‌آمد رایگان شروع کن\n\n"
    "همه‌چیز داخل پلتفرم یک‌جا انجام می‌شه —\n"
    "روی دکمه زیر بزن تا وارد بشی 👇"
)

WELCOME_BACK = (
    f"{EMOJI} **سلام دوباره!** 👋\n\n"
    "داشبورد، چت تحلیلگر و بک‌تست‌هات همگی منتظرتن.\n"
    "برای ادامه، پلتفرم رو باز کن 👇"
)

REFERRAL_TXT = (
    "🎁 **دعوت دوستان، گرفتن کوپن!**\n\n"
    "لینک اختصاصی‌تو بفرست برای دوستات. هر دوستی که با لینک تو ثبت‌نام کنه:\n"
    "✅ **۲ کوپن بک‌تست** به‌عنوان جایزه برای تو می‌شه\n"
    "✅ دوستت هم ۳ کوپن خوش‌آمد می‌گیره\n\n"
    "**لینک دعوت تو:**\n`{ref_link}`\n\n"
    "**👥 دعوت‌شده‌ها:** {invited} نفر\n"
    "**🎟️ کوپن‌های جایزه:** {coupons} عدد"
)

REFERRAL_TXT_OFFLINE = (
    "🎁 **دعوت دوستان**\n\n"
    "فعلاً آمار دعوت در دسترس نیست، ولی لینک اختصاصی‌تو همین الان بردار:\n"
    "`{ref_link}`"
)

HELP_TXT = (
    f"{EMOJI} **راهنمای {BRAND}**\n\n"
    "🚀 **ورود به پلتفرم** — داشبورد کامل: چت تحلیلگر، بک‌تست، سوارم و گزارش‌ها\n"
    "📢 **کانال ما** — اعلان‌ها، آپدیت‌ها و سیگنال‌های ویژه\n"
    "🎁 **دعوت دوستان** — با هر دعوت موفق ۲ کوپن بک‌تست جایزه بگیر\n\n"
    "سوال یا مشکل؟ توی کانال باهاتون هستیم."
)


# ============================================================================
# Handlers
# ============================================================================

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Hero welcome. /start <ref_code> captures referral attribution.

    Accepts BOTH the gateway link format (?start=<code>) and the legacy
    ref_-prefixed format (?start=ref_<code>) — the official invite link is
    t.me/<bot>?start=<code> (no prefix).
    """
    payload = message.text.split(" ", 1)[1].strip() if " " in message.text else ""
    if payload.startswith("ref_"):
        payload = payload[4:]
    start_param = payload if len(payload) >= 4 else None  # ref codes are ≥4 chars

    seen_before = await get_user_token(message.from_user.id)

    if start_param:
        # remember the ref code so the WebApp can pick it up via /start deep link
        r = await get_redis()
        await r.set(f"tg:{message.from_user.id}:ref", start_param, ex=60 * 60 * 24)

    kb = webapp_kb(start_param or "")
    txt = WELCOME_BACK if seen_before else WELCOME_NEW
    await message.answer(txt, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery):
    seen_before = await get_user_token(callback.from_user.id)
    await callback.message.edit_text(
        WELCOME_BACK if seen_before else WELCOME_NEW,
        reply_markup=webapp_kb(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "referral")
async def cb_referral(callback: CallbackQuery):
    user_id = callback.from_user.id
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_CODE" if BOT_USERNAME else ""

    # get the actual code from the gateway if the user has a token
    token = await get_user_token(user_id)
    invited, coupons = 0, 0
    if token:
        data = await gateway.referrals_me(token)
        if not data.get("error"):
            ref_link = data.get("tg_link") or ref_link
            invited = data.get("invited", 0)
            coupons = data.get("reward_coupons", 0)
            txt = REFERRAL_TXT.format(ref_link=ref_link, invited=invited, coupons=coupons)
            await callback.message.edit_text(txt, reply_markup=share_kb(ref_link), parse_mode="Markdown")
            await callback.answer()
            return

    # no token yet → derive code from tg id deterministically as a fallback display
    if not ref_link:
        await callback.message.edit_text(
            "🎁 برای فعال‌شدن لینک دعوت، اول یک‌بار وارد پلتفرم شو.",
            reply_markup=back_kb(),
        )
        await callback.answer()
        return

    txt = REFERRAL_TXT_OFFLINE.format(ref_link=ref_link)
    await callback.message.edit_text(txt, reply_markup=back_kb(), parse_mode="Markdown")
    await callback.answer()


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TXT, reply_markup=webapp_kb(), parse_mode="Markdown")


@router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery):
    await callback.message.edit_text(HELP_TXT, reply_markup=home_kb(), parse_mode="Markdown")
    await callback.answer()


# ============================================================================
# WebApp auto-login bridge — when the WebApp opens it sends its initData via
# the gateway directly; the bot never sees it. This handler only exists to
# keep /start ref_<code> attribution alive (see cmd_start).
# ============================================================================

# ============================================================================
# Main
# ============================================================================

async def main():
    bot = Bot(token=BOT_TOKEN)

    storage = RedisStorage.from_url(
        REDIS_URL,
        key_builder=DefaultKeyBuilder(with_bot_id=True),
    )
    dp = Dispatcher(storage=storage)
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="start", description="ورود به پلتفرم 🚀"),
        BotCommand(command="help", description="راهنما"),
    ])

    # Professional touch: menu button = Web App (the little ≡ next to the ✏️)
    if WEBAPP_URL:
        from aiogram.types import MenuButtonWebApp
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="🚀 پلتفرم", web_app=WebAppInfo(url=WEBAPP_URL)))

    print(f"[Bot] v2 hub starting — webapp={WEBAPP_URL or 'NOT SET'} channel={'set' if CHANNEL_URL else 'unset'}")
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
