#!/usr/bin/env python3
"""Tantivy search index ranks packages by relevance, not substring match.

Run:  python3 tests/test_search_index.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

tmpdir = tempfile.mkdtemp()
os.environ["SOOGLE_DB_ENGINE"] = "sqlite"
os.environ["SOOGLE_DB_PATH"] = os.path.join(tmpdir, "test.db")
os.environ["SOOGLE_INDEX_PATH"] = os.path.join(tmpdir, "index")

from sqlalchemy import select

from scrape.db import engine
from scrape.index import build_index, search
from scrape.schema import packages, sites

results = []


def check(condition, label):
    results.append(bool(condition))
    print(f"{'PASS' if condition else 'FAIL'}: {label}")


with engine.begin() as conn:
    site_id = conn.execute(select(sites.c.id).where(sites.c.name == "github")).scalar_one()
    for name, desc in [
        ("Seaside", "A web framework for Pharo Smalltalk"),
        ("Moose", "Metaobject protocol for Pharo"),
        ("Zinc", "A widget toolkit for Pharo"),
    ]:
        conn.execute(packages.insert().values(
            name=name, qualified_name=f"x/{name}", description=desc,
            dialect="pharo", dialect_confidence=90, file_format="tonel",
            site_id=site_id, external_id=f"x/{name}", stars=0, is_active=True,
        ))

check(search("web framework") is None, "search returns None before the index is built")

count = build_index()
check(count == 3, f"build_index indexes all packages (got {count})")

ids = search("web framework")
check(ids == [1], f"multi-term query matches the right package (got {ids})")

ids = search("pharo")
check(ids == [2, 3, 1], f"single term matches all and ranks by relevance (got {ids})")

ids = search("widget")
check(ids == [3], f"a term in one package only matches that package (got {ids})")

ids = search("nonexistentterm")
check(ids == [], f"a missing term returns no hits (got {ids})")

print("\n" + "=" * 60)
failed = results.count(False)
print(f"Results: {results.count(True)} passed, {failed} failed")
sys.exit(1 if failed else 0)