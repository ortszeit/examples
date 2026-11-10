#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest>=8", "pyyaml>=6.0", "pillow>=10.0"]
# ///
"""Tests for ortszeit_to_hugo.py against the checked-in sample export.

uv run hugo/tests/test_import.py

The tests that build the site call `hugo` and are skipped when it is not
installed.
"""

import importlib.util
import json
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
spec = importlib.util.spec_from_file_location("ortszeit_to_hugo", HERE.parent / "ortszeit_to_hugo.py")
importer = importlib.util.module_from_spec(spec)
sys.modules["ortszeit_to_hugo"] = importer
spec.loader.exec_module(importer)

HUGO = shutil.which("hugo")
needs_hugo = pytest.mark.skipif(HUGO is None, reason="hugo is not installed")
GPS_IFD = 0x8825


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
def public(project) -> Path:
    if HUGO is None:
        pytest.skip("hugo is not installed")
    subprocess.run([HUGO, "--minify", "--quiet", "-s", str(project)], check=True)
    return project / "public"


# --- the imported project ----------------------------------------------------


def test_structure(project):
    for rel in (
        "hugo.toml",
        ".gitignore",
        importer.MANIFEST,
        "themes/ortszeit/layouts/baseof.html",
        "content/venedig-2026/_index.md",
        "content/venedig-2026/ankunft-am-bahnhof/index.md",
        "content/venedig-2026/ankunft-am-bahnhof/IMG_4100.jpg",
        "content/venedig-2026/2026-10-04/IMG_4101.jpg",
        "content/posts/_index.md",
        "content/posts/wieder-zu-hause/index.md",
        "content/archive.md",
        "content/impressum.md",
    ):
        assert (project / rel).is_file(), rel
    toml = (project / "hugo.toml").read_text()
    assert 'theme = "ortszeit"' in toml and 'baseURL = "https://example.org/"' in toml
    assert 'title = "Unterwegs mit Lena"' in toml and 'location = "coordinates"' in toml


def test_front_matter(project):
    meta = front(project / "content/venedig-2026/2026-10-04/index.md")
    assert "title" not in meta  # titles are optional
    assert meta["date"].isoformat() == "2026-10-04T00:20:00+02:00"  # local offset kept
    assert meta["draft"] is False
    assert meta["build"]["publishResources"] is False
    params = meta["params"]
    assert params["time_zone"] == "Europe/Rome"
    assert params["place"]["name"] == "Ponte di Rialto"
    assert params["coordinates"] == {"lat": 45.438, "lon": 12.3359}
    assert params["hide_location"] is False
    assert [p["file"] for p in params["photos"]] == ["IMG_4101.jpg", "IMG_4102.jpg"]

    trip = front(project / "content/venedig-2026/_index.md")
    assert trip["type"] == "trip" and trip["cascade"]["type"] == "post"
    assert str(trip["params"]["started_on"]) == "2026-10-03"


def test_hidden_location_stays_out_of_the_project(project):
    params = front(project / "content/venedig-2026/burano/index.md")["params"]
    assert params["hide_location"] is True
    assert "place" not in params and "coordinates" not in params
    assert "45.48" not in (project / "content/venedig-2026/burano/index.md").read_text()


def test_drafts_stay_drafts_and_password_trips_stay_out(project):
    assert front(project / "content/posts/entwurf-packliste/index.md")["draft"] is True
    assert not (project / "content" / "familie").exists()
    assert "Omas" not in "".join(p.read_text() for p in (project / "content").rglob("*.md"))


def test_include_private_imports_password_posts_as_unlisted(tmp_path):
    assert importer.main([str(SAMPLE), "-o", str(tmp_path), "--include-private"]) == 0
    meta = front(tmp_path / "content/familie/omas-geburtstag/index.md")
    assert meta["params"]["visibility"] == "unlisted"
    assert meta["build"]["list"] == "local" and meta["sitemap"] == {"disable": True}


def test_photos_in_the_project_carry_no_exif(project):
    images = list((project / "content").rglob("*.jpg"))
    assert len(images) == 3
    for jpg in images:
        with Image.open(jpg) as image:
            assert not image.getexif()
        assert b"PhoneMaker" not in jpg.read_bytes()


# --- running it again ----------------------------------------------------------


def test_second_run_keeps_your_changes(tmp_path):
    out = tmp_path / "site"
    assert importer.main([str(SAMPLE), "-o", str(out)]) == 0
    post = out / "content/venedig-2026/burano/index.md"
    post.write_text(post.read_text() + "\nA line I added.\n")
    (out / "hugo.toml").write_text((out / "hugo.toml").read_text().replace("localhost:1313", "lena.example"))
    (out / "content/my-own-page.md").write_text("---\ntitle: Mine\n---\n")
    theme_file = out / "themes/ortszeit/layouts/404.html"
    theme_file.unlink()  # deleted by accident: the next import restores it

    assert importer.main([str(SAMPLE), "-o", str(out), "--base-url", "https://other.example"]) == 0
    assert "A line I added." in post.read_text()
    assert "lena.example" in (out / "hugo.toml").read_text()  # hugo.toml is written only once
    assert (out / "content/my-own-page.md").exists()
    assert theme_file.exists()


