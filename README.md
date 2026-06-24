# Companion

`Companion` is a Telegram-based personal capture bot for thoughts, tasks, ideas,
and lightweight reflection prompts.

It is built around intent routing instead of slash commands: the model decides
whether the message should be stored as a task, treated as an idea, answered as
chat, or turned into a reflection flow. The bot supports separate state per
allowed user, keeps a local SQLite memory store, and can work with text, images,
and voice notes.

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

## Setup

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

## Environment

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

## Repo notes

This public version intentionally excludes live tokens, databases, logs, backups,
and personal runtime data. Deployment details are documented at a high level, but
the live service unit and machine-specific runtime files are not included here.
