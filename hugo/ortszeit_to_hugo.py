#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pyyaml>=6.0",
#     "pillow>=10.0",
# ]
# ///
"""Turn an Ortszeit export into a Hugo project you keep developing yourself.

    uv run ortszeit_to_hugo.py EXPORT_DIR_OR_ZIP -o MYSITE [--lang de|en] [--base-url URL] [--include-private]

The result is a complete Hugo site: ``hugo.toml``, the ``ortszeit`` theme in
``themes/``, one page bundle per post (the post's markdown next to its
photos), a section per trip, the archive and the imprint. ``cd MYSITE && hugo
server`` shows it; nothing in it depends on this repository afterwards.

The script is laid out in the order the work happens:

1. read the archive (blog.yaml, trip.yaml, posts as markdown + front matter)
2. decide what is imported (drafts stay drafts, password trips stay out)
3. clean the photos (re-encoded without EXIF, so no GPS reaches the project)
4. write Hugo content: front matter in Hugo's terms, page bundles
5. sync into the target without touching what you changed (the manifest)
6. the command line
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from PIL import Image, ImageOps

SUPPORTED_SCHEMA = 1  # ORTSZEIT_SCHEMA_VERSION this reader understands
FORMAT_DOCS = "https://ortszeit.app/docs/portable-format/"
THEME = Path(__file__).resolve().parent / "themes" / "ortszeit"
MANIFEST = ".ortszeit-import.json"  # what this script wrote, with checksums
SLUG = re.compile(r"[\w-]+")  # a slug is one path segment, never ../ or /
JPEG_QUALITY = 92  # the cleaned copy is the bundle's "original": keep it good
STAMP = (
    "# Imported from Ortszeit by ortszeit_to_hugo.py. Edit freely: once you\n"
    "# change this file, a later import leaves it alone.\n"
)
TITLES = {
    "en": {"archive": "Archive", "imprint": "Imprint", "posts": "Posts"},
    "de": {"archive": "Archiv", "imprint": "Impressum", "posts": "Beiträge"},
}


class ExportError(Exception):
    """Something is wrong with the input; shown to the user without a traceback."""


# ---------------------------------------------------------------------------
# 1. Reading the archive
# ---------------------------------------------------------------------------


@dataclass
class Photo:
    source: Path | None  # None when the export carried no binary (free tier)
    alt: str | None
    taken_at: datetime | None


@dataclass
class Trip:
    slug: str
    title: str
    started_on: date | None
    ended_on: date | None
    visibility: str
    co_authors: list[str]


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


def checked_slug(slug: str, where: Path) -> str:
    if not SLUG.fullmatch(slug):
        raise ExportError(f"{where}: unusable slug {slug!r}")
    return slug


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
        source = (path.parent / str(item.get("file", ""))).resolve()
        inside = source.is_relative_to(path.parent.resolve())
        photos.append(
            Photo(
                source=source if inside and source.is_file() else None,
                alt=item.get("alt") or None,
                taken_at=as_datetime(item.get("taken_at")),
            )
        )
    return Post(
        # Filenames are YYYY-MM-DD-<slug>.md; the slug is what follows the date.
        slug=checked_slug(re.sub(r"^\d{4}-\d{2}-\d{2}-", "", path.stem), path),
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
            f"this archive has ortszeit_schema {schema!r}; this importer reads version "
            f"{SUPPORTED_SCHEMA}. See {FORMAT_DOCS} for the versions and their changes."
        )
    display = meta.get("display") or {}  # absent in older archives
    trips, posts = [], []
    for trip_file in sorted((root / "trips").glob("*/trip.yaml")):
        t = read_yaml(trip_file)
        trip = Trip(
            slug=checked_slug(str(t.get("slug") or trip_file.parent.name), trip_file),
            title=t.get("title") or trip_file.parent.name,
            started_on=as_date(t.get("started_on")),
            ended_on=as_date(t.get("ended_on")),
            visibility=t.get("visibility", "public"),
            co_authors=list(t.get("co_authors") or []),
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
# 2. What is imported
# ---------------------------------------------------------------------------
#
# A Hugo project is a place to keep writing, so the rules differ slightly
# from a finished site (compare ../ssg/):
#
# * drafts are imported with ``draft: true``. Hugo builds them only with
#   ``hugo server -D``, never into public/ by default;
# * ``unlisted`` posts get a page but no place in lists, feeds or the sitemap
#   (``build.list`` and ``sitemap.disable``), and ``noindex``;
# * ``password`` trips and posts are not imported at all. The export never
#   contains the password, and a static site could not check one anyway.
#   ``--include-private`` imports them as unlisted.


def is_protected(post: Post) -> bool:
    return post.visibility == "password" or bool(post.trip and post.trip.visibility == "password")


def effective_visibility(visibility: str) -> str:
    """With --include-private, password content arrives as unlisted."""
    return "unlisted" if visibility == "password" else visibility


def local_time(post: Post) -> datetime:
    """The post's instant on the wall clock where it was written (its Ortszeit).

    Hugo keeps the offset written in front matter, and the theme converts to
    ``params.time_zone`` again on display. Writing the local offset here makes
    the file read right to a human too: 00:20 in Venice is 00:20, not 22:20.
    """
    when = post.date.replace(microsecond=0)
    if post.time_zone:
        try:
            return when.astimezone(ZoneInfo(post.time_zone))
        except ZoneInfoNotFoundError:
            pass
    return when


# ---------------------------------------------------------------------------
# 3. Photos
# ---------------------------------------------------------------------------
#
# Why re-encode here, when Hugo already strips EXIF from every image it
# resizes? Because Hugo only protects public/. The project directory itself
# is meant to be committed, and a GitHub Pages site is usually built from a
# public repository: an original photo with GPS in a page bundle would publish
# the location there, including for posts whose location the author hid.
# So the copy in the bundle is re-encoded with Pillow at full size: pixels
# turned upright (EXIF orientation applied), colour profile kept, every other
# piece of metadata (EXIF, GPS, XMP, IPTC, PNG text) dropped. The theme then
# serves only Hugo's WebP renditions, and the trip sections set
# build.publishResources: false so not even this clean copy is published.


def clean_photo(source: Path) -> tuple[str, bytes] | None:
    """Return (filename, bytes) of a metadata-free copy, or None if unreadable."""
    try:
        with Image.open(source) as original:
            icc = original.info.get("icc_profile")
            image = ImageOps.exif_transpose(original)
            buffer = io.BytesIO()
            if image.mode in ("RGBA", "LA", "P") and (image.mode != "P" or "transparency" in image.info):
                image.save(buffer, "PNG", icc_profile=icc)
                suffix = ".png"
            else:
                image.convert("RGB").save(buffer, "JPEG", quality=JPEG_QUALITY, icc_profile=icc)
                suffix = ".jpg"
    except OSError as exc:  # e.g. HEIC without a plugin, or a broken file
        print(f"  skipped {source.name}: {exc}", file=sys.stderr)
        return None
    return source.stem + suffix, buffer.getvalue()


# ---------------------------------------------------------------------------
# 4. Hugo content
# ---------------------------------------------------------------------------
#
# Layout of the generated project (URLs match the hosted blog):
#
#   content/<trip>/_index.md          the trip, a section     -> /<trip>/
#   content/<trip>/<post>/index.md    a post, a page bundle   -> /<trip>/<post>/
#   content/<trip>/<post>/IMG_1.jpg   its photos, beside it
#   content/posts/<post>/index.md     a post without a trip   -> /posts/<post>/
#   content/archive.md, impressum.md  the archive and the imprint


class Dumper(yaml.SafeDumper):
    """Writes timestamps as ISO 8601 with their offset (2026-10-04T00:20:00+02:00),
    not PyYAML's default with a space."""


