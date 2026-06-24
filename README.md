# Companion

`Companion` is a Telegram capture bot for thoughts, tasks, reminders, and
lightweight reflection prompts.

Instead of a menu-heavy slash-command flow, it uses intent routing: the model
decides whether a message should be stored as a task, treated as an idea,
answered directly, or turned into a follow-up prompt. The bot keeps separate
state per allowed user, stores memory in SQLite, and works with text, images,
and voice notes.

## What it does

- captures ideas, tasks, and reminders in a low-friction chat flow
- routes messages through structured model output instead of brittle command parsing
- keeps user state isolated inside a small SQLite-backed memory layer
- runs as a simple long-polling bot with background reminder jobs

## What this project shows

- A real Telegram bot with long-polling and background reminder jobs
- Structured LLM output with a strict JSON contract
- Multi-user state isolation on top of SQLite
- Simple operations surface: `.env`, `systemd`, rotating logs, one-process deploy
- A practical "capture first, organize later" workflow instead of a chat-first bot

## Stack

- Python
- `python-telegram-bot`
- SQLite / `aiosqlite`
- Gemini and MiMo-compatible model clients
- `systemd` for deployment

## Running locally

1. Create a virtual environment and install dependencies:

```bash
python3 -m venv venv
./venv/bin/pip install -U pip
./venv/bin/pip install -r requirements.txt
```

2. Copy the example config and fill in your values:

```bash
cp .env.example .env
```

3. Start the bot:

```bash
./venv/bin/python bot.py
```

The bot uses Telegram long polling, so it does not need an inbound port.

## Environment notes

- `TELEGRAM_TOKEN`: Telegram bot token from BotFather
- `ALLOWED_USER_IDS`: comma-separated Telegram user IDs allowed to use the bot
- `DISPLAY_NAMES`: optional `id:name` pairs for friendlier replies
- `MIMO_API_KEY`, `MIMO_BASE_URL`, `MIMO_MODEL`: MiMo-compatible model settings
- `GOOGLE_API_KEY`, `GEMINI_MODEL`: Gemini-compatible model settings
- `DB_PATH`: SQLite file path, default `companion.db`
- `REMINDER_HOUR`: daily reminder scan hour in local time
- `MORNING_DIGEST_HOUR`: optional daily digest hour
- `HERMES_ENDPOINT`: optional future integration point

## How it works

- Messages are normalized into a model request plus a user-specific state snapshot.
- The model returns strict JSON: `intent`, `reply`, and `extracted`.
- The bot turns that structured output into storage updates, buttons, or follow-up prompts.
- Reminder jobs scan stored tasks and send one compact nudge instead of noisy repeated pings.

## Public repo notes

This public version intentionally excludes live tokens, databases, logs, backups,
and personal runtime data. Deployment details are documented at a high level, but
the live service unit and machine-specific runtime files are not included here.
