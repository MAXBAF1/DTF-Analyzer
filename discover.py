"""Discover new posts in the DTF Indie feed."""

from __future__ import annotations

import time

import aiohttp

from db import upsert_post

SUBSITE_ID = 287
FEED_URLS = [
    "https://api.dtf.ru/v1.6/subsite/{subsite_id}/timeline/recent?count={count}",
    "https://api.dtf.ru/v1.6/subsite/{subsite_id}/timeline/new?count={count}",
    "https://api.dtf.ru/v2.1/subsite/{subsite_id}/entries?page=1&count={count}",
]


def _entry_payload(item: dict) -> dict:
    return item.get("entry") or item.get("data") or item


def _extract_author(item: dict) -> str:
    entry = _entry_payload(item)
    author = entry.get("author") or entry.get("user") or item.get("author") or item.get("user") or {}
    return author.get("name") or author.get("nickname") or ""


def normalize_feed_item(item: dict, position: int) -> dict:
    entry = _entry_payload(item)
    post_id = entry.get("id") or item.get("id")
    title = entry.get("title") or item.get("title") or "Без названия"
    published_at = entry.get("date") or entry.get("created") or item.get("date") or item.get("created")
    return {
        "id": int(post_id),
        "title": title,
        "published_at": int(published_at),
        "author": _extract_author(item),
        "feed_position": position,
        "url": entry.get("url") or item.get("url") or f"https://dtf.ru/indie/{post_id}",
    }


async def fetch_json(session: aiohttp.ClientSession, url: str) -> dict:
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as response:
        response.raise_for_status()
        return await response.json()


async def fetch_first_available(session: aiohttp.ClientSession, urls: list[str]) -> dict:
    last_error: Exception | None = None
    for url in urls:
        try:
            return await fetch_json(session, url)
        except aiohttp.ClientResponseError as exc:
            if exc.status not in {404, 410}:
                raise
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("No DTF feed endpoints configured")


def extract_items(data: dict) -> list[dict]:
    result = data.get("result", data)
    if isinstance(result, list):
        return result
    for key in ("items", "entries", "timeline"):
        value = result.get(key) if isinstance(result, dict) else None
        if isinstance(value, list):
            return value
    return []


async def discover_posts(conn, session: aiohttp.ClientSession, count: int = 20) -> list[int]:
    urls = [url.format(subsite_id=SUBSITE_ID, count=count) for url in FEED_URLS]
    data = await fetch_first_available(session, urls)
    items = extract_items(data)
    new_posts: list[int] = []
    now = int(time.time())

    for position, item in enumerate(items, start=1):
        post = normalize_feed_item(item, position)
        if upsert_post(conn, post, now):
            print(f'[NEW] #{position} {post["id"]} | {post["title"]}')
            new_posts.append(post["id"])

    return new_posts