Dumper.add_representer(
    datetime, lambda dumper, value: dumper.represent_scalar("tag:yaml.org,2002:timestamp", value.isoformat())
)


def front_matter(data: dict) -> str:
    text = yaml.dump(data, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=1000)
    return f"---\n{STAMP}{text}---\n"


def post_files(post: Post, folder: str, include_private: bool) -> dict[str, bytes]:
    """The page bundle of one post: index.md plus its cleaned photos."""
    files: dict[str, bytes] = {}
    photos = []
    for photo in post.photos:
        if photo.source is None:
            continue  # free exports list media but carry no binaries
        cleaned = clean_photo(photo.source)
        if cleaned is None:
            continue
        name, data = cleaned
        stem, suffix = name.rsplit(".", 1)
        n = 2
        while f"{folder}/{name}" in files:  # IMG.png and IMG.jpg would collide
            name, n = f"{stem}-{n}.{suffix}", n + 1
        files[f"{folder}/{name}"] = data
        entry = {"file": name, "alt": photo.alt}
        if photo.taken_at:
            entry["taken_at"] = photo.taken_at.isoformat()
        photos.append(entry)

    visibility = effective_visibility(post.visibility) if include_private else post.visibility
    params: dict = {"time_zone": post.time_zone}
    if post.place and not post.hide_location:
        place = {k: post.place[k] for k in ("name", "osm", "geonames") if k in post.place}
        params["place"] = place
        if "lat" in post.place and "lon" in post.place:
            params["coordinates"] = {"lat": post.place["lat"], "lon": post.place["lon"]}
    # A hidden location is left out of the project entirely, not just off the
    # page: the project may live in a public repository. The export keeps it.
    params["hide_location"] = post.hide_location
    params["visibility"] = visibility
    params["photos"] = photos

    data: dict = {}
    if post.title:
        data["title"] = post.title
    data["date"] = local_time(post)
    data["draft"] = post.draft
    # publishResources: false keeps even the cleaned originals out of public/;
    # the theme publishes only resized WebP renditions. It is set here as well
    # as in the section's cascade because a page's own `build` replaces it.
    build: dict = {"publishResources": False}
    if visibility != "public":
        # An unlisted trip still lists its own posts (like the hosted blog);
        # anywhere else an unlisted post is reachable only by its URL.
        trip_unlisted = post.trip and effective_visibility(post.trip.visibility) != "public"
        build["list"] = "local" if trip_unlisted else "never"
        data["sitemap"] = {"disable": True}
    data["build"] = build
    data["params"] = params
    text = front_matter(data) + "\n" + post.body + "\n"
    files[f"{folder}/index.md"] = text.encode()
    return files


