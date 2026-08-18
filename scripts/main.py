#!/usr/bin/env python3
"""
Tractor AI Translator Bot
─────────────────────────
A Telegram bot that translates text, images, audio, and stickers
using the Gemini API. Supports group mode, multi-language output,
and an admin dashboard.

All logic lives in this single file.
"""

import os
import io
import re
import sqlite3
import logging
import datetime
from enum import Enum
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

import google.generativeai as genai
from PIL import Image

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
ADMIN_IDS: list[int] = [
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
]
DB_PATH: str = os.getenv("DB_PATH", "tractor_bot.db")

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is not set in environment / .env")
if not GEMINI_API_KEY:
    raise SystemExit("GEMINI_API_KEY is not set in environment / .env")

genai.configure(api_key=GEMINI_API_KEY)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("TractorBot")

# ─────────────────────────────────────────────
# LANGUAGE / MODEL CONSTANTS
# ─────────────────────────────────────────────
LANGUAGES: dict[str, str] = {
    "hi": "Hindi",
    "en": "English",
    "bn": "Bengali",
    "ta": "Tamil",
    "te": "Telugu",
    "mr": "Marathi",
    "gu": "Gujarati",
    "kn": "Kannada",
    "ml": "Malayalam",
    "pa": "Punjabi",
    "ur": "Urdu",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt": "Portuguese",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese (Simplified)",
    "ar": "Arabic",
    "ru": "Russian",
    "hinglish": "Hinglish",
}

# Short language labels for button rows (2 per row)
LANG_BUTTON_ROWS: list[list[tuple[str, str]]] = []
_items = list(LANGUAGES.items())
for i in range(0, len(_items), 2):
    row = _items[i : i + 2]
    LANG_BUTTON_ROWS.append(row)

MODELS: dict[str, str] = {
    "gemini-3.5-flash-lite": "Gemini 3.5 Flash Lite [Default]",
    "gemini-3.7-flash": "Gemini 3.7 Flash",
    "gemini-3.6-flash": "Gemini 3.6 Flash",
    "gemini-3.5-flash": "Gemini 3.5 Flash",
    "gemini-3.1-flash-lite": "Gemini 3.1 Flash Lite",
}


# ─────────────────────────────────────────────
# DATABASE  (SQLite – single file)
# ─────────────────────────────────────────────
def get_db() -> sqlite3.Connection:
    """Return a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id       INTEGER PRIMARY KEY,
            username      TEXT,
            first_name    TEXT,
            language      TEXT DEFAULT 'en',
            model         TEXT DEFAULT 'gemini-3.5-flash-lite',
            multi_lang    TEXT DEFAULT '',
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS group_settings (
            chat_id       INTEGER PRIMARY KEY,
            language      TEXT DEFAULT 'en',
            updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS translation_logs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER,
            chat_id       INTEGER,
            timestamp     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            message_type  TEXT,
            source_lang   TEXT,
            target_lang   TEXT
        );
        """
    )
    conn.commit()
    conn.close()


# ── User helpers ──────────────────────────────
def upsert_user(user_id: int, username: str | None, first_name: str | None) -> None:
    conn = get_db()
    conn.execute(
        """
        INSERT INTO users (user_id, username, first_name, last_active)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            username    = excluded.username,
            first_name  = excluded.first_name,
            last_active = CURRENT_TIMESTAMP
        """,
        (user_id, username or "", first_name or ""),
    )
    conn.commit()
    conn.close()


def get_user(user_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_user_language(user_id: int, lang: str) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE users SET language = ?, last_active = CURRENT_TIMESTAMP WHERE user_id = ?",
        (lang, user_id),
    )
    conn.commit()
    conn.close()


def set_user_model(user_id: int, model: str) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE users SET model = ?, last_active = CURRENT_TIMESTAMP WHERE user_id = ?",
        (model, user_id),
    )
    conn.commit()
    conn.close()


def set_user_multi_lang(user_id: int, langs: str) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE users SET multi_lang = ?, last_active = CURRENT_TIMESTAMP WHERE user_id = ?",
        (langs, user_id),
    )
    conn.commit()
    conn.close()


