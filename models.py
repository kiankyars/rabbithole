"""Schema migration -- run once to set up all tables."""

from db import get_conn

# Base schema for fresh installs
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    title TEXT,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    message_count INT DEFAULT 0,
    model_slug TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT,
    created_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);

CREATE TABLE IF NOT EXISTS rabbit_holes (
    id SERIAL PRIMARY KEY,
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'active',
    priority_score FLOAT DEFAULT 0.0,
    last_researched_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rabbit_hole_conversations (
    rabbit_hole_id INT REFERENCES rabbit_holes(id) ON DELETE CASCADE,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
    PRIMARY KEY (rabbit_hole_id, conversation_id)
);

CREATE TABLE IF NOT EXISTS insights (
    id SERIAL PRIMARY KEY,
    rabbit_hole_id INT REFERENCES rabbit_holes(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    source_url TEXT,
    grounded BOOLEAN DEFAULT FALSE,
    urgency TEXT DEFAULT 'low',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_insights_rh ON insights(rabbit_hole_id);

CREATE TABLE IF NOT EXISTS research_runs (
    id SERIAL PRIMARY KEY,
    rabbit_hole_id INT REFERENCES rabbit_holes(id) ON DELETE CASCADE,
    query_sent TEXT,
    deepseek_response TEXT,
    you_com_results TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS daily_plans (
    id SERIAL PRIMARY KEY,
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    plan_date DATE,
    plan_json TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

# Migration for existing installs: add user_id columns + indexes
MIGRATE_SQL = """
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS user_id TEXT REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE rabbit_holes ADD COLUMN IF NOT EXISTS user_id TEXT REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE daily_plans ADD COLUMN IF NOT EXISTS user_id TEXT REFERENCES users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_rabbit_holes_user ON rabbit_holes(user_id);

-- Drop old unique constraint on plan_date and add user-scoped one
ALTER TABLE daily_plans DROP CONSTRAINT IF EXISTS daily_plans_plan_date_key;
CREATE UNIQUE INDEX IF NOT EXISTS daily_plans_user_date ON daily_plans(user_id, plan_date);
"""

# Backfill: create a default user for any orphaned data
BACKFILL_SQL = """
INSERT INTO users (id, name) VALUES ('default', 'Default User') ON CONFLICT DO NOTHING;
UPDATE conversations SET user_id = 'default' WHERE user_id IS NULL;
UPDATE rabbit_holes SET user_id = 'default' WHERE user_id IS NULL;
UPDATE daily_plans SET user_id = 'default' WHERE user_id IS NULL;
"""


def apply_schema():
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(SCHEMA_SQL)
    cur.execute(MIGRATE_SQL)
    cur.execute(BACKFILL_SQL)
    cur.close()
    conn.close()
    print("Schema applied successfully.")


if __name__ == "__main__":
    apply_schema()