def section_cascade() -> dict:
    """Pages in a trip (or posts/) are posts, and publish no original files.
    New posts you write there inherit both."""
    return {"type": "post", "build": {"publishResources": False}}


def trip_file(trip: Trip, include_private: bool) -> bytes:
    visibility = effective_visibility(trip.visibility) if include_private else trip.visibility
    data: dict = {"title": trip.title, "type": "trip"}
    if visibility != "public":
        data["sitemap"] = {"disable": True}
    data["params"] = {
        "started_on": trip.started_on,
        "ended_on": trip.ended_on,
        "visibility": visibility,
        "co_authors": trip.co_authors,
    }
    data["cascade"] = section_cascade()
    return front_matter(data).encode()


def safe_css(css: str) -> str:
    """The author's custom CSS, minus anything that fetches from elsewhere or
    pins itself over the page (the hosted blog's rules, spec §5)."""
    css = re.sub(r"@import[^;]*;?", "", css, flags=re.IGNORECASE)
    css = re.sub(r"url\(\s*['\"]?\s*(https?:)?//[^)]*\)", "none", css, flags=re.IGNORECASE)
    return re.sub(r"position\s*:\s*fixed", "position:static", css, flags=re.IGNORECASE)


def toml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid TOML basic string


def hugo_toml(blog: Blog, lang: str, base_url: str) -> str:
    return f"""# Your Hugo site, imported from an Ortszeit export by ortszeit_to_hugo.py.
# This file is yours: the importer writes it only when it does not exist yet,
# so a later import never changes what you set here.

baseURL = {toml_str(base_url.rstrip("/") + "/")}
languageCode = {toml_str(lang)}
defaultContentLanguage = {toml_str(lang)}
title = {toml_str(blog.title)}
theme = "ortszeit"
enableRobotsTXT = true
disableKinds = ["taxonomy", "term"]

# No [permalinks] on purpose: a page's URL is its folder under content/, and
# the folders carry the Ortszeit slugs. /<trip>/<post>/ and /posts/<post>/
# are therefore the same addresses as on the hosted blog.

[outputs]
  home = ["html", "rss", "jsonfeed"]  # /feed.xml and /feed.json
  section = ["html"]
  page = ["html"]

[params]
  description = {toml_str(blog.bio)}

  # From blog.yaml. location: hidden | place | coordinates.
  # time: false hides the clock time next to the date.
  [params.display]
    location = {toml_str(blog.location)}
    time = {"true" if blog.show_time else "false"}

# Raw HTML in posts is shown as text, as on Ortszeit. Hugo's default; stated
# here so it is a decision, not an accident.
[markup.goldmark.renderer]
  unsafe = false
"""


