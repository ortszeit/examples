#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "markdown-it-py>=3.0",
#     "pyyaml>=6.0",
#     "pillow>=10.0",
# ]
# ///
"""Turn an Ortszeit export into a static blog you can host anywhere.

    uv run ortszeit_ssg.py EXPORT_DIR_OR_ZIP -o OUTDIR [--base-url URL] [--lang de|en] [--serve]

This is a teaching example, not a product: one file, three dependencies, no
build step. It reads the portable format (schema version 1, documented at
https://ortszeit.app/docs/portable-format/) and writes plain HTML, feeds and a
sitemap. The point it makes is that the archive is enough — nothing here talks
to Ortszeit, and nothing on the generated pages talks to anyone at all.

The script is laid out in the order the work happens:

1. read the archive (blog.yaml, trip.yaml, posts as markdown + front matter)
2. decide what may be published (drafts, unlisted posts, password trips)
3. format the meta line in the post's own Ortszeit
4. resize photos into web derivatives (which also strips EXIF/GPS)
5. render HTML pages
6. write feeds (RSS, Atom, JSON Feed 1.1), sitemap and robots.txt
7. the command line, and an optional local preview server
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.utils import format_datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from markdown_it import MarkdownIt
from PIL import Image, ImageOps

SUPPORTED_SCHEMA = 1  # ORTSZEIT_SCHEMA_VERSION this reader understands
INDEX_LIMIT = 25  # posts on the start page, like the hosted blog
FEED_LIMIT = 20
WIDTHS = (400, 800, 1600)  # derivative widths, spec §5
FORMAT_DOCS = "https://ortszeit.app/docs/portable-format/"
MARKER = ".ortszeit-ssg"  # marks an output directory as safe to replace


class ExportError(Exception):
    """Something is wrong with the input; shown to the user without a traceback."""


# ---------------------------------------------------------------------------
# 1. Reading the archive
# ---------------------------------------------------------------------------


@dataclass
class Photo:
    source: Path | None  # None when the export carried no binary (free tier)
    alt: str
    taken_at: datetime | None
    # Filled in by make_photos(): [(width, url)], plus the original's URL if copied.
    renditions: list[tuple[int, str]] = field(default_factory=list)
    size: tuple[int, int] | None = None
    original_url: str | None = None


@dataclass
class Trip:
    slug: str
    title: str
    started_on: date | None
    ended_on: date | None
    visibility: str


@dataclass
class Post:
    slug: str
    trip: Trip | None
    title: str | None
    date: datetime
    time_zone: str | None
    draft: bool
    place: dict | None
    hide_location: bool
    visibility: str
    body: str
    photos: list[Photo]

    @property
    def url(self) -> str:
        """Same paths as the hosted blog, so links survive a move."""
        return f"/{self.trip.slug}/{self.slug}/" if self.trip else f"/posts/{self.slug}/"


@dataclass
class Blog:
    title: str
    bio: str
    custom_domain: str | None
    custom_css: str
    location: str  # hidden | place | coordinates
    show_time: bool
    imprint: str
    trips: list[Trip]
    posts: list[Post]


def find_root(path: Path, workdir: Path) -> Path:
    """Accept a zip, the extracted ``blog/`` directory, or its parent."""
    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():  # refuse paths that escape the target
                if name.startswith("/") or ".." in Path(name).parts:
                    raise ExportError(f"unsafe path in zip: {name}")
            archive.extractall(workdir)
        path = workdir
    for candidate in (path, path / "blog"):
        if (candidate / "blog.yaml").is_file():
            return candidate
    raise ExportError(f"no blog.yaml found in {path} (expected an Ortszeit export)")


def as_datetime(value) -> datetime | None:
    """YAML gives us real datetimes; tolerate ISO strings too (posts.json style)."""
    if value is None or isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value))
    if result is not None and result.tzinfo is None:
        result = result.replace(tzinfo=UTC)  # the format promises aware values
    return result


def as_date(value) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def read_post(path: Path, trip: Trip | None) -> Post:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ExportError(f"{path}: missing front matter")
    front, _, body = text[4:].partition("\n---\n")
    meta = yaml.safe_load(front) or {}
    if "date" not in meta:
        raise ExportError(f"{path}: front matter has no date")
    photos = []
    for item in meta.get("media") or []:
        source = path.parent / item.get("file", "")
        photos.append(
            Photo(
                source=source if source.is_file() else None,
                alt=item.get("alt") or "",
                taken_at=as_datetime(item.get("taken_at")),
            )
        )
    return Post(
        # Filenames are YYYY-MM-DD-<slug>.md; the slug is what follows the date.
        slug=re.sub(r"^\d{4}-\d{2}-\d{2}-", "", path.stem),
        trip=trip,
        title=meta.get("title") or None,
        date=as_datetime(meta["date"]),
        time_zone=meta.get("time_zone") or None,  # absent in older archives
        draft=bool(meta.get("draft", False)),
        place=meta.get("place"),  # absent when there is no place
        hide_location=bool(meta.get("hide_location", False)),
        visibility=meta.get("visibility", "public"),
        body=body.strip(),
        photos=photos,
    )


def read_blog(root: Path) -> Blog:
    meta = read_yaml(root / "blog.yaml")
    schema = meta.get("ortszeit_schema")
    if schema != SUPPORTED_SCHEMA:
        raise ExportError(
            f"this archive has ortszeit_schema {schema!r}; this generator reads version "
            f"{SUPPORTED_SCHEMA}. See {FORMAT_DOCS} for the versions and their changes."
        )
    display = meta.get("display") or {}  # absent in older archives
    trips, posts = [], []
    for trip_file in sorted((root / "trips").glob("*/trip.yaml")):
        t = read_yaml(trip_file)
        trip = Trip(
            slug=t.get("slug") or trip_file.parent.name,
            title=t.get("title") or trip_file.parent.name,
            started_on=as_date(t.get("started_on")),
            ended_on=as_date(t.get("ended_on")),
            visibility=t.get("visibility", "public"),
        )
        trips.append(trip)
        posts += [read_post(p, trip) for p in sorted(trip_file.parent.glob("*.md"))]
    posts += [read_post(p, None) for p in sorted((root / "posts").glob("*.md"))]
    return Blog(
        title=meta.get("title") or "Blog",
        bio=meta.get("bio") or "",
        custom_domain=meta.get("custom_domain") or None,
        custom_css=meta.get("custom_css") or "",
        location=display.get("location", "place"),
        show_time=bool(display.get("time", True)),
        imprint=meta.get("imprint") or "",
        trips=trips,
        posts=posts,
    )


# ---------------------------------------------------------------------------
# 2. What may be published
# ---------------------------------------------------------------------------
#
# The archive is complete on purpose: drafts, unlisted posts and password
# trips are all in it, because it is the author's backup. A *public site* must
# be more careful. The rules mirror the hosted blog:
#
# * drafts are never published;
# * ``unlisted`` posts get a page (reachable by URL, ``noindex``) but appear on
#   no list, feed or sitemap;
# * ``password`` posts/trips are left out entirely. The password is never in
#   the export, so a static copy could not protect them. ``--include-private``
#   publishes them anyway, treated as unlisted.


def publish_plan(blog: Blog, include_private: bool) -> tuple[list[Post], list[Post], list[Trip]]:
    """Return (pages to build, posts to list, trips to build)."""
    pages = []
    for post in blog.posts:
        if post.draft:
            continue
        protected = post.visibility == "password" or (post.trip and post.trip.visibility == "password")
        if protected and not include_private:
            continue
        pages.append(post)
    listed = [p for p in pages if p.visibility == "public" and (not p.trip or p.trip.visibility == "public")]
    trips = [t for t in blog.trips if t.visibility != "password" or include_private]
    pages.sort(key=lambda p: p.date, reverse=True)
    listed.sort(key=lambda p: p.date, reverse=True)
    return pages, listed, trips


# ---------------------------------------------------------------------------
# 3. Ortszeit: the date and time as they were *there*
# ---------------------------------------------------------------------------

STRINGS = {
    "en": {
        "archive": "Archive",
        "trips": "Trips",
        "feed": "Feed",
        "imprint": "Imprint",
        "ongoing": "ongoing",
        "post": "post",
        "posts": "posts",
        "every_post": "Every post",
        "nothing": "Nothing published here yet.",
        "place_names": "Place names",
        "built_with": "Built with the Ortszeit example generator from a",
        "archive_link": "portable archive",
        "not_found": "This page does not exist.",
        "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        "months_long": [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
    },
    "de": {
        "archive": "Archiv",
        "trips": "Reisen",
        "feed": "Feed",
        "imprint": "Impressum",
        "ongoing": "läuft noch",
        "post": "Beitrag",
        "posts": "Beiträge",
        "every_post": "Alle Beiträge",
        "nothing": "Hier ist noch nichts veröffentlicht.",
        "place_names": "Ortsnamen",
        "built_with": "Erstellt mit dem Ortszeit-Beispielgenerator aus einem",
        "archive_link": "portablen Archiv",
        "not_found": "Diese Seite gibt es nicht.",
        "months": ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sep.", "Okt.", "Nov.", "Dez."],
        "months_long": [
            "Januar",
            "Februar",
            "März",
            "April",
            "Mai",
            "Juni",
            "Juli",
            "August",
            "September",
            "Oktober",
            "November",
            "Dezember",
        ],
    },
}


def local_time(post: Post) -> datetime:
    """The post's instant on the wall clock where it was written.

    ``date`` already carries the local offset when the zone was known, but we
    convert explicitly: the zone is the truth, the offset is a convenience.
    Without a zone we keep the value as written (UTC) and show no clock time —
    an honest date beats an invented hour.
    """
    if post.time_zone:
        try:
            return post.date.astimezone(ZoneInfo(post.time_zone))
        except ZoneInfoNotFoundError:
            pass
    return post.date


def shows_clock(post: Post, blog: Blog) -> bool:
    return bool(post.time_zone) and blog.show_time


def shows_place(post: Post, blog: Blog) -> bool:
    return post.place is not None and not post.hide_location and blog.location != "hidden"


def shows_coordinates(post: Post, blog: Blog) -> bool:
    return shows_place(post, blog) and blog.location == "coordinates"


def day_month(value: datetime, s: dict) -> str:
    sep = ". " if s is STRINGS["de"] else " "
    return f"{value.day}{sep}{s['months'][value.month - 1]}"


def long_date(value: date, s: dict) -> str:
    sep = ". " if s is STRINGS["de"] else " "
    return f"{value.day}{sep}{s['months_long'][value.month - 1]} {value.year}"


def post_label(post: Post, s: dict) -> str:
    """Titles are optional; lists and feeds fall back to the local date."""
    return post.title or long_date(local_time(post), s)


def coordinates(place: dict) -> str:
    lat, lon = float(place["lat"]), float(place["lon"])
    return f"{abs(lat):.3f}{'N' if lat >= 0 else 'S'} {abs(lon):.3f}{'E' if lon >= 0 else 'W'}"


# ---------------------------------------------------------------------------
# 4. Photos
# ---------------------------------------------------------------------------


def make_photos(post: Post, blog: Blog, out: Path, root: str, include_originals: bool) -> None:
    """Write 400/800/1600 px JPEG derivatives next to the post's page.

    Re-encoding with Pillow drops EXIF — including GPS — which matters: an
    original photo can reveal a location the author chose to hide. That is
    also why ``--include-originals`` copies originals only for posts whose
    coordinates the blog shows anyway.
    """
    target = out / post.url.strip("/")
    for photo in post.photos:
        if photo.source is None:
            continue  # free exports list media but carry no binaries
        try:
            with Image.open(photo.source) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")
                photo.size = image.size
                widths = sorted({w for w in WIDTHS if w < image.width} | {min(image.width, WIDTHS[-1])})
                for width in widths:
                    name = f"{photo.source.stem}-{width}.jpg"
                    height = round(image.height * width / image.width)
                    target.mkdir(parents=True, exist_ok=True)
                    image.resize((width, height), Image.LANCZOS).save(target / name, "JPEG", quality=82)
                    photo.renditions.append((width, f"{root}{post.url}{name}"))
        except OSError as exc:  # e.g. HEIC without a plugin, or a broken file
            print(f"  skipped {photo.source.name}: {exc}", file=sys.stderr)
            continue
        if include_originals and shows_coordinates(post, blog):
            (target / "original").mkdir(exist_ok=True)
            shutil.copy2(photo.source, target / "original" / photo.source.name)
            photo.original_url = f"{root}{post.url}original/{photo.source.name}"


# ---------------------------------------------------------------------------
# 5. HTML
# ---------------------------------------------------------------------------
#
# Templates are plain f-strings; ``e()`` escapes everything that came from the
# archive. The CSS is the hosted theme's (spec §5, palette "Koralle & Ozean"),
# trimmed to what static pages need: no lightbox script, no map. Coral
# (--accent) is for the route dots only; links and focus use --accent-strong
# #CA4224, which reaches WCAG AA on the paper (4.63:1) — coral itself does not.

e = html.escape

CSS = """*,*::before,*::after{box-sizing:border-box}
:root{--paper:#FFF8F3;--ink:#16181C;--muted:#69625D;--rule:#F2E2D8;--ocean:#1F5673;
--accent:#FF6B4A;--accent-strong:#CA4224;--accent-dim:#FFE5DA;
--font-body:Charter,"Bitstream Charter","Iowan Old Style",Georgia,serif;
--font-meta:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;--measure:34rem}
@media (prefers-color-scheme:dark){:root{--paper:#101B22;--ink:#ECE7E3;--muted:#9BA8B0;--rule:#1E2E38;--ocean:#7DB8D9;
--accent:#FF7E61;--accent-strong:#FF7E61;--accent-dim:#3A2621}}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--font-body);font-size:18px;line-height:1.65}
a{color:var(--accent-strong);text-decoration:none;border-bottom:1px solid var(--accent-dim)}
a:hover,a:focus-visible{border-bottom-color:var(--accent-strong)}
img{max-width:100%;height:auto;display:block}
.wrap{max-width:var(--measure);margin:0 auto;padding:0 1.25rem}
.meta{font-family:var(--font-meta);font-size:13px;letter-spacing:.02em;line-height:1.5;color:var(--muted);
text-transform:uppercase;margin:0}
.meta a{color:var(--muted);border-bottom-color:var(--rule)}.meta a:hover{color:var(--accent-strong)}
.site{padding:3rem 0 1.25rem}.site h1{font-size:1.35rem;line-height:1.3;margin:0;font-weight:600}
.site h1 a{color:var(--ink);border:0}.bio{color:var(--muted);font-size:1rem;margin:.5rem 0 0}
.site nav{margin-top:1.25rem}.site nav a{margin-right:1.25rem;border:0}
main{padding-bottom:4rem}
.post{padding:2.25rem 0;border-top:1px solid var(--rule)}.post:first-child{border-top:0;padding-top:.5rem}
.post h1,.post h2{font-size:1.25rem;line-height:1.35;margin:.6rem 0 0;font-weight:600}
.post h1 a,.post h2 a{color:var(--ink);border:0}
.body{margin-top:.85rem}.body>:first-child{margin-top:0}.body>:last-child{margin-bottom:0}
.body p{margin:0 0 1rem}.body ul,.body ol{margin:0 0 1rem;padding-left:1.25rem}
.body blockquote{margin:1.25rem 0;padding-left:1rem;border-left:2px solid var(--rule);color:var(--muted)}
.photos{margin:1.25rem 0 0}.photos.solo img{width:100%;border-radius:2px}
.strip{display:flex;gap:.5rem;overflow-x:auto;scroll-snap-type:x mandatory;padding-bottom:.5rem}
.strip a{flex:0 0 auto;border:0;line-height:0;scroll-snap-align:start}
.strip img{width:9rem;height:9rem;object-fit:cover;border-radius:2px}
.route-wrap{max-width:calc(var(--measure) + 1.5rem)}
.route{border-left:1px solid var(--rule);padding-left:1.5rem}
.route .post{position:relative;border-top:0;padding:1.75rem 0}
.route .post::before{content:"";position:absolute;left:calc(-1.5rem - 4px);top:2rem;width:7px;height:7px;
border-radius:50%;background:var(--accent);box-shadow:0 0 0 3px var(--paper)}
.route .post:first-child{padding-top:.75rem}.route .post:first-child::before{top:1rem}
.stack{list-style:none;margin:0 0 2rem;padding:0}
.stack li{padding:.35rem 0;display:flex;gap:.75rem;align-items:baseline}
.stack .when{flex:0 0 8.5rem;white-space:nowrap}.year{margin:2rem 0 .5rem;color:var(--ink)}
.site-footer{border-top:1px solid var(--rule);padding:1.5rem 0 3rem}.site-footer p+p{margin-top:.35rem}
@media (max-width:26rem){.strip img{width:7.5rem;height:7.5rem}}"""


def safe_css(css: str) -> str:
    """The author's custom CSS, minus anything that would fetch from elsewhere."""
    css = re.sub(r"@import[^;]*;?", "", css, flags=re.IGNORECASE)
    css = re.sub(r"url\(\s*['\"]?\s*(https?:)?//[^)]*\)", "none", css, flags=re.IGNORECASE)
    return css.replace("</", "<\\/")


@dataclass
class Context:
    """Everything the templates need besides the post itself."""

    blog: Blog
    s: dict
    lang: str
    root: str  # path prefix of --base-url, '' for a site at the domain root
    base_url: str  # absolute, no trailing slash
    markdown: MarkdownIt
    geonames_credit: bool


def page(ctx: Context, title: str, content: str, path: str, *, noindex=False, wrap="wrap") -> str:
    b, s, r = ctx.blog, ctx.s, ctx.root
    nav = f'<a href="{r}/archive/">{s["archive"]}</a><a href="{r}/feed.xml">{s["feed"]}</a>'
    footer = f'<a href="{r}/impressum/">{s["imprint"]}</a> · ' if b.imprint.strip() else ""
    footer += f'{s["built_with"]} <a href="{FORMAT_DOCS}">{s["archive_link"]}</a>'
    credit = (
        f'<p class="meta">{s["place_names"]}: <a href="https://www.geonames.org/">GeoNames</a> '
        '(<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>)</p>'
        if ctx.geonames_credit
        else ""
    )
    custom = f"<style>{safe_css(b.custom_css)}</style>" if b.custom_css.strip() else ""
    return f"""<!DOCTYPE html>
<html lang="{ctx.lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
{'<meta name="robots" content="noindex">' if noindex else f'<link rel="canonical" href="{ctx.base_url}{path}">'}
<link rel="alternate" type="application/rss+xml" title="{e(b.title)}" href="{r}/feed.xml">
<link rel="alternate" type="application/atom+xml" title="{e(b.title)}" href="{r}/atom.xml">
<link rel="alternate" type="application/feed+json" title="{e(b.title)}" href="{r}/feed.json">
<style>{CSS}</style>{custom}
</head>
<body>
<div class="{wrap}">
<header class="site"><h1><a href="{r}/">{e(b.title)}</a></h1>
{f'<p class="bio">{e(b.bio)}</p>' if b.bio else ""}<nav class="meta">{nav}</nav></header>
<main>
{content}
</main>
<footer class="site-footer"><p class="meta">{footer}</p>{credit}</footer>
</div>
</body>
</html>
"""


def meta_line(post: Post, ctx: Context, linked: bool) -> str:
    """The theme's signature device: PLACE · DATE · TIME · COORDINATES (§5).

    Only what exists *and* what the blog chose to show is rendered. The
    ``datetime`` attribute carries the hour only when the hour is shown.
    """
    when = local_time(post)
    clock = shows_clock(post, ctx.blog)
    label = day_month(when, ctx.s) + (f" · {when:%H:%M}" if clock else "")
    stamp = when.isoformat() if clock else when.date().isoformat()
    time = f'<time datetime="{stamp}">{label}</time>'
    if linked:
        time = f'<a href="{ctx.root}{post.url}">{time}</a>'
    parts = [time]
    if shows_place(post, ctx.blog):
        parts.insert(0, f'<span class="place">{e(post.place.get("name", ""))}</span>')
    if shows_coordinates(post, ctx.blog) and "lat" in post.place and "lon" in post.place:
        parts.append(coordinates(post.place))
    return f'<p class="meta">{" · ".join(parts)}</p>'


def photos_html(post: Post) -> str:
    """One photo: full measure. Two or more: a strip of square thumbnails (§5).

    The hosted theme opens a small JS lightbox; here a thumbnail simply links
    to the large rendition, so the page needs no JavaScript at all.
    """
    shown = [p for p in post.photos if p.renditions]
    if not shown:
        return ""

    def img(photo: Photo, sizes: str) -> str:
        srcset = ", ".join(f"{url} {w}w" for w, url in photo.renditions)
        src = next((url for w, url in photo.renditions if w >= 800), photo.renditions[-1][1])
        dims = f' width="{photo.size[0]}" height="{photo.size[1]}"' if photo.size and len(shown) == 1 else ""
        return (
            f'<img src="{src}" srcset="{srcset}" sizes="{sizes}" alt="{e(photo.alt)}"{dims} '
            'loading="lazy" decoding="async">'
        )

    if len(shown) == 1:
        photo = shown[0]
        tag = img(photo, "(max-width: 36rem) 100vw, 34rem")
        if photo.original_url:
            tag = f'<a href="{photo.original_url}" style="border:0">{tag}</a>'
        return f'<div class="photos solo">{tag}</div>'
    links = "".join(f'<a href="{p.original_url or p.renditions[-1][1]}">{img(p, "9rem")}</a>' for p in shown)
    return f'<div class="photos strip">{links}</div>'


def post_unit(post: Post, ctx: Context, *, detail: bool = False) -> str:
    if post.title:
        heading = (
            f"<h1>{e(post.title)}</h1>" if detail else f'<h2><a href="{ctx.root}{post.url}">{e(post.title)}</a></h2>'
        )
    else:
        heading = ""
    body = ctx.markdown.render(post.body)
    return (
        f'<article class="post">{meta_line(post, ctx, linked=not detail)}{heading}'
        f'<div class="body">{body}</div>{photos_html(post)}</article>'
    )


def trip_range(trip: Trip, count: int, s: dict) -> str:
    noun = s["post"] if count == 1 else s["posts"]
    if not trip.started_on:
        return f"{count} {noun}"
    end = long_date(trip.ended_on, s) if trip.ended_on else s["ongoing"]
    return f"{long_date(trip.started_on, s)} – {end} · {count} {noun}"


def archive_html(listed: list[Post], trips: list[Trip], ctx: Context) -> str:
    s, r = ctx.s, ctx.root
    out = [f'<h2 class="meta">{s["archive"]}</h2>']
    if trips:
        out.append(f'<h3 class="meta year">{s["trips"]}</h3><ul class="stack">')
        for trip in trips:
            start = str(trip.started_on.year) if trip.started_on else ""
            out.append(f'<li><span class="meta when">{start}</span><a href="{r}/{trip.slug}/">{e(trip.title)}</a></li>')
        out.append("</ul>")
    year = None
    for post in listed:
        when = local_time(post)
        if when.year != year:
            out.append(("</ul>" if year else "") + f'<h3 class="meta year">{when.year}</h3><ul class="stack">')
            year = when.year
        label = day_month(when, s) + (f" · {when:%H:%M}" if shows_clock(post, ctx.blog) else "")
        out.append(
            f'<li><span class="meta when">{label}</span><a href="{r}{post.url}">{e(post_label(post, s))}</a></li>'
        )
    if year:
        out.append("</ul>")
    if not listed and not trips:
        out.append(f'<p class="meta">{s["nothing"]}</p>')
    return "".join(out)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# 6. Feeds, sitemap, robots.txt
# ---------------------------------------------------------------------------


def x(text: str) -> str:
    """Escape for XML text and attributes."""
    return html.escape(text, quote=True)


def write_feeds(listed: list[Post], ctx: Context, out: Path) -> None:
    b, base = ctx.blog, ctx.base_url
    items = listed[:FEED_LIMIT]
    # Feed dates keep the local offset: the same instant, readable as it was there.
    updated = max((local_time(p) for p in items), default=datetime.now(UTC)).isoformat()

    rss = [
        '<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        f"<channel><title>{x(b.title)}</title><link>{base}/</link><description>{x(b.bio or b.title)}</description>",
        f'<language>{ctx.lang}</language><atom:link href="{base}/feed.xml" rel="self" type="application/rss+xml"/>',
    ]
    atom = [
        '<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">',
        f"<title>{x(b.title)}</title><id>{base}/</id><updated>{updated}</updated>",
        f'<link href="{base}/"/><link rel="self" href="{base}/atom.xml"/><author><name>{x(b.title)}</name></author>',
    ]
    json_items = []
    for post in items:
        url, when = f"{base}{post.url}", local_time(post)
        content = ctx.markdown.render(post.body)
        title = post_label(post, ctx.s)
        rss.append(
            f'<item><title>{x(title)}</title><link>{url}</link><guid isPermaLink="true">{url}</guid>'
            f"<pubDate>{format_datetime(when)}</pubDate><description>{x(content)}</description></item>"
        )
        atom.append(
            f'<entry><title>{x(title)}</title><link href="{url}"/><id>{url}</id>'
            f"<published>{when.isoformat()}</published><updated>{when.isoformat()}</updated>"
            f'<content type="html">{x(content)}</content></entry>'
        )
        item = {"id": url, "url": url, "content_html": content, "date_published": when.isoformat()}
        if post.title:
            item["title"] = post.title
        photo = next((p for p in post.photos if p.renditions), None)
        if photo:
            item["image"] = base + photo.renditions[-1][1][len(ctx.root) :]
        if shows_place(post, b):
            item["tags"] = [post.place.get("name", "")]
        json_items.append(item)
    write(out / "feed.xml", "".join(rss) + "</channel></rss>\n")
    write(out / "atom.xml", "".join(atom) + "</feed>\n")
    feed = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": b.title,
        "home_page_url": f"{base}/",
        "feed_url": f"{base}/feed.json",
        "language": ctx.lang,
        "items": json_items,
    }
    if b.bio:
        feed["description"] = b.bio
    write(out / "feed.json", json.dumps(feed, ensure_ascii=False, indent=2) + "\n")


def write_sitemap(paths: list[str], ctx: Context, out: Path) -> None:
    urls = "".join(f"<url><loc>{x(ctx.base_url + p)}</loc></url>" for p in paths)
    write(
        out / "sitemap.xml",
        f'<?xml version="1.0" encoding="utf-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n',
    )
    write(out / "robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {ctx.base_url}/sitemap.xml\n")


# ---------------------------------------------------------------------------
# 7. Putting it together
# ---------------------------------------------------------------------------


def build(
    source: Path, out: Path, *, base_url: str | None, lang: str, include_private: bool, include_originals: bool
) -> int:
    with tempfile.TemporaryDirectory() as workdir:
        blog = read_blog(find_root(source, Path(workdir)))
        pages, listed, trips = publish_plan(blog, include_private)
        if not base_url:
            base_url = f"https://{blog.custom_domain}" if blog.custom_domain else "http://localhost:8000"
            print(f"no --base-url given; feeds and sitemap use {base_url}", file=sys.stderr)
        base_url = base_url.rstrip("/")
        # The markdown subset Ortszeit allows: emphasis, links, lists, quotes.
        # html=False means raw HTML in a post is shown as text, never executed.
        md = MarkdownIt("zero", {"html": False, "linkify": False}).enable(
            ["emphasis", "link", "list", "blockquote", "newline"]
        )
        ctx = Context(
            blog=blog,
            s=STRINGS[lang],
            lang=lang,
            root=urlsplit(base_url).path.rstrip("/"),
            base_url=base_url,
            markdown=md,
            geonames_credit=any(shows_place(p, blog) and "geonames" in p.place for p in pages),
        )
        # A clean build, so nothing unpublished lingers from a previous run. We
        # only ever delete a directory we created ourselves (it has MARKER).
        if out.exists() and any(out.iterdir()):
            if not (out / MARKER).is_file():
                raise ExportError(f"{out} is not empty and was not built by this script; pick another -o")
            shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)
        (out / MARKER).write_text("built by ortszeit_ssg.py; this directory is replaced on every build\n")

        for post in pages:
            make_photos(post, blog, out, ctx.root, include_originals)

        # Start page: newest listed posts.
        content = "".join(post_unit(p, ctx) for p in listed[:INDEX_LIMIT]) or f'<p class="meta">{ctx.s["nothing"]}</p>'
        if len(listed) > INDEX_LIMIT:
            content += f'<p class="meta"><a href="{ctx.root}/archive/">{ctx.s["every_post"]} →</a></p>'
        write(out / "index.html", page(ctx, blog.title, content, "/"))
        sitemap = ["/", "/archive/"]

        # Post pages. Unlisted (and, with --include-private, password) posts
        # get a page but are marked noindex and kept off every list.
        for post in pages:
            content = post_unit(post, ctx, detail=True)
            if post.trip:
                content += f'<p class="meta"><a href="{ctx.root}/{post.trip.slug}/">← {e(post.trip.title)}</a></p>'
            title = f"{post.title} · {blog.title}" if post.title else blog.title
            write(
                out / post.url.strip("/") / "index.html",
                page(ctx, title, content, post.url, noindex=post not in listed),
            )
            if post in listed:
                sitemap.append(post.url)

        # Trip pages: oldest first, along the route rule (§5's signature element).
        for trip in trips:
            on_trip = sorted((p for p in pages if p.trip is trip), key=lambda p: p.date)
            if trip.visibility != "public":
                on_trip_listed = on_trip  # an unlisted trip lists its own posts, like the hosted one
            else:
                on_trip_listed = [p for p in on_trip if p in listed]
            units = "".join(post_unit(p, ctx) for p in on_trip_listed) or f'<p class="meta">{ctx.s["nothing"]}</p>'
            content = (
                f'<h2>{e(trip.title)}</h2><p class="meta">{trip_range(trip, len(on_trip_listed), ctx.s)}</p>'
                f'<div class="route">{units}</div>'
            )
            public = trip.visibility == "public"
            write(
                out / trip.slug / "index.html",
                page(
                    ctx,
                    f"{trip.title} · {blog.title}",
                    content,
                    f"/{trip.slug}/",
                    noindex=not public,
                    wrap="wrap route-wrap",
                ),
            )
            if public:
                sitemap.append(f"/{trip.slug}/")

        public_trips = [t for t in trips if t.visibility == "public"]
        write(
            out / "archive" / "index.html",
            page(ctx, f"{ctx.s['archive']} · {blog.title}", archive_html(listed, public_trips, ctx), "/archive/"),
        )

        if blog.imprint.strip():
            content = f'<article class="post"><h1>{ctx.s["imprint"]}</h1><div class="body">{md.render(blog.imprint)}</div></article>'
            write(
                out / "impressum" / "index.html",
                page(ctx, f"{ctx.s['imprint']} · {blog.title}", content, "/impressum/"),
            )
            sitemap.append("/impressum/")

        # GitHub Pages, Netlify and Cloudflare Pages all serve /404.html for misses.
        write(
            out / "404.html",
            page(ctx, blog.title, f'<p class="meta">{ctx.s["not_found"]}</p>', "/404.html", noindex=True),
        )
        write_feeds(listed, ctx, out)
        write_sitemap(sitemap, ctx, out)

    skipped = len([p for p in blog.posts if not p.draft]) - len(pages)
    drafts = len([p for p in blog.posts if p.draft])
    print(
        f"built {out}: posts {len(pages)}, trips {len(trips)}"
        f" (left out: drafts {drafts}, password-protected posts {skipped})"
    )
    return 0


