import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from llm_bias import config


def db_conn():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn


@contextmanager
def db():
    with closing(db_conn()) as conn, conn:
        yield conn


def init_db():
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with db() as conn:
        conn.executescript(schema)