GITIGNORE = "public/\nresources/\n.hugo_build.lock\n"


def project_files(blog: Blog, lang: str, include_private: bool) -> tuple[dict[str, bytes], dict]:
    """Everything the importer owns, as {relative path: bytes}, plus counts."""
    t = TITLES[lang]
    files: dict[str, bytes] = {}
    for path in sorted(THEME.rglob("*")):
        if path.is_file() and path.name != ".DS_Store":
            files[f"themes/ortszeit/{path.relative_to(THEME).as_posix()}"] = path.read_bytes()

    trips = [t_ for t_ in blog.trips if t_.visibility != "password" or include_private]
    for trip in trips:
        files[f"content/{trip.slug}/_index.md"] = trip_file(trip, include_private)
    standalone = {
        "title": t["posts"],
        # posts/ is only a folder for posts without a trip. The hosted blog
        # has no page at /posts/, so neither does this site.
        "build": {"render": "never", "list": "never"},
        "cascade": section_cascade(),
    }
    files["content/posts/_index.md"] = front_matter(standalone).encode()

    imported = drafts = left_out = 0
    for post in blog.posts:
        if is_protected(post) and not include_private:
            left_out += 1
            continue
        folder = f"content/{post.trip.slug if post.trip else 'posts'}/{post.slug}"
        files.update(post_files(post, folder, include_private))
        imported += 1
        drafts += post.draft

    files["content/archive.md"] = front_matter({"title": t["archive"], "layout": "archive"}).encode()
    if blog.imprint.strip():
        files["content/impressum.md"] = (
            front_matter({"title": t["imprint"]}) + "\n" + blog.imprint.strip() + "\n"
        ).encode()
    if blog.custom_css.strip():
        header = "/* Custom CSS from your Ortszeit export (sanitised). Appended after the theme. */\n"
        files["assets/css/custom.css"] = (header + safe_css(blog.custom_css).strip() + "\n").encode()
    counts = {"posts": imported, "drafts": drafts, "left_out": left_out, "trips": len(trips)}
    return files, counts


