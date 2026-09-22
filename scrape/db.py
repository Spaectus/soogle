"""Database helpers for Soogle scrapers.

Supports MySQL (default) and SQLite.  Set SOOGLE_DB_ENGINE=sqlite to use a
local SQLite file (SOOGLE_DB_PATH, default soogle.db) instead of a MySQL
server.  The SQLite path transpiles the MySQL-flavored raw SQL to SQLite at
the cursor (via sqlglot, plus a small fallback for the upsert and NOW() that
sqlglot doesn't translate), so the scrapers' SQL stays unchanged.
"""

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager

import pymysql
import sqlglot

from . import config

_BLOCKLIST = None

# Unique constraint each upsert targets, per table.  Used to translate
# MySQL's `ON DUPLICATE KEY UPDATE` into SQLite's `ON CONFLICT(...) DO UPDATE`.
_CONFLICT_COLS = {
    "packages": "site_id, external_id",
    "site_analyses": "domain",
    "videos": "video_id",
}


def _translate(sql):
    """Translate the MySQL-isms in a SQL statement to SQLite."""
    sql = sql.replace("%%", "%")
    sql = sql.replace("%s", "?")
    sql = sql.replace("NOW()", "datetime('now')")
    m = re.search(r"INSERT INTO (\w+)", sql)
    if m and "ON DUPLICATE KEY UPDATE" in sql:
        cols = _CONFLICT_COLS.get(m.group(1))
        if cols is None:
            raise ValueError(
                f"ON DUPLICATE KEY UPDATE on unknown table {m.group(1)!r}"
            )
        sql = sql.replace(
            "ON DUPLICATE KEY UPDATE", f"ON CONFLICT({cols}) DO UPDATE SET"
        )
        sql = re.sub(r"VALUES\((\w+)\)", r"excluded.\1", sql)
    return sqlglot.transpile(sql, read="mysql", write="sqlite")[0]


class _SqliteCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        return super().execute(_translate(sql), parameters)

    # sqlite3.Cursor's __enter__/__exit__ are not inherited by subclasses.
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _SqliteConnection(sqlite3.Connection):
    def cursor(self, *args, **kwargs):
        cur = _SqliteCursor(self, *args, **kwargs)
        cur.row_factory = self.row_factory
        return cur


def connect():
    if config.DB_ENGINE == "sqlite":
        conn = sqlite3.connect(config.DB_PATH, factory=_SqliteConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    return pymysql.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASS,
        database=config.DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


# Exposed so callers don't import pymysql directly.
IntegrityError = sqlite3.IntegrityError if config.DB_ENGINE == "sqlite" else pymysql.err.IntegrityError


def load_blocklist(conn):
    """Load the set of blocked (site_name, external_id) pairs from the blocklist table."""
    global _BLOCKLIST
    if _BLOCKLIST is not None:
        return _BLOCKLIST
    _BLOCKLIST = set()
    with conn.cursor() as cur:
        cur.execute("SELECT site_name, external_id FROM blocklist")
        for row in cur.fetchall():
            _BLOCKLIST.add((row["site_name"], row["external_id"]))
    return _BLOCKLIST


def is_blocked(conn, site_name, external_id):
    """Check if an external_id is on the blocklist."""
    return (site_name, external_id) in load_blocklist(conn)


@contextmanager
def connection():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn):
    """Context manager that commits on success, rolls back on exception."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_site_id(conn, site_name):
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM sites WHERE name = %s", (site_name,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Unknown site: {site_name}")
        return row["id"]


def create_scrape_job(conn, site_id, job_type="full_crawl"):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO scrape_jobs (site_id, job_type, status, started_at) "
            "VALUES (%s, %s, 'running', NOW())",
            (site_id, job_type),
        )
        conn.commit()
        return cur.lastrowid


def finish_scrape_job(conn, job_id, items_found, items_processed, items_failed, error=None):
    status = "failed" if error else "completed"
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE scrape_jobs SET status=%s, completed_at=NOW(), "
            "items_found=%s, items_processed=%s, items_failed=%s, error_message=%s "
            "WHERE id=%s",
            (status, items_found, items_processed, items_failed, error, job_id),
        )
        conn.commit()


def compute_checksum(data):
    """SHA-256 of the JSON-serialized data, for change detection."""
    raw = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def insert_scrape_raw(conn, job_id, site_id, external_id, raw_metadata):
    """Insert a raw scraped record.  Returns the row id."""
    checksum = compute_checksum(raw_metadata)

    # Skip if we already have an identical checksum for this source
    with conn.cursor() as cur:
        cur.execute(
            "SELECT sr.id FROM scrape_raw sr "
            "JOIN packages p ON p.id = sr.package_id "
            "WHERE sr.site_id = %s AND sr.external_id = %s "
            "AND p.scrape_checksum = %s AND sr.status = 'processed' "
            "LIMIT 1",
            (site_id, external_id, checksum),
        )
        if cur.fetchone():
            return None  # unchanged, skip

        cur.execute(
            "INSERT INTO scrape_raw "
            "(scrape_job_id, site_id, external_id, raw_metadata, raw_checksum) "
            "VALUES (%s, %s, %s, %s, %s)",
            (job_id, site_id, external_id, json.dumps(raw_metadata, default=str), checksum),
        )
        conn.commit()
        return cur.lastrowid


def fetch_pending_raw(conn, limit=100):
    """Fetch a batch of pending scrape_raw rows for processing."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, scrape_job_id, site_id, external_id, raw_metadata, raw_checksum "
            "FROM scrape_raw WHERE status = 'pending' "
            "ORDER BY id LIMIT %s",
            (limit,),
        )
        rows = cur.fetchall()
        if rows:
            ids = [r["id"] for r in rows]
            placeholders = ",".join(["%s"] * len(ids))
            cur.execute(
                f"UPDATE scrape_raw SET status='processing' WHERE id IN ({placeholders})",
                ids,
            )
            conn.commit()
        return rows


def get_site_name(conn, site_id):
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM sites WHERE id = %s", (site_id,))
        row = cur.fetchone()
        return row["name"] if row else None
