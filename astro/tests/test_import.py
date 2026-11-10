#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest>=8", "pyyaml>=6.0", "pillow>=10.0"]
# ///
"""Tests for ortszeit_to_astro.py against the checked-in sample export.

uv run astro/tests/test_import.py

The tests that build the site run `npm install` and `astro build` (and
`astro check`) in a temporary copy. They are skipped when npm is not
installed, when the install fails (offline), or with ORTSZEIT_SKIP_BUILD=1.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml
from PIL import Image

HERE = Path(__file__).resolve().parent
SAMPLE = HERE.parent.parent / "sample-export"
spec = importlib.util.spec_from_file_location("ortszeit_to_astro", HERE.parent / "ortszeit_to_astro.py")
importer = importlib.util.module_from_spec(spec)
sys.modules["ortszeit_to_astro"] = importer
spec.loader.exec_module(importer)

NPM = shutil.which("npm")
SKIP_BUILD = NPM is None or os.environ.get("ORTSZEIT_SKIP_BUILD") == "1"
needs_npm = pytest.mark.skipif(SKIP_BUILD, reason="npm is not installed, or ORTSZEIT_SKIP_BUILD=1")
GPS_IFD = 0x8825
POSTS = "src/content/posts"


def with_gps(export: Path) -> Path:
    """The sample's photos carry no EXIF; give them a camera tag and GPS, as a phone would."""
    for jpg in export.rglob("*.jpg"):
        with Image.open(jpg) as image:
            exif = Image.Exif()
            exif[0x010F] = "PhoneMaker"  # Make
            exif[GPS_IFD] = {1: "N", 2: (45.0, 29.0, 7.0), 3: "E", 4: (12.0, 25.0, 0.0)}
            image.save(jpg, exif=exif.tobytes())
        with Image.open(jpg) as check:
            assert check.getexif().get_ifd(GPS_IFD)  # the fixture really has GPS
    return export


def front(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text[4:].partition("\n---\n")[0])


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    export = with_gps(Path(shutil.copytree(SAMPLE, tmp_path_factory.mktemp("export") / "blog")))
    out = tmp_path_factory.mktemp("site") / "mysite"
    assert importer.main([str(export), "-o", str(out), "--lang", "de", "--base-url", "https://example.org"]) == 0
    return out


@pytest.fixture(scope="module")
def built(project, tmp_path_factory) -> Path:
    """A copy of the project with node_modules, after `astro build`."""
    if SKIP_BUILD:
        pytest.skip("npm is not installed, or ORTSZEIT_SKIP_BUILD=1")
    site = Path(shutil.copytree(project, tmp_path_factory.mktemp("build") / "mysite"))
    env = {**os.environ, "ASTRO_TELEMETRY_DISABLED": "1"}
    install = subprocess.run(
        [NPM, "install", "--no-audit", "--no-fund"], cwd=site, env=env, capture_output=True, text=True, check=False
    )
    if install.returncode != 0:
        pytest.skip(f"npm install failed (offline?): {install.stderr[-500:]}")
    subprocess.run([NPM, "run", "build"], cwd=site, env=env, check=True, capture_output=True)
    return site


@pytest.fixture(scope="module")
def dist(built) -> Path:
    return built / "dist"


# --- the imported project ----------------------------------------------------


def test_structure(project):
    for rel in (
        "package.json",
        "tsconfig.json",
        "astro.config.mjs",
        ".gitignore",
        importer.MANIFEST,
        "src/content.config.ts",
        "src/site.config.ts",
        "src/layouts/Base.astro",
        "src/pages/[trip]/[post].astro",
        "src/styles/ortszeit.css",
        "src/styles/custom.css",
        "src/content/trips/venedig-2026.yaml",
        f"{POSTS}/venedig-2026/ankunft-am-bahnhof/index.md",
        f"{POSTS}/venedig-2026/ankunft-am-bahnhof/IMG_4100.jpg",
        f"{POSTS}/venedig-2026/2026-10-04/IMG_4101.jpg",
        f"{POSTS}/posts/wieder-zu-hause/index.md",
        "src/content/pages/impressum.md",
    ):
        assert (project / rel).is_file(), rel
    config = (project / "src/site.config.ts").read_text()
    assert 'title: "Unterwegs mit Lena"' in config and 'location: "coordinates"' in config
    assert 'lang: "de"' in config and "time: true" in config
    assert 'site: "https://example.org"' in (project / "astro.config.mjs").read_text()


def test_versions_are_pinned():
    package = json.loads((importer.TEMPLATE / "package.json").read_text())
    versions = {**package["dependencies"], **package["devDependencies"]}
    assert versions["astro"].startswith("7.")
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in versions.values()), versions  # exact pins