# ── Group helpers ─────────────────────────────
def get_group_language(chat_id: int) -> str:
    conn = get_db()
    row = conn.execute(
        "SELECT language FROM group_settings WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    conn.close()
    return row["language"] if row else "en"


def set_group_language(chat_id: int, lang: str) -> None:
    conn = get_db()
    conn.execute(
        """
        INSERT INTO group_settings (chat_id, language, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(chat_id) DO UPDATE SET
            language   = excluded.language,
            updated_at = CURRENT_TIMESTAMP
        """,
        (chat_id, lang),
    )
    conn.commit()
    conn.close()


# ── Logging helpers ───────────────────────────
def log_translation(
    user_id: int,
    chat_id: int,
    message_type: str,
    source_lang: str,
    target_lang: str,
) -> None:
    conn = get_db()
    conn.execute(
        "INSERT INTO translation_logs (user_id, chat_id, message_type, source_lang, target_lang) VALUES (?, ?, ?, ?, ?)",
        (user_id, chat_id, message_type, source_lang, target_lang),
    )
    conn.commit()
    conn.close()


# ── Stats helpers ─────────────────────────────
def get_total_users() -> int:
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    conn.close()
    return count


def get_daily_active_users() -> int:
    conn = get_db()
    today = datetime.date.today().isoformat()
    count = conn.execute(
        "SELECT COUNT(*) as c FROM users WHERE DATE(last_active) = ?", (today,)
    ).fetchone()["c"]
    conn.close()
    return count


def get_language_stats() -> dict[str, int]:
    conn = get_db()
    rows = conn.execute(
        "SELECT target_lang, COUNT(*) as c FROM translation_logs GROUP BY target_lang ORDER BY c DESC"
    ).fetchall()
    conn.close()
    return {r["target_lang"]: r["c"] for r in rows}


def get_total_translations() -> int:
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) as c FROM translation_logs").fetchone()["c"]
    conn.close()
    return count


# ─────────────────────────────────────────────
# GEMINI API HELPERS
# ─────────────────────────────────────────────
def get_gemini_model(model_name: str = "gemini-2.0-flash-lite"):
    """Return a GenerativeModel instance."""
    return genai.GenerativeModel(model_name)


def translate_text(
    text: str,
    target_lang: str,
    model_name: str = "gemini-3.5-flash-lite",
) -> str:
    """Translate *text* into *target_lang* using Gemini."""
    lang_name = LANGUAGES.get(target_lang, target_lang)
    prompt = (
        f"Translate the following text into {lang_name}. "
        f"Only output the translated text, nothing else.\n\n{text}"
    )
    try:
        model = get_gemini_model(model_name)
        resp = model.generate_content(prompt)
        return resp.text.strip() if resp.text else text
    except Exception as exc:
        logger.error("Gemini translate error: %s", exc)
        return f"⚠️ Translation error: {exc}"


def extract_text_from_image(image_bytes: bytes) -> str:
    """Use Gemini Vision to OCR / describe an image."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        model = get_gemini_model("gemini-2.0-flash")
        resp = model.generate_content(
            [
                "Extract all visible text from this image. "
                "If no text is found, describe the image briefly.",
                img,
            ]
        )
        return resp.text.strip() if resp.text else ""
    except Exception as exc:
        logger.error("Gemini image OCR error: %s", exc)
        return ""


def transcribe_audio(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    """Use Gemini to transcribe an audio file."""
    try:
        model = get_gemini_model("gemini-2.0-flash")
        audio_part = {"mime_type": mime_type, "data": audio_bytes}
        resp = model.generate_content(
            ["Transcribe this audio exactly. Return only the transcription.", audio_part]
        )
        return resp.text.strip() if resp.text else ""
    except Exception as exc:
        logger.error("Gemini audio transcription error: %s", exc)
        return ""


def detect_language(text: str) -> str:
    """Ask Gemini to detect the language of a short text."""
    try:
        model = get_gemini_model("gemini-2.0-flash")
        resp = model.generate_content(
            f"Detect the language of this text. Reply with ONLY the ISO 639-1 code (e.g. en, hi, fr). Text:\n{text[:500]}"
        )
        code = resp.text.strip().lower()[:10]
        return code if code in LANGUAGES else "en"
    except Exception:
        return "en"


# ─────────────────────────────────────────────
# INLINE KEYBOARD BUILDERS
# ─────────────────────────────────────────────
def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Build the main menu with optional Admin Panel button."""
    buttons = [
        [
            InlineKeyboardButton("🌐 Change Language", callback_data="menu_language"),
            InlineKeyboardButton("🤖 Choose Model", callback_data="menu_model"),
        ],
        [
            InlineKeyboardButton("📊 My Stats", callback_data="menu_stats"),
            InlineKeyboardButton("🌍 Multi-Language Mode", callback_data="menu_multi"),
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="menu_help"),
        ],
    ]
    if user_id in ADMIN_IDS:
        buttons.append(
            [InlineKeyboardButton("🔒 Admin Panel", callback_data="menu_admin")]
        )
    return InlineKeyboardMarkup(buttons)


