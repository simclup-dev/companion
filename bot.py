"""Entry point: PTB app, хендлери, роутинг, callback-кнопки, JobQueue."""

import asyncio
import logging
from datetime import time, timedelta, timezone, datetime as dt
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import brain
import config
import memory
import prompts

logger = logging.getLogger(__name__)

# Омні-клієнт: Gemini Flash Lite (українське аудіо MiMo не тягне). MiMo-клієнт лишається в brain.py для відкату.
mimo_client = brain.GeminiClient(config.GOOGLE_API_KEY, config.GEMINI_MODEL)

MAX_PENDING = 50

# Дефолтні shelf_type для випадків, коли модель не заповнила extracted.shelf_type
_DEFAULT_SHELF_TYPE = {
    "task": "task",
    "idea": "idea",
    "thought": "thought",
    "filter": "idea",
    "conversation_prep": "thought",
    "brain": "idea",
}

_TYPE_RU_LABEL = {"task": "завдання", "idea": "ідею", "thought": "думку"}


def setup_logging() -> None:
    log_path = Path(__file__).parent / "companion.log"
    handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # don't log bot token in URLs
    root.addHandler(handler)
    root.addHandler(logging.StreamHandler())


def _kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows]
    )


def buttons_for(intent: str, mid: int) -> InlineKeyboardMarkup | None:
    p = f"pend:{mid}"
    if intent == "task":
        return _kb([
            [("На полицю ✓", f"{p}:save"), ("Це ідея", f"{p}:as_idea")],
            [("Розібрати 🧠", f"{p}:brain"), ("Відкинути", f"{p}:drop")],
        ])
    if intent in ("thought", "idea"):
        return _kb([
            [("На полицю ✓", f"{p}:save"), ("Це завдання", f"{p}:as_task")],
            [("Розібрати 🧠", f"{p}:brain"), ("Відкинути", f"{p}:drop")],
        ])
    if intent == "filter":
        return _kb([
            [("Стежити за таким", f"{p}:watch"), ("Разовий", f"{p}:once")],
            [("На полицю", f"{p}:shelf")],
        ])
    if intent == "brain":
        return _kb([[("Записати висновок", f"{p}:conclude"), ("Досить", f"{p}:stop")]])
    if intent == "conversation_prep":
        return _kb([[("На полицю", f"{p}:save"), ("Досить", f"{p}:stop")]])
    if intent == "query":
        return _kb([[("Показати закриті", f"{p}:closed")]])
    return None


def _store_pending(context: ContextTypes.DEFAULT_TYPE, mid: int, pending: dict) -> None:
    store = context.user_data.setdefault("pending", {})
    store[mid] = pending
    while len(store) > MAX_PENDING:
        store.pop(next(iter(store)))


async def _safe_clear_markup(query) -> None:
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