def test_front_matter(project):
    meta = front(project / f"{POSTS}/venedig-2026/2026-10-04/index.md")
    assert "title" not in meta  # titles are optional
    assert meta["date"].isoformat() == "2026-10-04T00:20:00+02:00"  # local offset kept
    assert meta["time_zone"] == "Europe/Rome"
    assert meta["draft"] is False
    assert meta["trip"] == "venedig-2026"
    assert meta["place"] == {"name": "Ponte di Rialto", "osm": "way/25058567"}
    assert meta["coordinates"] == {"lat": 45.438, "lon": 12.3359}
    assert meta["hide_location"] is False and meta["visibility"] == "public"
    assert meta["photos"] == [
        {"file": "./IMG_4101.jpg", "alt": "Rialtobrücke bei Nacht"},
        {"file": "./IMG_4102.jpg", "alt": None},
    ]
    assert "trip" not in front(project / f"{POSTS}/posts/wieder-zu-hause/index.md")

    trip = yaml.safe_load((project / "src/content/trips/venedig-2026.yaml").read_text())
    assert trip["title"] == "Venedig im Oktober" and str(trip["started_on"]) == "2026-10-03"
    assert trip["co_authors"] == ["Lena"]


def test_schema_knows_every_field_the_importer_writes(project):
    schema = (project / "src/content.config.ts").read_text()
    meta = front(project / f"{POSTS}/venedig-2026/ankunft-am-bahnhof/index.md")
    for key in meta:
        assert re.search(rf"^\s+{key}: ", schema, re.MULTILINE), key


def test_hidden_location_stays_out_of_the_project(project):
    meta = front(project / f"{POSTS}/venedig-2026/burano/index.md")
    assert meta["hide_location"] is True
    assert "place" not in meta and "coordinates" not in meta
    assert "45.48" not in (project / f"{POSTS}/venedig-2026/burano/index.md").read_text()


def test_drafts_stay_drafts_and_password_trips_stay_out(project):
    assert front(project / f"{POSTS}/posts/entwurf-packliste/index.md")["draft"] is True
    assert not (project / POSTS / "familie").exists()
    assert not (project / "src/content/trips/familie.yaml").exists()
    assert "Omas" not in "".join(p.read_text() for p in (project / "src/content").rglob("*.md"))


def test_include_private_imports_password_posts_as_unlisted(tmp_path):
    assert importer.main([str(SAMPLE), "-o", str(tmp_path), "--include-private"]) == 0
    assert front(tmp_path / f"{POSTS}/familie/omas-geburtstag/index.md")["visibility"] == "unlisted"
    assert yaml.safe_load((tmp_path / "src/content/trips/familie.yaml").read_text())["visibility"] == "unlisted"


def test_photos_in_the_project_carry_no_exif(project):
    images = list((project / "src").rglob("*.jpg"))
    assert len(images) == 3
    for jpg in images:
        with Image.open(jpg) as image:
            assert not image.getexif()
        assert b"PhoneMaker" not in jpg.read_bytes()


def test_custom_css_is_sanitised(tmp_path):
    export = Path(shutil.copytree(SAMPLE, tmp_path / "blog"))
    blog = yaml.safe_load((export / "blog.yaml").read_text())
    blog["custom_css"] = (
        "@import url(https://evil.example/x.css);\nbody{background:url(//evil.example/a.png)}\n.x{position:fixed}"
    )
    (export / "blog.yaml").write_text(yaml.safe_dump(blog))
    assert importer.main([str(export), "-o", str(tmp_path / "out")]) == 0
    css = (tmp_path / "out/src/styles/custom.css").read_text()
    assert "evil.example" not in css and "fixed" not in css and "position:static" in css


# --- running it again ----------------------------------------------------------


def test_second_run_keeps_your_changes(tmp_path):
    out = tmp_path / "site"
    assert importer.main([str(SAMPLE), "-o", str(out)]) == 0
    post = out / f"{POSTS}/venedig-2026/burano/index.md"
    post.write_text(post.read_text() + "\nA line I added.\n")
    config = out / "astro.config.mjs"
    config.write_text(config.read_text().replace("localhost:4321", "lena.example"))
    (out / "src/pages/about.astro").write_text("<p>Mine</p>\n")
    template_file = out / "src/pages/404.astro"
    template_file.unlink()  # deleted by accident: the next import restores it

    assert importer.main([str(SAMPLE), "-o", str(out), "--base-url", "https://other.example"]) == 0
    assert "A line I added." in post.read_text()
    assert "lena.example" in config.read_text()  # astro.config.mjs is written only once
    assert (out / "src/pages/about.astro").exists()
    assert template_file.exists()


def test_second_run_removes_what_left_the_export(tmp_path):
    export = Path(shutil.copytree(SAMPLE, tmp_path / "blog"))
    out = tmp_path / "site"
    assert importer.main([str(export), "-o", str(out)]) == 0
    assert (out / f"{POSTS}/venedig-2026/ankunft-am-bahnhof/IMG_4100.jpg").exists()
    (export / "trips/venedig-2026/2026-10-03-ankunft-am-bahnhof.md").unlink()
    assert importer.main([str(export), "-o", str(out)]) == 0
    assert not (out / f"{POSTS}/venedig-2026/ankunft-am-bahnhof").exists()