def serve(out: Path, port: int) -> None:
    """A preview server. Fine for looking; use a real web server for hosting."""

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(out), **kwargs)

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as httpd:
        print(f"serving {out} at http://127.0.0.1:{port}/ (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a static blog from an Ortszeit export.")
    parser.add_argument("source", type=Path, help="export zip, or the extracted blog/ directory")
    parser.add_argument("-o", "--output", type=Path, required=True, help="output directory (replaced)")
    parser.add_argument("--base-url", help="public URL of the site, e.g. https://example.org/blog")
    parser.add_argument("--lang", choices=sorted(STRINGS), default="en", help="language of the page chrome")
    parser.add_argument(
        "--include-private",
        action="store_true",
        help="also publish password-protected trips/posts (as unlisted; no protection!)",
    )
    parser.add_argument(
        "--include-originals",
        action="store_true",
        help="copy original photos (with EXIF) for posts that show coordinates",
    )
    parser.add_argument("--serve", action="store_true", help="preview the result on a local server")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    try:
        build(
            args.source,
            args.output,
            base_url=args.base_url or (f"http://localhost:{args.port}" if args.serve else None),
            lang=args.lang,
            include_private=args.include_private,
            include_originals=args.include_originals,
        )
    except ExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.serve:
        serve(args.output, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
