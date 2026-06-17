import json
import logging
import os
import time
import asyncio
import aiohttp
import requests
from datetime import datetime
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
    JobQueue,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
DATA_FILE = BASE_DIR / "data.json"

WAITING_USERNAME = 1
WAITING_REASON = 2
WAITING_WEBHOOK = 3

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def load_data():
    if not DATA_FILE.exists():
        data = {"queue": [], "unbanned": []}
        save_data(data)
        return data
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    elif seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m"
    else:
        d = seconds // 86400
        h = (seconds % 86400) // 3600
        return f"{d}d {h}h"

def check_instagram_account(username: str):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
        resp = requests.get(
            url,
            headers={
                **headers,
                "x-ig-app-id": "936619743392459",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            user_data = data.get("data", {}).get("user")
            if user_data:
                followers = user_data.get("edge_followed_by", {}).get("count", 0)
                return True, followers
        return False, 0
    except Exception as e:
        logger.error(f"Error checking {username}: {e}")
        return False, 0

async def send_discord_webhook(webhook_url: str, username: str, followers: int, reason: str, time_taken: str):
    if not webhook_url:
        return
    payload = {
        "embeds": [
            {
                "title": "✅ Username Unbanned Successfully!",
                "color": 0x00FF88,
                "fields": [
                    {"name": "Target", "value": f"@{username}", "inline": True},
                    {"name": "Followers", "value": str(followers), "inline": True},
                    {
                        "name": "Profile Link",
                        "value": f"[instagram.com/{username}](https://www.instagram.com/{username}/)",
                        "inline": False,
                    },
                    {"name": "Reason", "value": reason, "inline": True},
                    {"name": "Time Taken", "value": time_taken, "inline": True},
                ],
                "footer": {
                    "text": "IG: @w14b | TG: @plability"
                },
                "timestamp": datetime.utcnow().isoformat(),
            }
        ]
    }
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(webhook_url, json=payload)
    except Exception as e:
        logger.error(f"Discord webhook error: {e}")

def main_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add User", callback_data="add_user")],
            [
                InlineKeyboardButton("📋 Queue List", callback_data="queue_list"),
                InlineKeyboardButton("✅ Unbanned List", callback_data="unbanned_list"),
            ],
            [InlineKeyboardButton("🔗 Discord Webhook", callback_data="set_webhook")],
        ]
    )

CREDITS_TEXT = "\n\n─────────────────\n📸 IG: @w14b | ✈️ TG: @plability"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    if str(update.effective_user.id) != str(cfg.get("telegram_chat_id", "")):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    text = (
        "👁 *Instagram Unban Monitor*\n\n"
        "Monitor banned Instagram accounts and get notified the moment they're unbanned.\n\n"
        "Choose an option below:"
        + CREDITS_TEXT
    )
    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=main_menu_keyboard()
    )

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "👁 *Instagram Unban Monitor*\n\n"
        "Monitor banned Instagram accounts and get notified the moment they're unbanned.\n\n"
        "Choose an option below:"
        + CREDITS_TEXT
    )
    await query.edit_message_text(
        text, parse_mode="Markdown", reply_markup=main_menu_keyboard()
    )

async def queue_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = load_data()
    queue = data.get("queue", [])
    if not queue:
        text = "📋 *Queue List*\n\nNo accounts currently being monitored."
    else:
        lines = ["📋 *Queue List*\n"]
        for i, entry in enumerate(queue, 1):
            added_dt = datetime.fromisoformat(entry["added_at"])
            elapsed = format_duration((datetime.now() - added_dt).total_seconds())
            lines.append(
                f"`{i}.` @{entry['username']}\n"
                f"   📌 Reason: {entry['reason']}\n"
                f"   ⏱ Monitoring for: {elapsed}"
            )
        text = "\n\n".join(lines)
    text += CREDITS_TEXT
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]
        ),
    )

async def unbanned_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = load_data()
    unbanned = data.get("unbanned", [])
    if not unbanned:
        text = "✅ *Unbanned List*\n\nNo accounts have been unbanned yet."
    else:
        lines = ["✅ *Unbanned List*\n"]
        for i, entry in enumerate(unbanned, 1):
            lines.append(
                f"`{i}.` [@{entry['username']}](https://www.instagram.com/{entry['username']}/)\n"
                f"   👥 Followers: {entry.get('followers', '?')}\n"
                f"   📌 Reason: {entry['reason']}\n"
                f"   ⏱ Time Taken: {entry.get('time_taken', '?')}"
            )
        text = "\n\n".join(lines)
    text += CREDITS_TEXT
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]
        ),
        disable_web_page_preview=True,
    )

async def set_webhook_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cfg = load_config()
    current = cfg.get("discord_webhook", "")
    status = f"`{current}`" if current else "_Not set_"
    await query.edit_message_text(
        f"🔗 *Discord Webhook*\n\nCurrent webhook:\n{status}\n\nSend me your new Discord webhook URL now, or type /cancel to go back." + CREDITS_TEXT,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]
        ),
    )
    return WAITING_WEBHOOK

