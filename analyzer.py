"""Reports for choosing when to publish in DTF Indie."""

from __future__ import annotations

import argparse
import sqlite3
from statistics import mean

from db import DEFAULT_DB, connect


def best_publish_windows(conn: sqlite3.Connection, checkpoint: int = 60, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            strftime('%w', datetime(p.published_at, 'unixepoch')) AS weekday,
            strftime('%H', datetime(p.published_at, 'unixepoch')) AS hour,
            COUNT(*) AS posts,
            ROUND(AVG(m.views), 1) AS avg_views,
            ROUND(AVG(m.velocity_5m), 1) AS avg_velocity,
            ROUND(AVG(m.engagement_rate) * 100, 2) AS avg_er_percent
        FROM posts p
        JOIN metrics m ON p.id = m.post_id
        WHERE m.minutes_since_publish = ?
        GROUP BY weekday, hour
        HAVING posts >= 2
        ORDER BY avg_views DESC
        LIMIT ?
        """,
        (checkpoint, limit),
    ).fetchall()


def recent_competition(conn: sqlite3.Connection, sample_size: int = 20) -> dict:
    rows = conn.execute(
        """
        SELECT p.id, p.title, m.views, m.velocity_5m, m.engagement_rate
        FROM posts p
        JOIN metrics m ON p.id = m.post_id
        WHERE m.minutes_since_publish = 10
        ORDER BY p.published_at DESC
        LIMIT ?
        """,
        (sample_size,),
    ).fetchall()
    if not rows:
        return {"level": "unknown", "recommendation": "Недостаточно данных", "avg_views_10m": 0, "posts": 0}

    avg_views = mean(row["views"] for row in rows)
    if avg_views < 1200:
        level, recommendation = "low", "🟢 Низкая конкуренция — можно публиковать."
    elif avg_views < 3000:
        level, recommendation = "normal", "🟡 Нормальная активность — публиковать можно, если материал сильный."
    else:
        level, recommendation = "hot", "🔴 Лента перегрета — лучше подождать 20–40 минут."
    return {"level": level, "recommendation": recommendation, "avg_views_10m": avg_views, "posts": len(rows)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze collected DTF Indie metrics")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--checkpoint", type=int, default=60)
    args = parser.parse_args()

    conn = connect(args.db)
    print("Best publish windows:")
    for row in best_publish_windows(conn, args.checkpoint):
        print(dict(row))
    print("\nLive competition:")
    print(recent_competition(conn))


if __name__ == "__main__":
    main()
