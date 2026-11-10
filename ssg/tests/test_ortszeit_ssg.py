#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest>=8", "markdown-it-py>=3.0", "pyyaml>=6.0", "pillow>=10.0"]
# ///
"""Tests for ortszeit_ssg.py against the checked-in sample export.

uv run ssg/tests/test_ortszeit_ssg.py
"""

import importlib.util
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SAMPLE = HERE.parent.parent / "sample-export"
spec = importlib.util.spec_from_file_location("ortszeit_ssg", HERE.parent / "ortszeit_ssg.py")
ssg = importlib.util.module_from_spec(spec)
sys.modules["ortszeit_ssg"] = ssg
spec.loader.exec_module(ssg)


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("site") / "out"
    assert ssg.main([str(SAMPLE), "-o", str(out), "--lang", "de", "--base-url", "https://example.org"]) == 0
    return out


def test_ortszeit_after_midnight(site):
    # 22:20 UTC on 3 Oct is 00:20 on 4 Oct in Venice: the page shows Venice time.
    page = (site / "venedig-2026" / "2026-10-04" / "index.html").read_text()
    assert '<time datetime="2026-10-04T00:20:00+02:00">4. Okt. · 00:20</time>' in page


def test_no_clock_without_time_zone(site):
    page = (site / "posts" / "wieder-zu-hause" / "index.html").read_text()
    assert '<time datetime="2026-10-08">8. Okt.</time>' in page


def test_hide_location_is_respected(site):
    page = (site / "venedig-2026" / "burano" / "index.html").read_text()
    assert "45.485" not in page and 'class="place"' not in page


def test_drafts_and_password_trips_are_left_out(site):
    assert not (site / "familie").exists()
    assert not list(site.rglob("*packliste*"))


def test_derivatives_carry_no_exif(site):
    from PIL import Image

    for jpg in site.rglob("*.jpg"):
        with Image.open(jpg) as image:
            assert not image.getexif()


def test_feeds_are_well_formed(site):
    for name in ("feed.xml", "atom.xml", "sitemap.xml"):
        ET.parse(site / name)
    feed = json.loads((site / "feed.json").read_text())
    assert feed["version"] == "https://jsonfeed.org/version/1.1"
    assert all(item["id"].startswith("https://example.org/") for item in feed["items"])


def test_wrong_schema_version(tmp_path):
    shutil.copytree(SAMPLE, tmp_path / "blog")
    yaml_file = tmp_path / "blog" / "blog.yaml"
    yaml_file.write_text(yaml_file.read_text().replace("ortszeit_schema: 1", "ortszeit_schema: 99"))
    assert ssg.main([str(tmp_path), "-o", str(tmp_path / "out")]) == 2


def test_refuses_to_replace_foreign_directory(tmp_path):
    (tmp_path / "precious.txt").write_text("keep me")
    assert ssg.main([str(SAMPLE), "-o", str(tmp_path)]) == 2
    assert (tmp_path / "precious.txt").exists()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
