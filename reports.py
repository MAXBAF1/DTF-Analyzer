"""Generate daily HTML reports with PNG and SVG charts for DTF Indie metrics."""

from __future__ import annotations

import argparse
import html
import sqlite3
import struct
import zlib
from datetime import UTC, date, datetime
from pathlib import Path

from db import DEFAULT_DB, connect

WEEKDAYS = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"]


def _report_date(value: str | None) -> date:
    return date.fromisoformat(value) if value else datetime.now(UTC).date()


def _month_bounds(report_day: date) -> tuple[int, int]:
    start = datetime(report_day.year, report_day.month, 1, tzinfo=UTC)
    if report_day.month == 12:
        end = datetime(report_day.year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(report_day.year, report_day.month + 1, 1, tzinfo=UTC)
    return int(start.timestamp()), int(end.timestamp())


def heatmap_data(conn: sqlite3.Connection, checkpoint: float, min_posts: int) -> dict[tuple[int, int], sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT
            CAST(strftime('%w', datetime(p.published_at, 'unixepoch')) AS INTEGER) AS weekday,
            CAST(strftime('%H', datetime(p.published_at, 'unixepoch')) AS INTEGER) AS hour,
            COUNT(*) AS posts,
            ROUND(AVG(m.views), 1) AS avg_views,
            ROUND(AVG(m.velocity_5m), 1) AS avg_velocity,
            ROUND(AVG(m.engagement_rate) * 100, 2) AS avg_er_percent
        FROM posts p
        JOIN metrics m ON p.id = m.post_id
        WHERE m.minutes_since_publish = ?
        GROUP BY weekday, hour
        HAVING posts >= ?
        """,
        (checkpoint, min_posts),
    ).fetchall()
    return {(row["weekday"], row["hour"]): row for row in rows}


def growth_curve_data(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            minutes_since_publish AS minute,
            COUNT(*) AS samples,
            ROUND(AVG(views), 1) AS avg_views,
            ROUND(AVG(views_per_minute), 1) AS avg_views_per_minute
        FROM metrics
        GROUP BY minutes_since_publish
        HAVING samples >= 2
        ORDER BY minutes_since_publish
        """
    ).fetchall()


def top_fast_starts(conn: sqlite3.Connection, checkpoint: float, report_day: date, limit: int) -> list[sqlite3.Row]:
    start_ts, end_ts = _month_bounds(report_day)
    return conn.execute(
        """
        SELECT p.id, p.title, p.url, p.published_at, m.views, m.velocity_5m, m.engagement_rate
        FROM posts p
        JOIN metrics m ON p.id = m.post_id
        WHERE m.minutes_since_publish = ? AND p.published_at >= ? AND p.published_at < ?
        ORDER BY m.views DESC, m.velocity_5m DESC
        LIMIT ?
        """,
        (checkpoint, start_ts, end_ts, limit),
    ).fetchall()




def _write_png(path: Path, width: int, height: int, pixels: bytearray) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + pixels[y * width * 3 : (y + 1) * width * 3] for y in range(height))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def _fill_rect(pixels: bytearray, width: int, x: int, y: int, w: int, h: int, color: tuple[int, int, int]) -> None:
    height = len(pixels) // (width * 3)
    r, g, b = color
    for yy in range(max(0, y), min(height, y + h)):
        for xx in range(max(0, x), min(width, x + w)):
            offset = (yy * width + xx) * 3
            pixels[offset : offset + 3] = bytes((r, g, b))


def _draw_line(pixels: bytearray, width: int, start: tuple[int, int], end: tuple[int, int], color: tuple[int, int, int]) -> None:
    x1, y1 = start
    x2, y2 = end
    dx, dy = abs(x2 - x1), -abs(y2 - y1)
    sx = 1 if x1 < x2 else -1
    sy = 1 if y1 < y2 else -1
    err = dx + dy
    while True:
        _fill_rect(pixels, width, x1 - 1, y1 - 1, 3, 3, color)
        if x1 == x2 and y1 == y2:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x1 += sx
        if e2 <= dx:
            err += dx
            y1 += sy