async def receive_webhook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    if str(update.effective_user.id) != str(cfg.get("telegram_chat_id", "")):
        return ConversationHandler.END
    url = update.message.text.strip()
    if url.startswith("https://discord.com/api/webhooks/") or url.startswith("https://discordapp.com/api/webhooks/"):
        cfg["discord_webhook"] = url
        save_config(cfg)
        await update.message.reply_text(
            "✅ Discord webhook saved successfully!" + CREDITS_TEXT,
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await update.message.reply_text(
            "❌ Invalid webhook URL. Must start with `https://discord.com/api/webhooks/`. Try again or /cancel.",
            parse_mode="Markdown",
        )
        return WAITING_WEBHOOK
    return ConversationHandler.END

async def add_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "➕ *Add User to Monitor*\n\nPlease enter the target username:\n_(eg: w14b)_" + CREDITS_TEXT,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Cancel", callback_data="back_menu")]]
        ),
    )
    return WAITING_USERNAME

async def receive_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    if str(update.effective_user.id) != str(cfg.get("telegram_chat_id", "")):
        return ConversationHandler.END
    username = update.message.text.strip().lstrip("@")
    context.user_data["pending_username"] = username
    await update.message.reply_text(
        f"Please provide me the ban reason for @{username}:\n_(eg. fake account)_" + CREDITS_TEXT,
        parse_mode="Markdown",
    )
    return WAITING_REASON

async def receive_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    if str(update.effective_user.id) != str(cfg.get("telegram_chat_id", "")):
        return ConversationHandler.END
    reason = update.message.text.strip()
    username = context.user_data.get("pending_username", "")
    data = load_data()
    for entry in data["queue"]:
        if entry["username"].lower() == username.lower():
            await update.message.reply_text(
                f"⚠️ @{username} is already in the queue!",
                reply_markup=main_menu_keyboard(),
            )
            return ConversationHandler.END
    data["queue"].append(
        {
            "username": username,
            "reason": reason,
            "added_at": datetime.now().isoformat(),
            "chat_id": str(update.effective_user.id),
        }
    )
    save_data(data)
    await update.message.reply_text(
        f"✅ *@{username}* added to the monitoring queue!\n\n"
        f"📌 Reason: {reason}\n"
        f"🔍 I'll notify you the moment the account is unbanned." + CREDITS_TEXT,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ Cancelled." + CREDITS_TEXT,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END

async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    data = load_data()
    queue = data.get("queue", [])
    if not queue:
        return
    newly_unbanned = []
    remaining = []
    for entry in queue:
        username = entry["username"]
        is_live, followers = check_instagram_account(username)
        if is_live:
            added_dt = datetime.fromisoformat(entry["added_at"])
            elapsed = (datetime.now() - added_dt).total_seconds()
            time_taken = format_duration(elapsed)
            unbanned_entry = {
                "username": username,
                "reason": entry["reason"],
                "added_at": entry["added_at"],
                "unbanned_at": datetime.now().isoformat(),
                "followers": followers,
                "time_taken": time_taken,
            }
            data["unbanned"].append(unbanned_entry)
            newly_unbanned.append(unbanned_entry)
            logger.info(f"@{username} is UNBANNED! Followers: {followers}")
        else:
            remaining.append(entry)
    if newly_unbanned:
        data["queue"] = remaining
        save_data(data)
        for entry in newly_unbanned:
            username = entry["username"]
            followers = entry["followers"]
            reason = entry["reason"]
            time_taken = entry["time_taken"]
            tg_text = (
                "Username Unbanned Successfully\\!\n\n"
                f"Target: [@{username}](https://www.instagram.com/{username}/)\n"
                f"Followers: {followers}\n"
                f"Profile Link: [instagram\\.com/{username}](https://www.instagram.com/{username}/)\n"
                f"Reason: {reason}\n"
                f"Time Taken: {time_taken}"
                + "\n\n─────────────────\n📸 IG: @w14b \\| ✈️ TG: @plability"
            )
            try:
                await context.bot.send_message(
                    chat_id=cfg["telegram_chat_id"],
                    text=tg_text,
                    parse_mode="MarkdownV2",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.error(f"Telegram send error: {e}")
            discord_webhook = cfg.get("discord_webhook", "")
            if discord_webhook:
                await send_discord_webhook(
                    discord_webhook, username, followers, reason, time_taken
                )

def main():
    cfg = load_config()
    token = cfg.get("telegram_token", "")
    if not token:
        raise ValueError("No telegram_token found in config.json")

    app = Application.builder().token(token).build()

    webhook_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(set_webhook_prompt, pattern="^set_webhook$")],
        states={
            WAITING_WEBHOOK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_webhook)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True,
        per_user=True,
        per_message=False,
    )

    add_user_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_user_start, pattern="^add_user$")],
        states={
            WAITING_USERNAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_username)
            ],
            WAITING_REASON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reason)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True,
        per_user=True,
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(add_user_conv)
    app.add_handler(webhook_conv)
    app.add_handler(CallbackQueryHandler(queue_list, pattern="^queue_list$"))
    app.add_handler(CallbackQueryHandler(unbanned_list, pattern="^unbanned_list$"))
    app.add_handler(CallbackQueryHandler(menu, pattern="^back_menu$"))

    interval = cfg.get("check_interval_seconds", 60)
    app.job_queue.run_repeating(monitor_job, interval=interval, first=10)

    logger.info("Bot started. Monitoring every %d seconds.", interval)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