def back_button(callback_data: str = "menu_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Back", callback_data=callback_data)]]
    )


def language_keyboard(back_cb: str = "menu_main") -> InlineKeyboardMarkup:
    """Language selection grid."""
    buttons: list[list[InlineKeyboardButton]] = []
    for row in LANG_BUTTON_ROWS:
        btn_row = [
            InlineKeyboardButton(label, callback_data=f"setlang_{code}")
            for code, label in row
        ]
        buttons.append(btn_row)
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data=back_cb)])
    return InlineKeyboardMarkup(buttons)


def model_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"setmodel_{model}")]
        for model, label in MODELS.items()
    ]
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(buttons)


def multi_lang_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Checkbox-style multi-language selector."""
    user = get_user(user_id)
    selected = set((user.get("multi_lang") or "").split(",")) if user else set()
    selected.discard("")

    buttons: list[list[InlineKeyboardButton]] = []
    for row in LANG_BUTTON_ROWS:
        btn_row = []
        for code, label in row:
            prefix = "✅ " if code in selected else ""
            btn_row.append(
                InlineKeyboardButton(
                    f"{prefix}{label}", callback_data=f"multilang_toggle_{code}"
                )
            )
        buttons.append(btn_row)

    buttons.append(
        [
            InlineKeyboardButton("🚀 Translate", callback_data="multilang_go"),
            InlineKeyboardButton("🗑️ Clear All", callback_data="multilang_clear"),
        ]
    )
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(buttons)


# ─────────────────────────────────────────────
# HELPER: should bot respond in this chat?
# ─────────────────────────────────────────────
def should_respond(update: Update) -> bool:
    """Decide whether to process this update.

    In private chats: always respond.
    In groups: respond only if the bot is mentioned or the message is a reply to the bot.
    """
    msg = update.effective_message
    chat = update.effective_chat

    if chat.type == "private":
        return True

    # Group / supergroup
    bot_user = update.get_bot().username if update.get_bot() else None
    text = msg.text or msg.caption or ""

    # Check if bot is @mentioned
    if bot_user and f"@{bot_user}".lower() in text.lower():
        return True

    # Check if message is a reply to the bot
    if msg.reply_to_message and msg.reply_to_message.from_user:
        if msg.reply_to_message.from_user.is_bot:
            return True

    return False


async def is_group_admin(update: Update) -> bool:
    """Check if the user is an admin in the current group."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return False
    if chat.type == "private":
        return user.id in ADMIN_IDS
    try:
        member = await chat.get_member(user.id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


# ─────────────────────────────────────────────
# /start  HANDLER
# ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    upsert_user(user.id, user.username, user.first_name)

    welcome = (
        f"🚜 <b>Tractor AI Translator Bot</b>\n\n"
        f"Hello {user.first_name}! I can translate text, images, audio, "
        f"and stickers into your preferred language using AI.\n\n"
        f"Choose an option below 👇"
    )
    await update.message.reply_text(
        welcome,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(user.id),
    )


# ─────────────────────────────────────────────
# CALLBACK ROUTER
# ─────────────────────────────────────────────
async def callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data
    user = update.effective_user
    if not user:
        return

    upsert_user(user.id, user.username, user.first_name)
    await query.answer()

    # ── Admin guard for admin callbacks ──
    if data.startswith("menu_admin") or data.startswith("admin_"):
        if user.id not in ADMIN_IDS:
            await query.edit_message_text("⛔ You are not authorized.")
            return

    # ── Main menu ──
    if data == "menu_main":
        await query.edit_message_text(
            "🚜 <b>Tractor AI Translator Bot</b>\n\nChoose an option 👇",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(user.id),
        )
        return

    # ── Language menu ──
    if data == "menu_language":
        user_data = get_user(user.id)
        current = user_data["language"] if user_data else "en"
        lang_name = LANGUAGES.get(current, current)
        await query.edit_message_text(
            f"🌐 <b>Select Translation Language</b>\n\nCurrent: <b>{lang_name}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=language_keyboard(),
        )
        return

    # ── Set language ──
    if data.startswith("setlang_"):
        lang_code = data.replace("setlang_", "")
        if lang_code not in LANGUAGES:
            await query.edit_message_text("❌ Unknown language.")
            return
        set_user_language(user.id, lang_code)
        lang_name = LANGUAGES[lang_code]
        await query.edit_message_text(
            f"✅ Language set to <b>{lang_name}</b>.\n\n"
            f"Send me any text, image, audio, or sticker and I'll translate it!",
            parse_mode=ParseMode.HTML,
            reply_markup=back_button(),
        )
        return

    # ── Model menu ──
    if data == "menu_model":
        user_data = get_user(user.id)
        current = user_data["model"] if user_data else "gemini-2.0-flash"
        current_label = MODELS.get(current, current)
        await query.edit_message_text(
            f"🤖 <b>Choose Translation Model</b>\n\nCurrent: <b>{current_label}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=model_keyboard(),
        )
        return

    # ── Set model ──
    if data.startswith("setmodel_"):
        model_name = data.replace("setmodel_", "")
        if model_name not in MODELS:
            await query.edit_message_text("❌ Unknown model.")
            return
        set_user_model(user.id, model_name)
        model_label = MODELS[model_name]
        await query.edit_message_text(
            f"✅ Model set to <b>{model_label}</b>.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_button(),
        )
        return

    # ── My Stats ──
    if data == "menu_stats":
        user_data = get_user(user.id)
        lang = LANGUAGES.get(user_data["language"], "N/A") if user_data else "N/A"
        model = MODELS.get(user_data["model"], "N/A") if user_data else "N/A"
        multi = user_data.get("multi_lang", "") if user_data else ""
        multi_count = len([x for x in multi.split(",") if x]) if multi else 0

        # Count this user's translations
        conn = get_db()
        total = conn.execute(
            "SELECT COUNT(*) as c FROM translation_logs WHERE user_id = ?", (user.id,)
        ).fetchone()["c"]
        conn.close()

        text = (
            f"📊 <b>Your Stats</b>\n\n"
            f"🌐 Language: <b>{lang}</b>\n"
            f"🤖 Model: <b>{model}</b>\n"
            f"🌍 Multi-lang targets: <b>{multi_count}</b>\n"
            f"📝 Your translations: <b>{total}</b>\n"
        )
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=back_button()
        )
        return

    # ── Multi-Language Mode ──
    if data == "menu_multi":
        user_data = get_user(user.id)
        multi = user_data.get("multi_lang", "") if user_data else ""
        count = len([x for x in multi.split(",") if x]) if multi else 0
        await query.edit_message_text(
            f"🌍 <b>Multi-Language Mode</b>\n\n"
            f"Select multiple target languages. Your message will be translated "
            f"into all selected languages at once.\n\n"
            f"Currently selected: <b>{count}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=multi_lang_keyboard(user.id),
        )
        return

    # ── Multi-lang toggle ──
    if data.startswith("multilang_toggle_"):
        lang_code = data.replace("multilang_toggle_", "")
        if lang_code not in LANGUAGES:
            return

        user_data = get_user(user.id)
        current = set((user_data.get("multi_lang") or "").split(",")) if user_data else set()
        current.discard("")

        if lang_code in current:
            current.discard(lang_code)
        else:
            current.add(lang_code)

        new_val = ",".join(sorted(current))
        set_user_multi_lang(user.id, new_val)

        # Refresh the keyboard
        user_data = get_user(user.id)
        count = len([x for x in (user_data.get("multi_lang") or "").split(",") if x]) if user_data else 0
        await query.edit_message_text(
            f"🌍 <b>Multi-Language Mode</b>\n\n"
            f"Select multiple target languages. Your message will be translated "
            f"into all selected languages at once.\n\n"
            f"Currently selected: <b>{count}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=multi_lang_keyboard(user.id),
        )
        return

    # ── Multi-lang clear ──
    if data == "multilang_clear":
        set_user_multi_lang(user.id, "")
        await query.edit_message_text(
            "🌍 <b>Multi-Language Mode</b>\n\nAll languages cleared.",
            parse_mode=ParseMode.HTML,
            reply_markup=multi_lang_keyboard(user.id),
        )
        return

    # ── Multi-lang go (enable/disable) ──
    if data == "multilang_go":
        user_data = get_user(user.id)
        multi = user_data.get("multi_lang", "") if user_data else ""
        langs = [x for x in multi.split(",") if x]
        if not langs:
            await query.edit_message_text(
                "⚠️ No languages selected. Pick at least one.",
                reply_markup=multi_lang_keyboard(user.id),
            )
            return
        lang_names = ", ".join(LANGUAGES.get(l, l) for l in langs)
        await query.edit_message_text(
            f"✅ Multi-Language Mode <b>enabled</b>!\n\n"
            f"Target languages: <b>{lang_names}</b>\n\n"
            f"Send me any message and I'll translate it into all of them.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_button(),
        )
        return

    # ── Help ──
    if data == "menu_help":
        help_text = (
            "❓ <b>Help — Tractor AI Translator Bot</b>\n\n"
            "🚀 <b>Getting Started</b>\n"
            "• Press /start to open the main menu\n"
            "• Select your preferred language\n"
            "• Send me text, images, audio, or stickers\n\n"
            "🌐 <b>Change Language</b>\n"
            "Pick from 20+ languages. Your selection is saved.\n\n"
            "🤖 <b>Choose Model</b>\n"
            "Select which Gemini model to use for translation.\n\n"
            "🌍 <b>Multi-Language Mode</b>\n"
            "Select multiple languages and translate into all at once.\n\n"
            "📊 <b>My Stats</b>\n"
            "View your preferences and translation count.\n\n"
            "💬 <b>Group Mode</b>\n"
            "In groups, mention me (@botname) or reply to my messages. "
            "Group admins can set a default language via /settings.\n\n"
            "🖼️ <b>Image Translation</b>\n"
            "Send a photo with text — I'll extract it via OCR and translate.\n\n"
            "🎙️ <b>Audio Translation</b>\n"
            "Send a voice message — I'll transcribe and translate it.\n\n"
            "📌 <b>Sticker Translation</b>\n"
            "Send a sticker with a caption — I'll translate the caption."
        )
        await query.edit_message_text(
            help_text, parse_mode=ParseMode.HTML, reply_markup=back_button()
        )
        return

    # ── Admin Panel ──
    if data == "menu_admin":
        total_users = get_total_users()
        dau = get_daily_active_users()
        total_trans = get_total_translations()
        lang_stats = get_language_stats()

        stats_lines = "\n".join(
            f"  • {LANGUAGES.get(l, l)}: {c}" for l, c in lang_stats.items()
        ) or "  (none yet)"

        text = (
            f"🔒 <b>Admin Panel</b>\n\n"
            f"👥 Total Users: <b>{total_users}</b>\n"
            f"📈 Daily Active Users: <b>{dau}</b>\n"
            f"📝 Total Translations: <b>{total_trans}</b>\n\n"
            f"🌐 <b>Language Usage</b>\n{stats_lines}"
        )
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=back_button("menu_main"),
        )
        return

    # ── Group settings ──
    if data == "group_settings":
        if not await is_group_admin(update):
            await query.edit_message_text("⛔ Only group admins can change settings.")
            return
        chat_id = update.effective_chat.id
        current = get_group_language(chat_id)
        lang_name = LANGUAGES.get(current, current)
        await query.edit_message_text(
            f"⚙️ <b>Group Settings</b>\n\nDefault language: <b>{lang_name}</b>\n\nSelect a new default language:",
            parse_mode=ParseMode.HTML,
            reply_markup=language_keyboard("menu_main"),
        )
        return

    if data.startswith("setlang_") and update.effective_chat.type != "private":
        # Group admin setting language
        if await is_group_admin(update):
            lang_code = data.replace("setlang_", "")
            if lang_code in LANGUAGES:
                set_group_language(update.effective_chat.id, lang_code)
                await query.edit_message_text(
                    f"✅ Group default language set to <b>{LANGUAGES[lang_code]}</b>.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_button("menu_main"),
                )
                return