# ---------------------------------------------------------------------------
# 5. Writing into the target, without touching what is yours
# ---------------------------------------------------------------------------
#
# The importer can run again after a new export. It keeps a manifest
# (.ortszeit-import.json) with the checksum of every file it wrote. On the
# next run a file is replaced or removed only if it is still exactly what the
# importer wrote last time. Anything you edited, and anything the importer
# never wrote, is left alone, and the run says so.
#
# Written only once, then yours: hugo.toml and .gitignore.


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sync(out: Path, files: dict[str, bytes], once: dict[str, bytes]) -> dict[str, list[str]]:
    manifest_path = out / MANIFEST
    old: dict[str, str] = {}
    if manifest_path.is_file():
        old = json.loads(manifest_path.read_text(encoding="utf-8")).get("files", {})
    new: dict[str, str] = {}
    report: dict[str, list[str]] = {"written": [], "unchanged": [], "kept": [], "removed": []}

    for rel, data in once.items():
        target = out / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            report["written"].append(rel)

    for rel, data in sorted(files.items()):
        target, digest = out / rel, sha(data)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            report["written"].append(rel)
            new[rel] = digest
            continue
        current = sha(target.read_bytes())
        if current == digest:
            report["unchanged"].append(rel)
            new[rel] = digest
        elif old.get(rel) == current:  # ours, untouched since: replace
            target.write_bytes(data)
            report["written"].append(rel)
            new[rel] = digest
        else:  # edited by you, or never ours: keep, and remember it is not ours
            report["kept"].append(rel)
            if rel in old:
                new[rel] = old[rel]

    for rel, digest in old.items():
        if rel in files:
            continue
        target = out / rel
        if target.is_file() and sha(target.read_bytes()) == digest:
            target.unlink()  # e.g. a post that is now password-protected
            report["removed"].append(rel)
            for parent in target.parents:
                if parent == out or any(parent.iterdir()):
                    break
                parent.rmdir()

    manifest = {
        "note": "Written by ortszeit_to_hugo.py: files it may replace on the next import, "
        "with the checksum it wrote. A file you edit no longer matches and is left alone.",
        "files": dict(sorted(new.items())),
    }
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# 6. The command line
# ---------------------------------------------------------------------------


def run(source: Path, out: Path, *, lang: str, base_url: str | None, include_private: bool) -> int:
    if out.exists() and not out.is_dir():
        raise ExportError(f"{out} exists and is not a directory")
    if not THEME.is_dir():
        raise ExportError(f"theme not found at {THEME}; run the script from its repository folder")
    with tempfile.TemporaryDirectory() as workdir:
        blog = read_blog(find_root(source, Path(workdir)))
        if not base_url:
            base_url = f"https://{blog.custom_domain}/" if blog.custom_domain else "http://localhost:1313/"
        files, counts = project_files(blog, lang, include_private)
    once = {"hugo.toml": hugo_toml(blog, lang, base_url).encode(), ".gitignore": GITIGNORE.encode()}
    out.mkdir(parents=True, exist_ok=True)
    report = sync(out, files, once)

    print(
        f"imported into {out}: posts {counts['posts']} (drafts {counts['drafts']}), trips {counts['trips']}"
        f"; left out: password-protected posts {counts['left_out']}"
    )
    print(
        f"  files written {len(report['written'])}, unchanged {len(report['unchanged'])}, "
        f"removed {len(report['removed'])}, kept as you left them {len(report['kept'])}"
    )
    for rel in report["kept"]:
        print(f"  kept (changed by you, or not written by this importer): {rel}")
    if "hugo.toml" not in report["written"]:
        print("  hugo.toml exists and was left as it is")
    if include_private and counts["left_out"] == 0 and any(is_protected(p) for p in blog.posts):
        print("  note: password-protected posts were imported as unlisted pages. They are NOT protected.")
    print(f"next: cd {out} && hugo server")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import an Ortszeit export into a Hugo project.")
    parser.add_argument("source", type=Path, help="export zip, or the extracted blog/ directory")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Hugo project directory (created or updated)")
    parser.add_argument("--lang", choices=sorted(TITLES), default="en", help="site language (labels, dates)")
    parser.add_argument("--base-url", help="public URL of the site, e.g. https://example.org/ (first import only)")
    parser.add_argument(
        "--include-private",
        action="store_true",
        help="also import password-protected trips/posts, as unlisted (no protection!)",
    )
    args = parser.parse_args(argv)
    try:
        return run(
            args.source,
            args.output,
            lang=args.lang,
            base_url=args.base_url,
            include_private=args.include_private,
        )
    except ExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
