"""SQLite helpers for the DTF Indie monitor."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

DEFAULT_DB = "dtf_indie.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    published_at INTEGER NOT NULL,
    author TEXT DEFAULT '',
    first_seen_at INTEGER NOT NULL,
    last_feed_position INTEGER,
    url TEXT DEFAULT '',
    date_modified INTEGER,
    subsite_id INTEGER,
    subsite_name TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS metrics (
    post_id INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    minutes_since_publish REAL NOT NULL,
    views INTEGER NOT NULL,
    likes INTEGER NOT NULL,
    comments INTEGER NOT NULL,
    favorites INTEGER DEFAULT 0,
    reactions INTEGER DEFAULT 0,
    reads INTEGER DEFAULT 0,
    hits INTEGER DEFAULT 0,
    timespent INTEGER DEFAULT 0,
    online INTEGER DEFAULT 0,
    feed_position INTEGER,
    views_per_minute REAL,
    views_last_5m INTEGER,
    velocity_5m REAL,
    acceleration REAL,
    engagement_rate REAL,
    PRIMARY KEY (post_id, ts),
    FOREIGN KEY (post_id) REFERENCES posts(id)
);

CREATE INDEX IF NOT EXISTS idx_metrics_post_minutes
    ON metrics(post_id, minutes_since_publish);
CREATE INDEX IF NOT EXISTS idx_posts_published_at
    ON posts(published_at);
"""


def connect(db_path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate_legacy_schema(conn)
    conn.commit()


def _migrate_legacy_schema(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(posts)").fetchall()
    }
    post_columns = {
        "first_seen_at": "INTEGER NOT NULL DEFAULT 0",
        "last_feed_position": "INTEGER",
        "url": "TEXT DEFAULT ''",
        "date_modified": "INTEGER",
        "subsite_id": "INTEGER",
        "subsite_name": "TEXT DEFAULT ''",
    }
    for name, ddl in post_columns.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE posts ADD COLUMN {name} {ddl}")

    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(metrics)").fetchall()
    }
    metric_columns = {
        "favorites": "INTEGER DEFAULT 0",
        "reactions": "INTEGER DEFAULT 0",
        "reads": "INTEGER DEFAULT 0",
        "hits": "INTEGER DEFAULT 0",
        "timespent": "INTEGER DEFAULT 0",
        "online": "INTEGER DEFAULT 0",
        "feed_position": "INTEGER",
        "views_per_minute": "REAL",
        "views_last_5m": "INTEGER",
        "velocity_5m": "REAL",
        "acceleration": "REAL",
        "engagement_rate": "REAL",
    }
    for name, ddl in metric_columns.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE metrics ADD COLUMN {name} {ddl}")


def upsert_post(conn: sqlite3.Connection, post: dict, now: int) -> bool:
    exists = conn.execute("SELECT 1 FROM posts WHERE id=?", (post["id"],)).fetchone()
    conn.execute(
        """
        INSERT INTO posts (
            id, title, published_at, author, first_seen_at, last_feed_position, url,
            date_modified, subsite_id, subsite_name
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,
            author=excluded.author,
            last_feed_position=excluded.last_feed_position,
            url=excluded.url,
            date_modified=excluded.date_modified,
            subsite_id=excluded.subsite_id,
            subsite_name=excluded.subsite_name
        """,
        (
            post["id"], post["title"], post["published_at"], post.get("author", ""),
            now, post.get("feed_position"), post.get("url", ""),
            post.get("date_modified"), post.get("subsite_id"), post.get("subsite_name", ""),
        ),
    )
    conn.commit()
    return exists is None


def insert_metric(conn: sqlite3.Connection, metric: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO metrics (
            post_id, ts, minutes_since_publish, views, likes, comments, favorites,
            reactions, reads, hits, timespent, online, feed_position, views_per_minute,
            views_last_5m, velocity_5m, acceleration, engagement_rate
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metric["post_id"], metric["ts"], metric["minutes_since_publish"],
            metric["views"], metric["likes"], metric["comments"], metric.get("favorites", 0),
            metric.get("reactions", 0), metric.get("reads", 0), metric.get("hits", 0),
            metric.get("timespent", 0), metric.get("online", 0), metric.get("feed_position"),
            metric.get("views_per_minute"), metric.get("views_last_5m"),
            metric.get("velocity_5m"), metric.get("acceleration"), metric.get("engagement_rate"),
        ),
    )
    conn.commit()


def active_post_ids(
    conn: sqlite3.Connection,
    now: int,
    checkpoints: Iterable[float],
    max_track_age_minutes: int | None = None,
) -> list[int]:
    max_age = max(checkpoints) * 60
    if max_track_age_minutes is not None:
        max_age = min(max_age, max_track_age_minutes * 60)
    rows = conn.execute(
        "SELECT id FROM posts WHERE published_at + ? >= ?",
        (max_age, now),
    ).fetchall()
    return [row["id"] for row in rows]
