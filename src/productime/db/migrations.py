import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS sounds (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  source_type TEXT NOT NULL CHECK (source_type IN ('bundled', 'custom')),
  file_path TEXT NOT NULL,
  duration_seconds REAL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS targets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  tag_label TEXT NOT NULL DEFAULT 'Study',
  tag_color TEXT NOT NULL DEFAULT 'purple',
  target_total_minutes INTEGER NOT NULL CHECK (target_total_minutes >= 1),
  work_minutes INTEGER NOT NULL CHECK (work_minutes >= 1),
  short_break_minutes INTEGER NOT NULL CHECK (short_break_minutes >= 0),
  long_rest_enabled INTEGER NOT NULL DEFAULT 0,
  long_rest_every INTEGER NOT NULL DEFAULT 3 CHECK (long_rest_every >= 1),
  long_rest_minutes INTEGER NOT NULL DEFAULT 30 CHECK (long_rest_minutes >= 1),
  snooze_limit INTEGER NULL,
  sound_id TEXT NOT NULL REFERENCES sounds(id),
  status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'in_progress', 'completed')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS target_batches (
  id TEXT PRIMARY KEY,
  target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  batch_index INTEGER NOT NULL CHECK (batch_index >= 1),
  subtask_title TEXT NOT NULL,
  planned_work_minutes INTEGER NOT NULL CHECK (planned_work_minutes >= 1),
  planned_break_minutes INTEGER NOT NULL CHECK (planned_break_minutes >= 0),
  break_type TEXT NOT NULL CHECK (break_type IN ('none', 'short', 'long')),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'completed', 'skipped')),
  actual_work_seconds INTEGER NOT NULL DEFAULT 0,
  snooze_seconds_used INTEGER NOT NULL DEFAULT 0,
  snooze_count INTEGER NOT NULL DEFAULT 0,
  completed_at TEXT NULL,
  UNIQUE(target_id, batch_index)
);

CREATE TABLE IF NOT EXISTS alarms (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  hour_24 INTEGER NOT NULL CHECK (hour_24 BETWEEN 0 AND 23),
  minute INTEGER NOT NULL CHECK (minute BETWEEN 0 AND 59),
  repeat_days_json TEXT NOT NULL DEFAULT '[]',
  sound_id TEXT NOT NULL REFERENCES sounds(id),
  snooze_interval_minutes INTEGER NOT NULL DEFAULT 5 CHECK (snooze_interval_minutes IN (1,5,10,15,20,25,30)),
  snooze_limit INTEGER NULL,
  mission_mode TEXT NOT NULL DEFAULT 'off' CHECK (mission_mode IN ('off', 'typing', 'math', 'click')),
  mission_count INTEGER NOT NULL DEFAULT 1 CHECK (mission_count BETWEEN 1 AND 3),
  mission_difficulty TEXT NOT NULL DEFAULT 'easy' CHECK (mission_difficulty IN ('easy', 'medium', 'hard')),
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  mission_clicks INTEGER NOT NULL DEFAULT 30 CHECK (mission_clicks BETWEEN 10 AND 200)
);

CREATE TABLE IF NOT EXISTS timers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  duration_seconds INTEGER NOT NULL CHECK (duration_seconds >= 1),
  sound_id TEXT NOT NULL REFERENCES sounds(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS focus_logs (
  id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL CHECK (source_type IN ('target_batch', 'timer')),
  target_id TEXT NULL,
  batch_id TEXT NULL,
  title TEXT NOT NULL,
  tag_label TEXT NOT NULL DEFAULT 'Focus',
  started_at TEXT NOT NULL,
  ended_at TEXT NOT NULL,
  duration_seconds INTEGER NOT NULL CHECK (duration_seconds >= 0),
  subtask_completed INTEGER NOT NULL DEFAULT 0,
  local_date_key TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_preferences (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Where a running target or timer stood when the app closed. Never part of a backup.
CREATE TABLE IF NOT EXISTS runtime_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_target_batches_target ON target_batches(target_id, batch_index);
CREATE INDEX IF NOT EXISTS idx_focus_logs_date ON focus_logs(local_date_key);
"""

# Only the file name is stored; it is resolved against the Assets folder at playback time.
BUNDLED_SOUNDS = (
    ("bundled-8footdino", "8-Foot Dino on Scratch", "8footdino_on_scratch-alarm.mp3", 22.96),
    ("bundled-biohazard", "Biohazard", "biohazard-alarm.mp3", 50.0),
    ("bundled-community", "Community", "community-alarm.mp3", 6.12),
    ("bundled-digital", "Digital Alarm Clock", "digital-alarm-clock.mp3", 16.04),
    ("bundled-funny", "Funny", "funny-alarm.mp3", 9.5),
    ("bundled-lofi", "Lo-fi Alarm Clock", "lo-fi-alarm-clock.mp3", 59.95),
)


SCHEMA_VERSION = 2

# Version 2 adds the click mission. SQLite cannot change a CHECK constraint in place, so the
# alarms table is rebuilt and every row copied, all in one transaction.
ALARMS_UPGRADE = """
CREATE TABLE alarms_upgrade (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  hour_24 INTEGER NOT NULL CHECK (hour_24 BETWEEN 0 AND 23),
  minute INTEGER NOT NULL CHECK (minute BETWEEN 0 AND 59),
  repeat_days_json TEXT NOT NULL DEFAULT '[]',
  sound_id TEXT NOT NULL REFERENCES sounds(id),
  snooze_interval_minutes INTEGER NOT NULL DEFAULT 5 CHECK (snooze_interval_minutes IN (1,5,10,15,20,25,30)),
  snooze_limit INTEGER NULL,
  mission_mode TEXT NOT NULL DEFAULT 'off' CHECK (mission_mode IN ('off', 'typing', 'math', 'click')),
  mission_count INTEGER NOT NULL DEFAULT 1 CHECK (mission_count BETWEEN 1 AND 3),
  mission_difficulty TEXT NOT NULL DEFAULT 'easy' CHECK (mission_difficulty IN ('easy', 'medium', 'hard')),
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  mission_clicks INTEGER NOT NULL DEFAULT 30 CHECK (mission_clicks BETWEEN 10 AND 200)
);
"""

def _upgrade_alarms(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(alarms)").fetchall()}
    if "mission_clicks" in columns:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(ALARMS_UPGRADE)
        conn.execute(
            "INSERT INTO alarms_upgrade (id, name, hour_24, minute, repeat_days_json, sound_id,"
            " snooze_interval_minutes, snooze_limit, mission_mode, mission_count, mission_difficulty,"
            " enabled, created_at) SELECT id, name, hour_24, minute, repeat_days_json, sound_id,"
            " snooze_interval_minutes, snooze_limit, mission_mode, mission_count, mission_difficulty,"
            " enabled, created_at FROM alarms"
        )
        conn.execute("DROP TABLE alarms")
        conn.execute("ALTER TABLE alarms_upgrade RENAME TO alarms")
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise


def apply(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        _upgrade_alarms(conn)
        conn.execute("PRAGMA user_version = 2")
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO sounds (id, display_name, source_type, file_path, duration_seconds)"
            " VALUES (?, ?, 'bundled', ?, ?)",
            BUNDLED_SOUNDS,
        )