def test_second_run_removes_what_left_the_export(tmp_path):
    export = Path(shutil.copytree(SAMPLE, tmp_path / "blog"))
    out = tmp_path / "site"
    assert importer.main([str(export), "-o", str(out)]) == 0
    assert (out / "content/venedig-2026/ankunft-am-bahnhof/IMG_4100.jpg").exists()
    (export / "trips/venedig-2026/2026-10-03-ankunft-am-bahnhof.md").unlink()
    assert importer.main([str(export), "-o", str(out)]) == 0
    assert not (out / "content/venedig-2026/ankunft-am-bahnhof").exists()


def test_second_run_is_a_no_op(tmp_path, capsys):
    assert importer.main([str(SAMPLE), "-o", str(tmp_path)]) == 0
    assert importer.main([str(SAMPLE), "-o", str(tmp_path)]) == 0
    assert "files written 0," in capsys.readouterr().out


def test_wrong_schema_version(tmp_path):
    shutil.copytree(SAMPLE, tmp_path / "blog")
    yaml_file = tmp_path / "blog" / "blog.yaml"
    yaml_file.write_text(yaml_file.read_text().replace("ortszeit_schema: 1", "ortszeit_schema: 99"))
    assert importer.main([str(tmp_path), "-o", str(tmp_path / "out")]) == 2


# --- the built site (needs hugo) ---------------------------------------------


@needs_hugo
def test_ortszeit_after_midnight(public):
    # 22:20 UTC on 3 Oct is 00:20 on 4 Oct in Venice: the page shows Venice time.
    page = (public / "venedig-2026/2026-10-04/index.html").read_text()
    assert re.search(r'<time datetime="?2026-10-04T00:20:00\+02:00"?>4\. Okt\. · 00:20</time>', page)
    assert "4. Oktober 2026 · Unterwegs mit Lena" in page  # untitled: the local date names it


@needs_hugo
def test_no_clock_without_time_zone(public):
    page = (public / "posts/wieder-zu-hause/index.html").read_text()
    assert re.search(r'<time datetime="?2026-10-08"?>8\. Okt\.</time>', page)


@needs_hugo
def test_urls_match_the_hosted_blog(public):
    for rel in ("venedig-2026", "venedig-2026/burano", "posts/wieder-zu-hause", "archive", "impressum"):
        assert (public / rel / "index.html").is_file(), rel
    assert not (public / "posts/index.html").exists()  # the hosted blog has no /posts/ page


@needs_hugo
def test_drafts_and_password_posts_are_not_published(public):
    assert not list(public.rglob("*packliste*"))
    assert not (public / "familie").exists()
    assert "packliste" not in (public / "sitemap.xml").read_text()


@needs_hugo
def test_hide_location_on_the_page(public):
    page = (public / "venedig-2026/burano/index.html").read_text()
    assert "45.485" not in page and "12.417" not in page
    assert not re.search(r'class="?place', page)


@needs_hugo
def test_public_images_carry_no_exif_or_gps(public):
    files = [p for p in public.rglob("*") if p.is_file()]
    assert not [p for p in files if p.suffix.lower() in (".jpg", ".jpeg", ".png")]  # no originals
    webps = [p for p in files if p.suffix == ".webp"]
    assert webps
    for path in webps:
        data = path.read_bytes()
        assert b"EXIF" not in data and b"Exif" not in data and b"PhoneMaker" not in data
        with Image.open(path) as image:
            assert not image.getexif()


@needs_hugo
def test_responsive_images(public):
    page = (public / "venedig-2026/ankunft-am-bahnhof/index.html").read_text()
    assert re.search(r'srcset="?[^"]*\.webp 400w', page) and "loading=lazy" in page.replace('"', "")


@needs_hugo
def test_feeds_are_well_formed(public):
    for name in ("feed.xml", "sitemap.xml"):
        ET.parse(public / name)
    rss = ET.parse(public / "feed.xml").getroot()
    assert len(rss.findall("./channel/item")) == 4
    feed = json.loads((public / "feed.json").read_text())
    assert feed["version"] == "https://jsonfeed.org/version/1.1"
    assert all(item["id"].startswith("https://example.org/") for item in feed["items"])
    night = next(i for i in feed["items"] if i["id"].endswith("/2026-10-04/"))
    assert night["date_published"] == "2026-10-04T00:20:00+02:00"


@needs_hugo
def test_no_javascript_and_no_external_requests(public):
    for page in public.rglob("*.html"):
        html = page.read_text()
        assert "<script" not in html
        assert not re.search(r'(src|srcset)="?https?://', html)
        assert not re.search(r'<link[^>]*rel="?stylesheet"?[^>]*href="?https?://', html)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
