#!/usr/bin/env python3
"""SQLite backend end-to-end: scrape_raw -> process -> packages.

Exercises the MySQL->SQLite translation layer in scrape/db.py against a real
SQLite file: schema load, scrape job, scrape_raw insert, processing into
packages, the ON CONFLICT upsert (update not duplicate), INSERT OR IGNORE,
and NOW() translation.

No MySQL env vars are set here, so the engine auto-detects to SQLite.

Run:  python3 tests/test_sqlite_pipeline.py
"""

import os
import sys
import tempfile

os.environ["SOOGLE_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3

from scrape import config, db
from scrape.processor import process_all

results = []


def check(condition, label):
    results.append(bool(condition))
    print(f"{'PASS' if condition else 'FAIL'}: {label}")


check(config.DB_ENGINE == "sqlite", "engine auto-detects to sqlite without MySQL vars")
check(config.DB_PATH == os.environ["SOOGLE_DB_PATH"], "DB_PATH read from env")


schema = os.path.join(os.path.dirname(__file__), "..", "db", "schema.sqlite.sql")
with open(schema) as f:
    sqlite3.connect(config.DB_PATH).executescript(f.read())

with db.connection() as conn:
    site_id = db.get_site_id(conn, "github")
    job_id = db.create_scrape_job(conn, site_id, "incremental")

    meta = {
        "name": "pharo-project",
        "full_name": "pharo-project/pharo",
        "description": "Pharo Smalltalk",
        "html_url": "https://github.com/pharo-project/pharo",
        "clone_url": "https://github.com/pharo-project/pharo.git",
        "stargazers_count": 100,
        "forks_count": 10,
        "size": 5000,
        "license": {"spdx_id": "MIT"},
        "fork": False,
        "archived": False,
        "default_branch": "master",
        "topics": ["pharo", "smalltalk"],
        "created_at": "2024-01-15T10:30:00Z",
        "updated_at": "2024-01-15T10:30:00Z",
        "pushed_at": "2024-01-15T10:30:00Z",
    }
    row_id = db.insert_scrape_raw(conn, job_id, site_id, "pharo-project/pharo", meta)
    check(row_id is not None, "insert_scrape_raw returns a row id")

    result = process_all(conn)
    check(result["processed"] == 1 and result["errors"] == 0,
          "process_all processes the row")

    with conn.cursor() as cur:
        cur.execute("SELECT name, dialect, stars FROM packages")
        pkg = cur.fetchone()
        check(pkg and pkg["name"] == "pharo-project" and pkg["dialect"] == "pharo",
              "package upserted with detected dialect")

    # Re-scrape with changed stars -> ON CONFLICT update, not a duplicate
    meta["stargazers_count"] = 200
    db.insert_scrape_raw(conn, job_id, site_id, "pharo-project/pharo", meta)
    process_all(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM packages")
        check(cur.fetchone()["n"] == 1, "re-process updates, does not duplicate")
        cur.execute("SELECT stars FROM packages")
        check(cur.fetchone()["stars"] == 200, "upsert updates changed fields")

    # INSERT IGNORE translation (blocklist)
    cur = conn.cursor()
    cur.execute(
        "INSERT IGNORE INTO blocklist (external_id, site_name, reason) "
        "VALUES (%s, %s, %s)",
        ("pharo-project/pharo", "github", "test"),
    )
    conn.commit()
    cur.execute(
        "INSERT IGNORE INTO blocklist (external_id, site_name, reason) "
        "VALUES (%s, %s, %s)",
        ("pharo-project/pharo", "github", "test"),
    )
    conn.commit()
    cur.execute("SELECT COUNT(*) AS n FROM blocklist")
    check(cur.fetchone()["n"] == 1, "INSERT IGNORE dedupes on conflict")

    # NOW() translation (scrape_jobs timestamps)
    cur.execute("SELECT started_at FROM scrape_jobs WHERE id = %s", (job_id,))
    check(cur.fetchone()["started_at"] is not None, "NOW() translated to a timestamp")

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)