import os
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load from ~/.env (user's global env) then project .env
load_dotenv(Path.home() / ".env")
load_dotenv(override=True)

DATABASE_URL = os.getenv("DATABASE_URL")


def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def execute(query, params=None, fetch=False):
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(query, params)
    result = cur.fetchall() if fetch else None
    cur.close()
    conn.close()
    return result


def execute_one(query, params=None):
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(query, params)
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result


def execute_many(query, params_list):
    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()
    cur.executemany(query, params_list)
    conn.commit()
    cur.close()
    conn.close()


def execute_batch(query, params_list, page_size=100):
    """Insert many rows efficiently using execute_values-style batching."""
    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()
    from psycopg2.extras import execute_batch as pg_execute_batch
    pg_execute_batch(cur, query, params_list, page_size=page_size)
    conn.commit()
    cur.close()
    conn.close()
