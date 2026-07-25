"""Discover new posts in the DTF Indie feed."""

from __future__ import annotations

import time

import aiohttp

from db import upsert_post

SUBSITE_ID = 287
FEED_URL = "https://api.dtf.ru/v2.1/subsite/{subsite_id}/entries?page=1&count={count}"


def _extract_author(item: dict) -> str:
    author = item.get("author") or item.get("user") or {}
    return author.get("name") or author.get("nickname") or ""


def normalize_feed_item(item: dict, position: int) -> dict:
    post_id = item.get("id") or item.get("entry", {}).get("id")
    title = item.get("title") or item.get("entry", {}).get("title") or "Без названия"
    published_at = item.get("date") or item.get("entry", {}).get("date")
    return {
        "id": int(post_id),
        "title": title,
        "published_at": int(published_at),
        "author": _extract_author(item),
        "feed_position": position,
        "url": item.get("url") or f"https://dtf.ru/indie/{post_id}",
    }


async def fetch_json(session: aiohttp.ClientSession, url: str) -> dict:
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as response:
        response.raise_for_status()
        return await response.json()


async def discover_posts(conn, session: aiohttp.ClientSession, count: int = 20) -> list[int]:
    data = await fetch_json(session, FEED_URL.format(subsite_id=SUBSITE_ID, count=count))
    items = data.get("result", {}).get("items", [])
    new_posts: list[int] = []
    now = int(time.time())

    for position, item in enumerate(items, start=1):
        post = normalize_feed_item(item, position)
        if upsert_post(conn, post, now):
            print(f'[NEW] #{position} {post["id"]} | {post["title"]}')
            new_posts.append(post["id"])

    return new_posts
