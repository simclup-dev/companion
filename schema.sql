CREATE TABLE IF NOT EXISTS shelf (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id   INTEGER NOT NULL,
  created   TEXT NOT NULL,            -- ISO timestamp
  type      TEXT NOT NULL,            -- 'task' | 'idea' | 'thought'
  content   TEXT NOT NULL,            -- нормалізований текст
  status    TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'done' | 'dropped' | 'archived'
  source    TEXT,                     -- 'text' | 'image' | 'voice'
  reminded  TEXT                      -- ISO timestamp останнього нагадування (для task)
);

CREATE TABLE IF NOT EXISTS profiles (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id   INTEGER NOT NULL,
  created   TEXT NOT NULL,
  query     TEXT NOT NULL,            -- що відстежувати (відеокарта, техніка...)
  criteria  TEXT,                     -- критерії / нотатки
  status    TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS reflections (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id   INTEGER NOT NULL,
  created   TEXT NOT NULL,
  question  TEXT,                     -- питання, яке поставив бот
  answer    TEXT                      -- відповідь користувача
);

CREATE TABLE IF NOT EXISTS messages (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id   INTEGER NOT NULL,
  chat_id   INTEGER NOT NULL,
  role      TEXT NOT NULL,            -- 'user' | 'assistant'
  content   TEXT NOT NULL,
  created   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shelf_user   ON shelf(user_id, status);
CREATE INDEX IF NOT EXISTS idx_msg_user     ON messages(user_id, created);
CREATE INDEX IF NOT EXISTS idx_refl_user    ON reflections(user_id, created);
CREATE INDEX IF NOT EXISTS idx_prof_user    ON profiles(user_id, status);

-- v2: scheduled reminders
ALTER TABLE shelf ADD COLUMN remind_at TEXT;
ALTER TABLE shelf ADD COLUMN recurring TEXT;
CREATE INDEX IF NOT EXISTS idx_shelf_remind ON shelf(user_id, remind_at, status);