def write_heatmap_png(path: Path, data: dict[tuple[int, int], sqlite3.Row]) -> None:
    cell, left, top = 28, 16, 16
    width, height = left + 24 * cell + 16, top + 7 * cell + 16
    pixels = bytearray([255, 255, 255] * width * height)
    max_views = max((row["avg_views"] for row in data.values()), default=1)
    for weekday in range(7):
        for hour in range(24):
            row = data.get((weekday, hour))
            value = float(row["avg_views"]) if row else 0
            intensity = value / max_views if max_views else 0
            color = (255, int(245 - 130 * intensity), int(255 - 210 * intensity))
            _fill_rect(pixels, width, left + hour * cell, top + weekday * cell, cell - 2, cell - 2, color)
    _write_png(path, width, height, pixels)


def write_growth_png(path: Path, rows: list[sqlite3.Row]) -> None:
    width, height, pad = 760, 320, 42
    pixels = bytearray([255, 255, 255] * width * height)
    _draw_line(pixels, width, (pad, height - pad), (width - pad, height - pad), (170, 170, 170))
    _draw_line(pixels, width, (pad, pad), (pad, height - pad), (170, 170, 170))
    points = [(float(r["minute"]), float(r["avg_views"])) for r in rows]
    max_x = max((p[0] for p in points), default=1)
    max_y = max((p[1] for p in points), default=1)
    rendered = []
    for minute, avg_views in points:
        x = int(pad + (minute / max_x) * (width - pad * 2))
        y = int(height - pad - (avg_views / max_y) * (height - pad * 2))
        rendered.append((x, y))
    for start, end in zip(rendered, rendered[1:]):
        _draw_line(pixels, width, start, end, (255, 90, 0))
    _write_png(path, width, height, pixels)


