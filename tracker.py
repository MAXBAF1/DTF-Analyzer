"""Track post counters and derive growth metrics."""

from __future__ import annotations

import asyncio
import time

import aiohttp

from db import insert_metric
from discover import fetch_json

CONTENT_URL = "https://api.dtf.ru/v2.10/content?id={post_id}&markdown=false"
CHECKPOINTS = [1, 2, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 360, 720, 1440]
# TODO(early-dynamics): consider sub-minute checkpoints (15s/30s/45s) or a
# separate early-tracking mode to make the first minute curve visible.


def growth_rate(prev_views: int, curr_views: int, delta_minutes: float) -> float:
    if delta_minutes <= 0:
        return 0.0
    return (curr_views - prev_views) / delta_minutes


def engagement_rate(likes: int, comments: int, views: int) -> float:
    return 0.0 if views <= 0 else (likes + comments) / views


def _counter_value(source: dict, *names: str) -> int:
    for name in names:
        value = source.get(name)
        if value is not None:
            return int(value)
    return 0


def _metrics_from_entry(entry: dict) -> dict:
    counters = entry.get("counters", {})
    return {
        "views": _counter_value(counters, "views"),
        "likes": _counter_value(counters, "likes", "favorites"),
        "comments": _counter_value(counters, "comments", "comments_count", "commentsCount"),
        "favorites": _counter_value(counters, "favorites"),
        "reactions": _counter_value(counters, "reactions"),
        "reads": _counter_value(counters, "reads"),
        "hits": _counter_value(counters, "hits"),
        "timespent": _counter_value(counters, "timespent"),
        "online": _counter_value(counters, "online"),
    }


async def fetch_post_metrics(session: aiohttp.ClientSession, post_id: int) -> dict:
    data = await fetch_json(session, CONTENT_URL.format(post_id=post_id))
    result = data.get("result", data)
    entry = result.get("entry") or result.get("data") or result
    return _metrics_from_entry(entry)


def build_metric(conn, post_id: int, raw: dict, checkpoint_minute: float, now: int) -> dict:
    previous = conn.execute(
        """
        SELECT minutes_since_publish, views, velocity_5m
        FROM metrics WHERE post_id=?
        ORDER BY minutes_since_publish DESC LIMIT 1
        """,
        (post_id,),
    ).fetchone()
    baseline = conn.execute(
        """
        SELECT minutes_since_publish, views
        FROM metrics WHERE post_id=? AND minutes_since_publish <= ?
        ORDER BY minutes_since_publish DESC LIMIT 1
        """,
        (post_id, max(0, checkpoint_minute - 5)),
    ).fetchone()

    total_velocity = raw["views"] / checkpoint_minute if checkpoint_minute > 0 else 0.0
    if baseline:
        delta_views = raw["views"] - baseline["views"]
        delta_minutes = checkpoint_minute - baseline["minutes_since_publish"]
    elif previous:
        delta_views = raw["views"] - previous["views"]
        delta_minutes = checkpoint_minute - previous["minutes_since_publish"]
    else:
        delta_views = raw["views"]
        delta_minutes = checkpoint_minute

    velocity_5m = growth_rate(0, delta_views, delta_minutes)
    previous_velocity = previous["velocity_5m"] if previous else None
    acceleration = None if previous_velocity is None else velocity_5m - previous_velocity

    feed_position_row = conn.execute(
        "SELECT last_feed_position FROM posts WHERE id=?", (post_id,)
    ).fetchone()

    return {
        "post_id": post_id,
        "ts": now,
        "minutes_since_publish": checkpoint_minute,
        "views": raw["views"],
        "likes": raw["likes"],
        "comments": raw["comments"],
        "favorites": raw.get("favorites", 0),
        "reactions": raw.get("reactions", 0),
        "reads": raw.get("reads", 0),
        "hits": raw.get("hits", 0),
        "timespent": raw.get("timespent", 0),
        "online": raw.get("online", 0),
        "feed_position": feed_position_row["last_feed_position"] if feed_position_row else None,
        "views_per_minute": total_velocity,
        "views_last_5m": delta_views,
        "velocity_5m": velocity_5m,
        "acceleration": acceleration,
        "engagement_rate": engagement_rate(raw["likes"], raw["comments"], raw["views"]),
    }


async def track_post(conn, session: aiohttp.ClientSession, post_id: int) -> None:
    row = conn.execute("SELECT published_at, title FROM posts WHERE id=?", (post_id,)).fetchone()
    if not row:
        return
    print(f'[TRACK] {post_id} | {row["title"]}')

    for minute in CHECKPOINTS:
        existing = conn.execute(
            "SELECT 1 FROM metrics WHERE post_id=? AND minutes_since_publish=?",
            (post_id, minute),
        ).fetchone()
        if existing:
            continue

        wait = row["published_at"] + minute * 60 - time.time()
        if wait > 0:
            await asyncio.sleep(wait)

        try:
            raw = await fetch_post_metrics(session, post_id)
            metric = build_metric(conn, post_id, raw, minute, int(time.time()))
            insert_metric(conn, metric)
            print(
                f'[{post_id}] +{minute:>4}m | {raw["views"]:>6} views | '
                f'{metric["velocity_5m"]:>7.1f} v/m | Δ5m {metric["views_last_5m"]:>5} | '
                f'accel {metric["acceleration"] if metric["acceleration"] is not None else 0:>7.1f} | '
                f'ER {metric["engagement_rate"]:.2%}'
            )
        except Exception as exc:
            print(f'[ERR] {post_id} +{minute}m: {exc}')
