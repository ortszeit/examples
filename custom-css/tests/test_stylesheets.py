#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest>=8"]
# ///
"""Checks for the example stylesheets in custom-css/.

uv run custom-css/tests/test_stylesheets.py

Ortszeit removes some constructs from custom CSS before it reaches a blog
(see the README: comments, backslashes, "<", @import, @charset, @namespace,
@font-face, url(), image-set(), src(), expression(), position: fixed,
behavior and -moz-binding). A stylesheet here must not need any of them:
apart from its comments, it has to arrive on the blog exactly as written.
The tests also check that every token it uses exists, and that the braces
balance.
"""

import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SHEETS = sorted(HERE.parent.glob("*.css"))

COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
FORBIDDEN = {
    "a backslash (escapes are removed)": re.compile(r"\\"),
    "'<' (it could close the <style> element)": re.compile(r"<"),
    "@import, @charset or @namespace": re.compile(r"@(?:import|charset|namespace)\b", re.IGNORECASE),
    "@font-face (blogs use system fonts)": re.compile(r"@font-face", re.IGNORECASE),
    "url()": re.compile(r"\burl\s*\(", re.IGNORECASE),
    "image-set() or src()": re.compile(r"(?<![-\w])(?:-webkit-)?(?:image-set|src)\s*\(", re.IGNORECASE),
    "expression()": re.compile(r"\bexpression\s*\(", re.IGNORECASE),
    "position: fixed": re.compile(r"\bposition\s*:\s*fixed", re.IGNORECASE),
    "behavior or -moz-binding": re.compile(r"(?<![-\w])(?:-moz-binding|behaviou?r)\s*:", re.IGNORECASE),
}

# The custom properties the hosted theme defines (and ../ssg/, ../hugo/ and
# ../astro/ with it). A sheet may use these and any it defines itself.
TOKENS = {
    "--paper", "--ink", "--muted", "--rule", "--ocean", "--accent", "--accent-strong", "--accent-dim",
    "--on-accent", "--shade", "--font-body", "--font-meta", "--measure",
}  # fmt: skip


def ids(path: Path) -> str:
    return path.name


def test_there_are_examples():
    assert len(SHEETS) >= 3


@pytest.mark.parametrize("sheet", SHEETS, ids=ids)
def test_nothing_ortszeit_would_remove(sheet):
    css = COMMENT.sub("", sheet.read_text(encoding="utf-8"))
    found = [what for what, pattern in FORBIDDEN.items() if pattern.search(css)]
    assert not found, f"{sheet.name} uses {', '.join(found)}"


@pytest.mark.parametrize("sheet", SHEETS, ids=ids)
def test_starts_with_a_header_comment(sheet):
    assert re.match(r"/\* \w+: ", sheet.read_text(encoding="utf-8")), "begin with /* Name: what it does"


@pytest.mark.parametrize("sheet", SHEETS, ids=ids)
def test_only_known_tokens(sheet):
    css = COMMENT.sub("", sheet.read_text(encoding="utf-8"))
    defined = set(re.findall(r"(--[\w-]+)\s*:", css))
    used = set(re.findall(r"var\(\s*(--[\w-]+)", css))
    assert used <= TOKENS | defined, used - TOKENS - defined
    assert defined <= TOKENS, f"not a theme token (fine, but then say so here): {defined - TOKENS}"


@pytest.mark.parametrize("sheet", SHEETS, ids=ids)
def test_balanced(sheet):
    css = COMMENT.sub("", sheet.read_text(encoding="utf-8"))
    depth = 0
    for char in css:
        depth += {"{": 1, "}": -1}.get(char, 0)
        assert depth >= 0
    assert depth == 0
    assert css.count('"') % 2 == 0


@pytest.mark.parametrize("sheet", SHEETS, ids=ids)
def test_dark_mode_is_handled(sheet):
    """The theme switches its tokens for dark systems. A sheet that sets its
    own colours must do the same, or say it is dark (or light) on purpose."""
    css = COMMENT.sub("", sheet.read_text(encoding="utf-8"))
    sets_colours = re.search(r"--(?:paper|ink)\s*:", css)
    assert not sets_colours or "prefers-color-scheme" in css or re.search(r"color-scheme\s*:\s*(dark|light)", css)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
