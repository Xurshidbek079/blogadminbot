#!/usr/bin/env python3
import os
import re
import subprocess
from pathlib import Path
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, CallbackQueryHandler,
    filters, ContextTypes,
)

BOT_TOKEN  = os.environ["BOT_TOKEN"]
ADMIN_ID   = int(os.environ["ADMIN_ID"])
BLOG_DIR   = Path("/root/blog")
POSTS_DIR  = BLOG_DIR / "content/posts"
DRAFTS_DIR = BLOG_DIR / "content/drafts"

TITLE, TAGS, CONTENT, CONFIRM = range(4)


def is_admin(update: Update) -> bool:
    return update.effective_user.id == ADMIN_ID


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def shell(cmd: str) -> tuple[int, str]:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text(
        "Blog admin\n\n"
        "/new — write a new post\n"
        "/drafts — list & publish drafts\n"
        "/posts — recent published posts\n"
        "/restart — restart blog service\n"
        "/status — service status"
    )


# ── /new conversation ─────────────────────────────────────────────────────────

async def new_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return ConversationHandler.END
    await update.message.reply_text("Title:")
    return TITLE


async def got_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["title"] = update.message.text.strip()
    await update.message.reply_text("Tags (comma-separated) or /skip:")
    return TAGS


async def got_tags(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    ctx.user_data["tags"] = [slugify(t) for t in raw.split(",") if t.strip()]
    await update.message.reply_text("Post content (Markdown):")
    return CONTENT


async def skip_tags(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["tags"] = []
    await update.message.reply_text("Post content (Markdown):")
    return CONTENT


async def got_content(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["content"] = update.message.text
    title = ctx.user_data["title"]
    tags  = ctx.user_data["tags"]
    preview = f"*{title}*\nTags: {', '.join(tags) or 'none'}"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Publish now",   callback_data="new:publish"),
        InlineKeyboardButton("Save as draft", callback_data="new:draft"),
        InlineKeyboardButton("Cancel",        callback_data="new:cancel"),
    ]])
    await update.message.reply_text(preview, parse_mode="Markdown", reply_markup=kb)
    return CONFIRM


async def new_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action = q.data.split(":")[1]

    if action == "cancel":
        await q.edit_message_text("Cancelled.")
        return ConversationHandler.END

    title   = ctx.user_data["title"]
    tags    = ctx.user_data["tags"]
    content = ctx.user_data["content"]
    slug    = slugify(title)
    date    = datetime.now().strftime("%Y-%m-%d")
    fname   = f"{date}-{slug}.md"
    tags_yaml = "[" + ", ".join(tags) + "]" if tags else "[]"
    body = (
        f"---\ntitle: {title}\ndate: {date}\nslug: {slug}\n"
        f"published: true\ntags: {tags_yaml}\n---\n\n{content}"
    )

    if action == "publish":
        POSTS_DIR.mkdir(parents=True, exist_ok=True)
        (POSTS_DIR / fname).write_text(body, encoding="utf-8")
        shell("systemctl restart blog")
        await q.edit_message_text(f"Published ✓\n/essays/{slug}")
    else:
        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        (DRAFTS_DIR / fname).write_text(body, encoding="utf-8")
        await q.edit_message_text(f"Draft saved: {fname}")

    return ConversationHandler.END


async def new_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ── /drafts ───────────────────────────────────────────────────────────────────

async def cmd_drafts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(DRAFTS_DIR.glob("*.md"), reverse=True)
    if not files:
        await update.message.reply_text("No drafts.")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f.name, callback_data=f"draft:{f.name}")]
        for f in files
    ])
    await update.message.reply_text("Drafts:", reply_markup=kb)


async def draft_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(update):
        return
    fname = q.data.split(":", 1)[1]
    path  = DRAFTS_DIR / fname
    if not path.exists():
        await q.edit_message_text("Draft not found.")
        return
    ctx.user_data["draft"] = fname
    preview = path.read_text(encoding="utf-8")[:600]
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Publish", callback_data="dpub:yes"),
        InlineKeyboardButton("Cancel",  callback_data="dpub:no"),
    ]])
    await q.edit_message_text(
        f"`{fname}`\n\n{preview}", parse_mode="Markdown", reply_markup=kb
    )


async def draft_publish(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(update):
        return
    if q.data == "dpub:no":
        await q.edit_message_text("Cancelled.")
        return
    fname = ctx.user_data.get("draft")
    if not fname:
        await q.edit_message_text("No draft selected.")
        return
    src = DRAFTS_DIR / fname
    dst = POSTS_DIR / fname
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    shell("systemctl restart blog")
    await q.edit_message_text(f"Published ✓\n{fname}")


# ── /posts ────────────────────────────────────────────────────────────────────

async def cmd_posts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    files = sorted(POSTS_DIR.glob("*.md"), reverse=True)[:10]
    if not files:
        await update.message.reply_text("No posts yet.")
        return
    lines = "\n".join(f.stem for f in files)
    await update.message.reply_text(f"Recent posts:\n{lines}")


# ── /restart ──────────────────────────────────────────────────────────────────

async def cmd_restart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    code, out = shell("systemctl restart blog")
    await update.message.reply_text("Restarted ✓" if code == 0 else f"Error:\n{out}")


# ── /status ───────────────────────────────────────────────────────────────────

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    _, out = shell("systemctl status blog --no-pager -l --lines=8")
    await update.message.reply_text(f"```\n{out}\n```", parse_mode="Markdown")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    new_conv = ConversationHandler(
        entry_points=[CommandHandler("new", new_start)],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_title)],
            TAGS: [
                CommandHandler("skip", skip_tags),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_tags),
            ],
            CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_content)],
            CONFIRM: [CallbackQueryHandler(new_confirm, pattern=r"^new:")],
        },
        fallbacks=[CommandHandler("cancel", new_cancel)],
    )

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(new_conv)
    app.add_handler(CommandHandler("drafts",  cmd_drafts))
    app.add_handler(CallbackQueryHandler(draft_pick,    pattern=r"^draft:"))
    app.add_handler(CallbackQueryHandler(draft_publish, pattern=r"^dpub:"))
    app.add_handler(CommandHandler("posts",   cmd_posts))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(CommandHandler("status",  cmd_status))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