def render_heatmap_svg(data: dict[tuple[int, int], sqlite3.Row], checkpoint: float) -> str:
    cell, left, top = 34, 44, 30
    width, height = left + 24 * cell + 20, top + 7 * cell + 45
    max_views = max((row["avg_views"] for row in data.values()), default=1)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">']
    parts.append('<style>text{font-family:Arial,sans-serif;font-size:11px}.label{fill:#555}.cellText{fill:#111;text-anchor:middle}</style>')
    parts.append(f'<text x="{left}" y="18" font-size="14">Средние просмотры на +{checkpoint:g} мин по дню и часу</text>')
    for hour in range(24):
        parts.append(f'<text class="label" x="{left + hour * cell + cell / 2}" y="{top - 6}" text-anchor="middle">{hour:02d}</text>')
    for weekday, name in enumerate(WEEKDAYS):
        y = top + weekday * cell
        parts.append(f'<text class="label" x="8" y="{y + 21}">{name}</text>')
        for hour in range(24):
            row = data.get((weekday, hour))
            value = float(row["avg_views"]) if row else 0
            intensity = value / max_views if max_views else 0
            green = int(245 - 130 * intensity)
            blue = int(255 - 210 * intensity)
            color = f'rgb(255,{green},{blue})'
            x = left + hour * cell
            parts.append(f'<rect x="{x}" y="{y}" width="{cell - 2}" height="{cell - 2}" rx="4" fill="{color}"/>')
            if row:
                parts.append(f'<text class="cellText" x="{x + cell / 2}" y="{y + 21}">{int(value)}</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def render_growth_svg(rows: list[sqlite3.Row]) -> str:
    width, height, pad = 760, 320, 42
    points = [(float(r["minute"]), float(r["avg_views"])) for r in rows]
    max_x = max((p[0] for p in points), default=1)
    max_y = max((p[1] for p in points), default=1)
    def xy(point: tuple[float, float]) -> tuple[float, float]:
        x = pad + (point[0] / max_x) * (width - pad * 2)
        y = height - pad - (point[1] / max_y) * (height - pad * 2)
        return x, y
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in map(xy, points))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<style>text{{font-family:Arial,sans-serif;font-size:12px;fill:#555}}</style>
<text x="{pad}" y="20" font-size="14">Средняя кривая роста просмотров</text>
<line x1="{pad}" y1="{height-pad}" x2="{width-pad}" y2="{height-pad}" stroke="#aaa"/>
<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height-pad}" stroke="#aaa"/>
<polyline fill="none" stroke="#ff5a00" stroke-width="3" points="{polyline}"/>
<text x="{width-pad-70}" y="{height-12}">минуты</text>
<text x="8" y="{pad}">просмотры</text>
</svg>'''


def write_daily_report(conn: sqlite3.Connection, output_dir: Path, report_day: date, checkpoint: float, min_posts: int, top_limit: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    heatmap = heatmap_data(conn, checkpoint, min_posts)
    growth = growth_curve_data(conn)
    top = top_fast_starts(conn, 10, report_day, top_limit)
    heatmap_name = f"heatmap-{report_day.isoformat()}.svg"
    growth_name = f"growth-{report_day.isoformat()}.svg"
    heatmap_png_name = f"heatmap-{report_day.isoformat()}.png"
    growth_png_name = f"growth-{report_day.isoformat()}.png"
    (output_dir / heatmap_name).write_text(render_heatmap_svg(heatmap, checkpoint), encoding="utf-8")
    (output_dir / growth_name).write_text(render_growth_svg(growth), encoding="utf-8")
    write_heatmap_png(output_dir / heatmap_png_name, heatmap)
    write_growth_png(output_dir / growth_png_name, growth)

    top_items = []
    for row in top:
        published = datetime.fromtimestamp(row["published_at"], UTC).strftime("%Y-%m-%d %H:%M UTC")
        title = html.escape(row["title"])
        url = html.escape(row["url"] or f'https://dtf.ru/{row["id"]}')
        top_items.append(f'<li><a href="{url}">{title}</a> — {row["views"]} просмотров на +10 мин, {published}</li>')
    body = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>DTF Indie report {report_day}</title></head>
<body>
<h1>Ежедневный отчет DTF Indie за {report_day}</h1>
<p>Цель отчета — найти день и время, когда у читающих максимальная активность: где посты быстрее набирают просмотры и получают лучший старт.</p>
<h2>Heatmap: день × час</h2><img src="{heatmap_png_name}" alt="Heatmap день × час"><p><a href="{heatmap_name}">SVG-версия</a></p>
<h2>Средняя кривая роста</h2><img src="{growth_png_name}" alt="Средняя кривая роста просмотров"><p><a href="{growth_name}">SVG-версия</a></p>
<h2>Топ-{top_limit} самых быстрых стартов месяца</h2>
<ol>{''.join(top_items) or '<li>Недостаточно данных за месяц.</li>'}</ol>
</body></html>"""
    report_path = output_dir / f"daily-report-{report_day.isoformat()}.html"
    report_path.write_text(body, encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate DTF Indie daily HTML report with PNG and SVG charts")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--output-dir", default="reports")
    parser.add_argument("--date", help="Report date in YYYY-MM-DD, defaults to today UTC")
    parser.add_argument("--checkpoint", type=float, default=60, help="Metric checkpoint for heatmap")
    parser.add_argument("--min-posts", type=int, default=2, help="Minimum posts per weekday/hour cell")
    parser.add_argument("--top-limit", type=int, default=10)
    args = parser.parse_args()

    conn = connect(args.db)
    path = write_daily_report(conn, Path(args.output_dir), _report_date(args.date), args.checkpoint, args.min_posts, args.top_limit)
    print(f"Report written to {path}")


if __name__ == "__main__":
    main()