def test_second_run_is_a_no_op(tmp_path, capsys):
    assert importer.main([str(SAMPLE), "-o", str(tmp_path)]) == 0
    assert importer.main([str(SAMPLE), "-o", str(tmp_path)]) == 0
    assert "files written 0," in capsys.readouterr().out


def test_wrong_schema_version(tmp_path):
    shutil.copytree(SAMPLE, tmp_path / "blog")
    yaml_file = tmp_path / "blog" / "blog.yaml"
    yaml_file.write_text(yaml_file.read_text().replace("ortszeit_schema: 1", "ortszeit_schema: 99"))
    assert importer.main([str(tmp_path), "-o", str(tmp_path / "out")]) == 2


# --- the built site (needs npm and the network once) --------------------------


@needs_npm
def test_astro_check(built):
    env = {**os.environ, "ASTRO_TELEMETRY_DISABLED": "1"}
    result = subprocess.run([NPM, "run", "check"], cwd=built, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout[-2000:]
    assert "0 errors" in result.stdout


@needs_npm
def test_ortszeit_after_midnight(dist):
    # 22:20 UTC on 3 Oct is 00:20 on 4 Oct in Venice: the page shows Venice time.
    page = (dist / "venedig-2026/2026-10-04/index.html").read_text()
    assert '<time datetime="2026-10-04T00:20:00+02:00">4. Okt. · 00:20</time>' in page
    assert "<title>4. Oktober 2026 · Unterwegs mit Lena</title>" in page  # untitled: the local date names it
    assert "45.438N 12.336E" in page  # display.location: coordinates


@needs_npm
def test_no_clock_without_time_zone(dist):
    page = (dist / "posts/wieder-zu-hause/index.html").read_text()
    assert '<time datetime="2026-10-08">8. Okt.</time>' in page


@needs_npm
def test_urls_match_the_hosted_blog(dist):
    for rel in ("venedig-2026", "venedig-2026/burano", "posts/wieder-zu-hause", "archive", "places", "impressum"):
        assert (dist / rel / "index.html").is_file(), rel
    assert not (dist / "posts/index.html").exists()  # the hosted blog has no /posts/ page


@needs_npm
def test_drafts_and_password_posts_are_not_published(dist):
    assert not list(dist.rglob("*packliste*"))
    assert not (dist / "familie").exists()
    assert "packliste" not in (dist / "sitemap.xml").read_text()


@needs_npm
def test_hide_location_on_the_page(dist):
    page = (dist / "venedig-2026/burano/index.html").read_text()
    assert "45.485" not in page and "12.417" not in page
    assert 'class="place' not in page
    assert "Burano" not in (dist / "places/index.html").read_text().split("<main>")[1]


@needs_npm
def test_public_images_carry_no_exif_or_gps(dist):
    files = [p for p in dist.rglob("*") if p.is_file()]
    assert not [p for p in files if p.suffix.lower() in (".jpg", ".jpeg", ".png")]  # no originals
    webps = [p for p in files if p.suffix == ".webp"]
    assert webps
    for path in webps:
        data = path.read_bytes()
        assert b"EXIF" not in data and b"Exif" not in data and b"PhoneMaker" not in data
        with Image.open(path) as image:
            assert not image.getexif()


@needs_npm
def test_responsive_images(dist):
    page = (dist / "venedig-2026/ankunft-am-bahnhof/index.html").read_text()
    assert re.search(r'srcset="[^"]*\.webp 400w', page) and 'loading="lazy"' in page


@needs_npm
def test_feeds_are_well_formed(dist):
    for name in ("feed.xml", "sitemap.xml"):
        ET.parse(dist / name)
    rss = ET.parse(dist / "feed.xml").getroot()
    items = rss.findall("./channel/item")
    assert len(items) == 4
    assert all(i.findtext("link").startswith("https://example.org/") for i in items)
    assert "Sitemap: https://example.org/sitemap.xml" in (dist / "robots.txt").read_text()


@needs_npm
def test_html_in_a_post_is_text(built):
    post = built / f"{POSTS}/venedig-2026/burano/index.md"
    post.write_text(post.read_text() + "\n<script>alert(1)</script>\n\nSo <b>bold</b>.\n")
    env = {**os.environ, "ASTRO_TELEMETRY_DISABLED": "1"}
    out = built / "dist-html"
    subprocess.run(
        [NPM, "run", "build", "--", "--outDir", str(out)], cwd=built, env=env, check=True, capture_output=True
    )
    page = (out / "venedig-2026/burano/index.html").read_text()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "&lt;b&gt;bold&lt;/b&gt;" in page
    assert "<script" not in page


@needs_npm
def test_no_javascript_and_no_external_requests(dist):
    for page in dist.rglob("*.html"):
        html = page.read_text()
        assert "<script" not in html
        assert not re.search(r'(src|srcset)="?https?://', html)
        assert not re.search(r'<link[^>]*rel="?stylesheet"?[^>]*href="?https?://', html)
    assert not list(dist.rglob("*.js"))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
