import asyncio
import aiohttp
import sqlite3
import time
from datetime import datetime, timezone

DB = 'dtf_indie.db'
SUBSITE_ID = 287

CHECKPOINTS = [
    1, 2, 5, 10, 15, 20, 30, 45, 60,
    90, 120, 180, 360, 720, 1440
]

conn = sqlite3.connect(DB)
conn.execute('''
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    title TEXT,
    published_at INTEGER,
    author TEXT
)
''')

conn.execute('''
CREATE TABLE IF NOT EXISTS metrics (
    post_id INTEGER,
    ts INTEGER,
    minutes_since_publish REAL,
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    PRIMARY KEY (post_id, ts)
)
''')
conn.commit()


async def fetch_json(session, url):
    async with session.get(url, timeout=20) as r:
        r.raise_for_status()
        return await r.json()


async def discover_posts(session):
    url = f'https://api.dtf.ru/v2.1/subsite/{SUBSITE_ID}/entries?page=1&count=20'
    data = await fetch_json(session, url)

    new_posts = []

    for item in data['result']['items']:
        post_id = item['id']

        exists = conn.execute(
            'SELECT 1 FROM posts WHERE id=?',
            (post_id,)
        ).fetchone()

        if exists:
            continue

        title = item['title']
        published_at = item['date']

        author = ''
        if item.get('author'):
            author = item['author'].get('name', '')

        conn.execute(
            'INSERT INTO posts VALUES (?, ?, ?, ?)',
            (post_id, title, published_at, author)
        )
        conn.commit()

        print(f'[NEW] {post_id} | {title}')
        new_posts.append(post_id)

    return new_posts


async def fetch_post_metrics(session, post_id):
    url = f'https://api.dtf.ru/v2.1/content/{post_id}'
    data = await fetch_json(session, url)

    r = data['result']

    return {
        'views': r.get('counters', {}).get('views', 0),
        'likes': r.get('counters', {}).get('likes', 0),
        'comments': r.get('comments_count', 0),
    }


async def track_post(session, post_id):
    row = conn.execute(
        'SELECT published_at, title FROM posts WHERE id=?',
        (post_id,)
    ).fetchone()

    published_at, title = row

    print(f'[TRACK] {post_id} | {title}')

    for minute in CHECKPOINTS:
        target_ts = published_at + minute * 60
        wait = target_ts - time.time()

        if wait > 0:
            await asyncio.sleep(wait)

        try:
            m = await fetch_post_metrics(session, post_id)

            now = int(time.time())

            conn.execute(
                'INSERT OR REPLACE INTO metrics VALUES (?, ?, ?, ?, ?, ?)',
                (
                    post_id,
                    now,
                    minute,
                    m['views'],
                    m['likes'],
                    m['comments'],
                )
            )
            conn.commit()

            print(
                f'[{post_id}] +{minute:>4}m | '
                f'{m["views"]:>6} views | '
                f'{m["likes"]:>4} likes | '
                f'{m["comments"]:>3} comments'
            )

        except Exception as e:
            print(f'[ERR] {post_id} +{minute}m: {e}')


async def monitor():
    async with aiohttp.ClientSession(
        headers={
            'User-Agent': 'AroundAnalytics/1.0'
        }
    ) as session:

        active_tasks = {}

        while True:
            try:
                new_posts = await discover_posts(session)

                for post_id in new_posts:
                    active_tasks[post_id] = asyncio.create_task(
                        track_post(session, post_id)
                    )

            except Exception as e:
                print('[DISCOVER ERR]', e)

            await asyncio.sleep(15)


if __name__ == '__main__':
    asyncio.run(monitor())