async def _to_wav(data: bytes) -> bytes | None:
    """Перекодовує аудіо (напр. Telegram ogg/opus) у wav 16kHz mono для MiMo."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-i", "-", "-f", "wav", "-ar", "16000", "-ac", "1", "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate(data)
        if proc.returncode != 0 or not stdout:
            logger.warning("ffmpeg conversion failed (code %s)", proc.returncode)
            return None
        return stdout
    except Exception:
        logger.exception("ffmpeg conversion failed")
        return None


# --- command handlers -------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or not config.is_allowed(user.id):
        return
    name = config.display_name(user.id)
    greeting = f"Привіт, {name}!" if name else "Привіт!"
    await update.message.reply_text(f"{greeting} Пиши, кидай фото чи голосові — я розберуся.")


# --- main message handler ----------------------------------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if user is None or message is None or not config.is_allowed(user.id):
        return

    user_id = user.id
    chat_id = update.effective_chat.id

    text = message.text or message.caption
    image_bytes = image_mime = None
    audio_bytes = audio_mime = None
    source = "text"

    if message.photo:
        photo = message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = bytes(await file.download_as_bytearray())
        image_mime = "image/jpeg"
        source = "image"
    elif message.voice or message.audio:
        voice = message.voice or message.audio
        file = await context.bot.get_file(voice.file_id)
        raw_bytes = bytes(await file.download_as_bytearray())
        wav_bytes = await _to_wav(raw_bytes)
        if wav_bytes:
            audio_bytes, audio_mime = wav_bytes, "audio/wav"
        else:
            audio_bytes, audio_mime = raw_bytes, voice.mime_type or "audio/ogg"
        source = "voice"

    if not text and not image_bytes and not audio_bytes:
        return

    history_text = text or ("[фото]" if image_bytes else "[голосове повідомлення]")

    # Якщо попереднім кроком було reflection — це повідомлення є відповіддю
    pending_question = context.user_data.pop("pending_reflection_question", None)
    if pending_question and text:
        await memory.add_reflection(config.DB_PATH, user_id, pending_question, text)

    user_content = brain.build_user_content(
        text=text,
        image_bytes=image_bytes,
        image_mime=image_mime,
        audio_bytes=audio_bytes,
        audio_mime=audio_mime,
    )

    snapshot = await memory.build_snapshot(config.DB_PATH, user_id)
    system_prompt = prompts.build_system_prompt(config.display_name(user_id), snapshot)

    history = await memory.get_recent_messages(config.DB_PATH, user_id)
    history_msgs = [{"role": m["role"], "content": m["content"]} for m in history]

    if context.user_data.get("brain_active"):
        history_msgs.append({"role": "system", "content": prompts.BRAIN_CONTINUE_NOTE})

    await memory.add_message(config.DB_PATH, user_id, chat_id, "user", history_text)

    try:
        result = await mimo_client.ask(system_prompt, history_msgs, user_content)
    except Exception:
        logger.exception("MiMo request failed")
        await message.reply_text("Зараз не можу подумати — спробуй ще раз трохи пізніше.")
        return

    intent = result.get("intent", "chat")
    reply = result.get("reply") or "..."
    extracted = result["extracted"]
    remind_at = extracted.get("remind_at")
    recurring = extracted.get("recurring")

    logger.info("user=%s intent=%s extracted=%s", user_id, intent, extracted)

    await memory.add_message(config.DB_PATH, user_id, chat_id, "assistant", reply)

    if intent == "reflection":
        context.user_data["pending_reflection_question"] = reply
        context.user_data["brain_active"] = False
        await message.reply_text(reply)
        return

    if intent != "brain":
        context.user_data["brain_active"] = False

    keyboard = None
    if intent in _DEFAULT_SHELF_TYPE or intent == "query":
        content = extracted.get("content") or text or history_text
        shelf_type = extracted.get("shelf_type") or _DEFAULT_SHELF_TYPE.get(intent, "thought")

        # Auto-save immediately when remind_at is set — user said "нагадай", no button needed
        if remind_at and intent in ("task", "thought", "idea"):
            await memory.add_shelf_item(
                config.DB_PATH, user_id, shelf_type, content, source, remind_at, recurring
            )
            logger.info("user=%s auto-saved reminder remind_at=%s", user_id, remind_at)
        else:
            mid = message.message_id
            pending = {
                "intent": intent,
                "content": content,
                "shelf_type": shelf_type,
                "profile_query": extracted.get("profile_query"),
                "source": source,
                "remind_at": remind_at,
                "recurring": recurring,
            }
            _store_pending(context, mid, pending)
            if intent == "brain":
                context.user_data["brain_active"] = True
            keyboard = buttons_for(intent, mid)

    await message.reply_text(reply, reply_markup=keyboard)


# --- callback query handler ---------------------------------------------------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None or not config.is_allowed(user.id):
        if query is not None:
            await query.answer()
        return

    data = query.data or ""
    parts = data.split(":")
    await query.answer()

    if parts[0] == "pend" and len(parts) == 3:
        mid = int(parts[1])
        action = parts[2]
        pending = context.user_data.get("pending", {}).get(mid)
        if pending is None:
            await _safe_clear_markup(query)
            return
        await _handle_pending_action(update, context, mid, pending, action)
    elif parts[0] == "task" and len(parts) == 4:
        shelf_id, owner_id, action = int(parts[1]), int(parts[2]), parts[3]
        if user.id != owner_id:
            return
        await _handle_task_action(update, context, shelf_id, action)


async def _handle_pending_action(update: Update, context: ContextTypes.DEFAULT_TYPE, mid: int, pending: dict, action: str) -> None:
    query = update.callback_query
    user_id = update.effective_user.id

    if action == "save":
        await memory.add_shelf_item(config.DB_PATH, user_id, pending["shelf_type"], pending["content"], pending["source"], pending.get("remind_at"), pending.get("recurring"))
        await _safe_clear_markup(query)
        await query.message.reply_text("Записано на полицю.")
    elif action in ("as_idea", "as_task"):
        new_type = "idea" if action == "as_idea" else "task"
        await memory.add_shelf_item(config.DB_PATH, user_id, new_type, pending["content"], pending["source"], pending.get("remind_at"), pending.get("recurring"))
        await _safe_clear_markup(query)
        await query.message.reply_text(f"Записано як {_TYPE_RU_LABEL[new_type]}.")
    elif action == "drop":
        await _safe_clear_markup(query)
        await query.message.reply_text("Не зберігаю.")
    elif action == "brain":
        await _start_brain(update, context, pending["content"])
    elif action == "watch":
        await memory.add_profile(config.DB_PATH, user_id, pending.get("profile_query") or pending["content"], None)
        await _safe_clear_markup(query)
        await query.message.reply_text("Стежитиму.")
    elif action == "once":
        await _safe_clear_markup(query)
    elif action == "shelf":
        await memory.add_shelf_item(config.DB_PATH, user_id, pending["shelf_type"], pending["content"], pending["source"], pending.get("remind_at"), pending.get("recurring"))
        await _safe_clear_markup(query)
        await query.message.reply_text("Записано на полицю.")
    elif action == "conclude":
        await _conclude_brain(update, context, pending)
    elif action == "stop":
        context.user_data["brain_active"] = False
        await _safe_clear_markup(query)
        await query.message.reply_text("Добре, закінчили.")
    elif action == "closed":
        items = await memory.get_shelf_by_statuses(config.DB_PATH, user_id, ["done", "dropped", "archived"])
        text = memory.format_shelf_list(items) if items else "Закритих пунктів нема."
        await query.message.reply_text(text)


async def _start_brain(update: Update, context: ContextTypes.DEFAULT_TYPE, content: str) -> None:
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    snapshot = await memory.build_snapshot(config.DB_PATH, user_id)
    system_prompt = prompts.build_system_prompt(config.display_name(user_id), snapshot)
    user_content = f"{prompts.BRAIN_FORCE_NOTE}\nТема: {content}"

    try:
        result = await mimo_client.ask(system_prompt, [], user_content)
    except Exception:
        logger.exception("MiMo request failed (brain)")
        await query.message.reply_text("Зараз не можу подумати — спробуй пізніше.")
        return

    reply = result.get("reply") or "..."
    await _safe_clear_markup(query)
    sent = await query.message.reply_text(reply)

    _store_pending(context, sent.message_id, {
        "intent": "brain",
        "content": content,
        "shelf_type": "idea",
        "profile_query": None,
        "source": "text",
    })
    context.user_data["brain_active"] = True

    await sent.edit_reply_markup(reply_markup=buttons_for("brain", sent.message_id))
    await memory.add_message(config.DB_PATH, user_id, chat_id, "assistant", reply)


async def _conclude_brain(update: Update, context: ContextTypes.DEFAULT_TYPE, pending: dict) -> None:
    query = update.callback_query
    user_id = update.effective_user.id

    snapshot = await memory.build_snapshot(config.DB_PATH, user_id)
    system_prompt = prompts.build_system_prompt(config.display_name(user_id), snapshot)
    history = await memory.get_recent_messages(config.DB_PATH, user_id)
    history_msgs = [{"role": m["role"], "content": m["content"]} for m in history]

    content = pending["content"]
    try:
        result = await mimo_client.ask(system_prompt, history_msgs, prompts.BRAIN_CONCLUDE_NOTE)
    except Exception:
        logger.exception("MiMo request failed (conclude)")
    else:
        content = result["extracted"].get("content") or result.get("reply") or content

    await memory.add_shelf_item(config.DB_PATH, user_id, "idea", content, "text")
    context.user_data["brain_active"] = False
    await _safe_clear_markup(query)
    await query.message.reply_text("Записано висновок на полицю.")


async def _handle_task_action(update: Update, context: ContextTypes.DEFAULT_TYPE, shelf_id: int, action: str) -> None:
    query = update.callback_query

    if action == "done":
        await memory.set_shelf_status(config.DB_PATH, shelf_id, "done")
        toast = "Готово ✓"
    elif action == "more":
        await memory.set_shelf_reminded(config.DB_PATH, shelf_id)
        toast = "Окей, нагадаю пізніше."
    elif action == "drop":
        await memory.set_shelf_status(config.DB_PATH, shelf_id, "dropped")
        toast = "Прибрано."
    elif action == "snooze1h":
        new_remind = (dt.now(timezone.utc) + timedelta(hours=1)).isoformat()
        await memory.set_shelf_remind_at(config.DB_PATH, shelf_id, new_remind)
        await memory.update_shelf_reminded(config.DB_PATH, shelf_id, None)
        toast = "Нагадаю через годину ⏰"
    elif action == "snooze1d":
        tomorrow_10 = (dt.now(timezone.utc) + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
        await memory.set_shelf_remind_at(config.DB_PATH, shelf_id, tomorrow_10.isoformat())
        await memory.update_shelf_reminded(config.DB_PATH, shelf_id, None)
        toast = "Нагадаю завтра 📅"
    else:
        return

    old_markup = query.message.reply_markup
    new_rows = []
    if old_markup:
        for row in old_markup.inline_keyboard:
            if not any(btn.callback_data.startswith(f"task:{shelf_id}:") for btn in row):
                new_rows.append(row)
    new_markup = InlineKeyboardMarkup(new_rows) if new_rows else None

    try:
        await query.edit_message_reply_markup(reply_markup=new_markup)
    except Exception:
        pass
    await query.answer(text=toast)


# --- daily reminder job ---------------------------------------------------------

async def daily_reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    for user_id in config.ALLOWED_USER_IDS:
        tasks = await memory.get_stale_tasks(config.DB_PATH, user_id, days=7)
        if not tasks:
            continue

        lines = ["Давно висить на полиці:"]
        keyboard_rows = []
        for t in tasks:
            lines.append(f"- {t['content']}")
            keyboard_rows.append([
                InlineKeyboardButton("Зробив", callback_data=f"task:{t['id']}:{user_id}:done"),
                InlineKeyboardButton("Ще треба", callback_data=f"task:{t['id']}:{user_id}:more"),
                InlineKeyboardButton("Відкинути", callback_data=f"task:{t['id']}:{user_id}:drop"),
            ])
            await memory.set_shelf_reminded(config.DB_PATH, t["id"])

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="\n".join(lines),
                reply_markup=InlineKeyboardMarkup(keyboard_rows),
            )
        except Exception:
            logger.exception("Failed to send daily reminder to %s", user_id)


# --- entrypoint -------------------------------------------------------------------

async def _post_init(application: Application) -> None:
    await memory.init_db(config.DB_PATH)
    for uid in config.ALLOWED_USER_IDS:
        due = await memory.get_due_reminders(config.DB_PATH, uid)
        for item in due:
            try:
                await application.bot.send_message(
                    chat_id=uid,
                    text="⏰ *Пропущене нагадування*\n\n" + item['content'].replace('_', '\_').replace('*', '\*'),
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Зробив", callback_data=f"task:{item['id']}:{uid}:done"),
                    ]])
                )
                await memory.set_shelf_reminded(config.DB_PATH, item["id"])
            except Exception:
                logger.exception("Failed to send missed reminder")




# --- check reminders job (every 60s) ----------------------------------------

async def check_reminders_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("check_reminders_job: checking...")
    for user_id in config.ALLOWED_USER_IDS:
        due = await memory.get_due_reminders(config.DB_PATH, user_id)
        logger.info("check_reminders_job: user=%s due=%s", user_id, len(due))
        if not due:
            continue
        for item in due:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="\u23f0 *\u041d\u0430\u0433\u0430\u0434\u0443\u0432\u0430\u043d\u043d\u044f*\n\n" + item["content"].replace("_", "\\_").replace("*", "\\*"),
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("\u2705 \u0417\u0440\u043e\u0431\u0438\u0432", callback_data=f'task:{item["id"]}:{user_id}:done'),
                        InlineKeyboardButton("\u23f0 +1 \u0433\u043e\u0434", callback_data=f'task:{item["id"]}:{user_id}:snooze1h'),
                        InlineKeyboardButton("\U0001f4c5 \u0417\u0430\u0432\u0442\u0440\u0430", callback_data=f'task:{item["id"]}:{user_id}:snooze1d'),
                    ]])
                )
                await memory.set_shelf_reminded(config.DB_PATH, item["id"])
            except Exception:
                logger.exception("Failed to send reminder for item %s", item["id"])


# --- morning digest job (daily at MORNING_DIGEST_HOUR) -----------------------

async def morning_digest_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    for user_id in config.ALLOWED_USER_IDS:
        items = await memory.get_today_items(config.DB_PATH, user_id)
        if not items["today"] and not items["pending"] and not items["ideas"]:
            continue

        lines = ["\U0001f305 *\u0414\u043e\u0431\u0440\u043e\u0433\u043e \u0440\u0430\u043d\u043a\u0443!*"]

        if items["today"]:
            lines.append("\n\u23f0 *\u041d\u0430 \u0441\u044c\u043e\u0433\u043e\u0434\u043d\u0456:*")
            for t in items["today"]:
                try:
                    remind_dt = dt.fromisoformat(t["remind_at"])
                    if remind_dt.tzinfo is None:
                        remind_dt = remind_dt.replace(tzinfo=timezone.utc)
                    kyiv_dt = remind_dt.astimezone(ZoneInfo("Europe/Kyiv"))
                    time_str = kyiv_dt.strftime("%H:%M")
                except Exception:
                    time_str = "??:??"
                lines.append(f"  {time_str} \u2014 {t['content']}")

        if items["pending"]:
            lines.append("\n\U0001f4cb *\u0411\u0435\u0437 \u0434\u0435\u0434\u043b\u0430\u0439\u043d\u0443:*")
            for t in items["pending"]:
                lines.append(f"  \u2022 {t['content']}")

        if items["ideas"]:
            lines.append("\n\U0001f4a1 *\u0406\u0434\u0435\u0457 \u043d\u0430 \u0440\u043e\u0437\u0431\u0456\u0440:*")
            for t in items["ideas"]:
                lines.append(f"  \u2022 {t['content']}")

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="\n".join(lines),
                parse_mode="Markdown",
            )
        except Exception:
            logger.exception("Failed to send digest to %s", user_id)


def main() -> None:
    setup_logging()

    application: Application = (
        ApplicationBuilder()
        .token(config.TELEGRAM_TOKEN)
        .post_init(_post_init)
        .connect_timeout(30)
        .read_timeout(30)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(30)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(
        MessageHandler((filters.TEXT | filters.PHOTO | filters.VOICE | filters.AUDIO) & ~filters.COMMAND, handle_message)
    )

    kyiv_tz = ZoneInfo("Europe/Kyiv")
    application.job_queue.run_daily(daily_reminder_job, time=time(hour=config.REMINDER_HOUR, tzinfo=kyiv_tz))
    application.job_queue.run_repeating(check_reminders_job, interval=60, first=10)
    application.job_queue.run_daily(morning_digest_job, time=time(hour=config.MORNING_DIGEST_HOUR, tzinfo=kyiv_tz))

    logger.info("Companion bot starting (long polling)")
    application.run_polling()


if __name__ == "__main__":
    main()
