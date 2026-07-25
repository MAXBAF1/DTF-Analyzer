"""Continuous DTF Indie monitor.

Run with: python monitor.py --db dtf_indie.db
"""

from __future__ import annotations

import argparse
import asyncio
import time

import aiohttp

from db import DEFAULT_DB, active_post_ids, connect
from discover import discover_posts
from tracker import CHECKPOINTS, track_post


def _cleanup_tasks(tasks: dict[int, asyncio.Task]) -> None:
    for post_id, task in list(tasks.items()):
        if not task.done():
            continue
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"[TRACK ERR] {post_id}: {exc}")
        del tasks[post_id]


async def monitor(db_path: str, poll_interval: int, feed_count: int) -> None:
    conn = connect(db_path)
    active_tasks: dict[int, asyncio.Task] = {}

    async with aiohttp.ClientSession(headers={"User-Agent": "AroundAnalytics/2.0"}) as session:
        for post_id in active_post_ids(conn, int(time.time()), CHECKPOINTS):
            active_tasks[post_id] = asyncio.create_task(track_post(conn, session, post_id))

        while True:
            try:
                _cleanup_tasks(active_tasks)
                for post_id in await discover_posts(conn, session, feed_count):
                    if post_id not in active_tasks:
                        active_tasks[post_id] = asyncio.create_task(track_post(conn, session, post_id))
                print(f"[OK] active={len(active_tasks)}")
            except Exception as exc:
                print(f"[DISCOVER ERR] {exc}")
            await asyncio.sleep(poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor DTF Indie post growth")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--poll-interval", type=int, default=15)
    parser.add_argument("--feed-count", type=int, default=20)
    args = parser.parse_args()
    asyncio.run(monitor(args.db, args.poll_interval, args.feed_count))


if __name__ == "__main__":
    main()