# ─────────────────────────────────────────────
# /settings  (group admin command)
# ─────────────────────────────────────────────
async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat or chat.type == "private":
        await update.message.reply_text("This command is for group chats only.")
        return

    if not await is_group_admin(update):
        await update.message.reply_text("⛔ Only group admins can use this.")
        return

    current = get_group_language(chat.id)
    lang_name = LANGUAGES.get(current, current)

    buttons = [
        [
            InlineKeyboardButton(
                "⚙️ Set Group Language", callback_data="group_settings"
            )
        ]
    ]
    await update.message.reply_text(
        f"⚙️ <b>Group Settings</b>\n\nDefault language: <b>{lang_name}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ─────────────────────────────────────────────
# MESSAGE HANDLERS  (text, photo, voice, sticker)
# ─────────────────────────────────────────────
async def handle_text_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat:
        return

    text = msg.text
    if not text or not text.strip():
        return

    # Group filter
    if not should_respond(update):
        return

    upsert_user(user.id, user.username, user.first_name)

    # Determine target language
    user_data = get_user(user.id)
    multi_lang = user_data.get("multi_lang", "") if user_data else ""

    if chat.type != "private" and not multi_lang:
        target_lang = get_group_language(chat.id)
    else:
        target_lang = user_data["language"] if user_data else "en"

    model_name = user_data["model"] if user_data else "gemini-3.5-flash-lite"

    # Multi-language mode
    if multi_lang:
        langs = [l for l in multi_lang.split(",") if l]
        await msg.reply_chat_action("typing")

        results = []
        for lang in langs:
            translated = translate_text(text, lang, model_name)
            lang_name = LANGUAGES.get(lang, lang)
            results.append(f"**{lang_name}:**\n{translated}")
            log_translation(user.id, chat.id, "text", "auto", lang)

        header = f"🌍 <b>Multi-Language Translation</b>\n\n"
        full = header + "\n\n---\n\n".join(results)
        # Telegram message limit ~4096
        if len(full) > 4000:
            for chunk in results:
                await msg.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
        else:
            await msg.reply_text(full, parse_mode=ParseMode.HTML)
        return

    # Single language
    await msg.reply_chat_action("typing")
    translated = translate_text(text, target_lang, model_name)
    lang_name = LANGUAGES.get(target_lang, target_lang)
    log_translation(user.id, chat.id, "text", "auto", target_lang)

    await msg.reply_text(
        f"🌐 <b>{lang_name}:</b>\n\n{translated}",
        parse_mode=ParseMode.HTML,
    )


async def handle_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat:
        return

    if not should_respond(update):
        return

    upsert_user(user.id, user.username, user.first_name)
    await msg.reply_chat_action("typing")

    user_data = get_user(user.id)
    multi_lang = user_data.get("multi_lang", "") if user_data else ""

    if chat.type != "private" and not multi_lang:
        target_lang = get_group_language(chat.id)
    else:
        target_lang = user_data["language"] if user_data else "en"

    model_name = user_data["model"] if user_data else "gemini-3.5-flash-lite"

    # Extract text from image
    photo = msg.photo[-1] if msg.photo else None
    if not photo:
        await msg.reply_text("❌ Could not read the image.")
        return

    file = await photo.get_file()
    img_bytes = await file.download_as_bytearray()

    await msg.reply_text("🔍 Extracting text from image...")
    extracted = extract_text_from_image(bytes(img_bytes))

    if not extracted:
        await msg.reply_text("ℹ️ No text found in the image.")
        return

    # Also check caption for extra context
    caption = msg.caption or ""

    if multi_lang:
        langs = [l for l in multi_lang.split(",") if l]
        results = []
        for lang in langs:
            translated = translate_text(extracted, lang, model_name)
            lang_name = LANGUAGES.get(lang, lang)
            results.append(f"**{lang_name}:**\n{translated}")
            log_translation(user.id, chat.id, "image", "auto", lang)

        header = f"🖼️ <b>Image Text (OCR)</b>\n\n<pre>{extracted[:300]}</pre>\n\n"
        full = header + "\n\n---\n\n".join(results)
        if len(full) > 4000:
            await msg.reply_text(
                f"🖼️ <b>OCR Result:</b>\n<pre>{extracted[:500]}</pre>",
                parse_mode=ParseMode.HTML,
            )
            for chunk in results:
                await msg.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
        else:
            await msg.reply_text(full, parse_mode=ParseMode.HTML)
        return

    translated = translate_text(extracted, target_lang, model_name)
    lang_name = LANGUAGES.get(target_lang, target_lang)
    log_translation(user.id, chat.id, "image", "auto", target_lang)

    await msg.reply_text(
        f"🖼️ <b>OCR Result:</b>\n<pre>{extracted[:500]}</pre>\n\n"
        f"🌐 <b>{lang_name}:</b>\n\n{translated}",
        parse_mode=ParseMode.HTML,
    )


async def handle_voice(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat:
        return

    if not should_respond(update):
        return

    upsert_user(user.id, user.username, user.first_name)
    await msg.reply_chat_action("typing")

    user_data = get_user(user.id)
    multi_lang = user_data.get("multi_lang", "") if user_data else ""

    if chat.type != "private" and not multi_lang:
        target_lang = get_group_language(chat.id)
    else:
        target_lang = user_data["language"] if user_data else "en"

    model_name = user_data["model"] if user_data else "gemini-3.5-flash-lite"

    voice = msg.voice or msg.audio
    if not voice:
        await msg.reply_text("❌ No audio found.")
        return

    file = await voice.get_file()
    audio_bytes = await file.download_as_bytearray()
    mime = voice.mime_type or "audio/ogg"

    await msg.reply_text("🎙️ Transcribing audio...")
    transcription = transcribe_audio(bytes(audio_bytes), mime)

    if not transcription:
        await msg.reply_text("ℹ️ Could not transcribe the audio.")
        return

    if multi_lang:
        langs = [l for l in multi_lang.split(",") if l]
        results = []
        for lang in langs:
            translated = translate_text(transcription, lang, model_name)
            lang_name = LANGUAGES.get(lang, lang)
            results.append(f"**{lang_name}:**\n{translated}")
            log_translation(user.id, chat.id, "audio", "auto", lang)

        header = f"🎙️ <b>Transcription:</b>\n<pre>{transcription[:300]}</pre>\n\n"
        full = header + "\n\n---\n\n".join(results)
        if len(full) > 4000:
            await msg.reply_text(
                f"🎙️ <b>Transcription:</b>\n<pre>{transcription[:500]}</pre>",
                parse_mode=ParseMode.HTML,
            )
            for chunk in results:
                await msg.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
        else:
            await msg.reply_text(full, parse_mode=ParseMode.HTML)
        return

    translated = translate_text(transcription, target_lang, model_name)
    lang_name = LANGUAGES.get(target_lang, target_lang)
    log_translation(user.id, chat.id, "audio", "auto", target_lang)

    await msg.reply_text(
        f"🎙️ <b>Transcription:</b>\n<pre>{transcription[:500]}</pre>\n\n"
        f"🌐 <b>{lang_name}:</b>\n\n{translated}",
        parse_mode=ParseMode.HTML,
    )


async def handle_sticker(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat:
        return

    if not should_respond(update):
        return

    caption = msg.caption
    if not caption:
        await msg.reply_text(
            "📌 Send a sticker with a <b>caption</b> and I'll translate it!",
            parse_mode=ParseMode.HTML,
        )
        return

    upsert_user(user.id, user.username, user.first_name)
    await msg.reply_chat_action("typing")

    user_data = get_user(user.id)
    multi_lang = user_data.get("multi_lang", "") if user_data else ""

    if chat.type != "private" and not multi_lang:
        target_lang = get_group_language(chat.id)
    else:
        target_lang = user_data["language"] if user_data else "en"

    model_name = user_data["model"] if user_data else "gemini-3.5-flash-lite"

    if multi_lang:
        langs = [l for l in multi_lang.split(",") if l]
        results = []
        for lang in langs:
            translated = translate_text(caption, lang, model_name)
            lang_name = LANGUAGES.get(lang, lang)
            results.append(f"**{lang_name}:**\n{translated}")
            log_translation(user.id, chat.id, "sticker", "auto", lang)

        header = f"📌 <b>Sticker Caption:</b>\n{caption}\n\n"
        full = header + "\n\n---\n\n".join(results)
        if len(full) > 4000:
            for chunk in results:
                await msg.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
        else:
            await msg.reply_text(full, parse_mode=ParseMode.HTML)
        return

    translated = translate_text(caption, target_lang, model_name)
    lang_name = LANGUAGES.get(target_lang, target_lang)
    log_translation(user.id, chat.id, "sticker", "auto", target_lang)

    await msg.reply_text(
        f"📌 <b>Sticker Caption:</b>\n{caption}\n\n"
        f"🌐 <b>{lang_name}:</b>\n\n{translated}",
        parse_mode=ParseMode.HTML,
    )


async def handle_document(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle image documents (e.g. .jpg sent as document)."""
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat:
        return

    if not should_respond(update):
        return

    doc = msg.document
    if not doc or not doc.mime_type:
        return

    if not doc.mime_type.startswith("image/"):
        return

    # Reuse photo logic by downloading and OCR-ing
    upsert_user(user.id, user.username, user.first_name)
    await msg.reply_chat_action("typing")

    user_data = get_user(user.id)
    target_lang = user_data["language"] if user_data else "en"
    model_name = user_data["model"] if user_data else "gemini-3.5-flash-lite"

    file = await doc.get_file()
    img_bytes = await file.download_as_bytearray()

    await msg.reply_text("🔍 Extracting text from image...")
    extracted = extract_text_from_image(bytes(img_bytes))

    if not extracted:
        await msg.reply_text("ℹ️ No text found in the image.")
        return

    translated = translate_text(extracted, target_lang, model_name)
    lang_name = LANGUAGES.get(target_lang, target_lang)
    log_translation(user.id, chat.id, "image", "auto", target_lang)

    await msg.reply_text(
        f"🖼️ <b>OCR Result:</b>\n<pre>{extracted[:500]}</pre>\n\n"
        f"🌐 <b>{lang_name}:</b>\n\n{translated}",
        parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────────
# ERROR HANDLER
# ─────────────────────────────────────────────
async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    logger.error("Exception while handling update: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ An unexpected error occurred. Please try again."
            )
        except Exception:
            pass


# ─────────────────────────────────────────────
# BOT STARTUP
# ─────────────────────────────────────────────
def main() -> None:
    """Initialize the bot and start polling."""
    init_db()
    logger.info("Database initialized.")

    app = Application.builder().token(BOT_TOKEN).build()

    # ── Command handlers ──
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("settings", cmd_settings))

    # ── Callback query handler ──
    app.add_handler(CallbackQueryHandler(callback_handler))

    # ── Message handlers (order matters: more specific first) ──
    # Voice / Audio
    app.add_handler(
        MessageHandler(filters.VOICE | filters.AUDIO, handle_voice)
    )
    # Photos
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    # Sticker with caption
    app.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker))
    # Document images
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))
    # Plain text (must be last)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message)
    )

    # ── Error handler ──
    app.add_error_handler(error_handler)

    # ── Set bot commands ──
    async def post_init(application: Application) -> None:
        await application.bot.set_my_commands(
            [
                BotCommand("start", "Open main menu"),
                BotCommand("settings", "Group settings (admins)"),
            ]
        )

    logger.info("🚜 Tractor AI Translator Bot is starting...")
    app.run_polling(drop_pending_updates=True, post_init=post_init)


if __name__ == "__main__":
    main()
