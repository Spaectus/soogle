"""Tantivy full-text search index over the packages table.

The index is a directory on disk (SOOGLE_INDEX_PATH, default data/index).
Rebuild it with `python -m scrape index` after processing; the web app and
MCP server search it read-only and fall back to a LIKE query when it is
missing.
"""

from pathlib import Path

import tantivy
from sqlalchemy import func, select
from tqdm import tqdm

from . import config
from .db import engine
from .schema import packages

SEARCH_FIELDS = ["name", "qualified_name", "description", "readme_excerpt", "topics"]


def _schema():
    return (
        tantivy.SchemaBuilder()
        .add_integer_field("id", indexed=True, stored=True)
        .add_text_field("name", stored=True)
        .add_text_field("qualified_name", stored=True)
        .add_text_field("description", stored=True)
        .add_text_field("readme_excerpt", stored=True)
        .add_text_field("topics", stored=True)
        .build()
    )


def index_path():
    return Path(config.INDEX_PATH)


def build_index():
    """Rebuild the index from the packages table. Returns the number of documents."""
    path = index_path()
    path.mkdir(parents=True, exist_ok=True)
    index = tantivy.Index(_schema(), path=str(path))
    writer = index.writer(heap_size=15_000_000, num_threads=1)
    count = 0
    with engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(packages)).scalar_one()
        rows = conn.execute(
            select(
                packages.c.id,
                packages.c.name,
                packages.c.qualified_name,
                packages.c.description,
                packages.c.readme_excerpt,
                packages.c.topics,
            )
        ).mappings()
        for row in tqdm(rows, desc="index", unit="pkg", total=total):
            doc = tantivy.Document()
            doc.add_integer("id", row["id"])
            doc.add_text("name", row["name"] or "")
            doc.add_text("qualified_name", row["qualified_name"] or "")
            doc.add_text("description", row["description"] or "")
            doc.add_text("readme_excerpt", row["readme_excerpt"] or "")
            topics = row["topics"]
            if isinstance(topics, list):
                topics = " ".join(topics)
            doc.add_text("topics", topics or "")
            writer.add_document(doc)
            count += 1
    writer.commit()
    writer.wait_merging_threads()
    return count


def search(query, limit=1000):
    """Return package ids matching *query* ranked by relevance, or None if the index is missing."""
    path = index_path()
    if not path.exists():
        return None
    index = tantivy.Index(_schema(), path=str(path))
    searcher = index.searcher()
    parsed = index.parse_query(query, SEARCH_FIELDS)
    result = searcher.search(parsed, limit=limit)
    return [searcher.doc(addr).get_first("id") for _, addr in result.hits]