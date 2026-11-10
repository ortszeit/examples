#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest>=8", "httpx>=0.27", "pyyaml>=6.0", "keyring>=25"]
# ///
"""Tests for ortszeit.py. HTTP is mocked; no request leaves the machine.

uv run cli/tests/test_cli.py

The fixtures follow the response shapes in Ortszeit's API schema
(https://ortszeit.app/docs/api/).
"""

import importlib.util
import io
import json
import stat
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).resolve().parent
SAMPLE = HERE.parent.parent / "sample-export"
spec = importlib.util.spec_from_file_location("ortszeit_cli", HERE.parent / "ortszeit.py")
cli = importlib.util.module_from_spec(spec)
sys.modules["ortszeit_cli"] = cli
spec.loader.exec_module(cli)

BASE = "https://studio.test"
KEY = "oz_test_key"

QUOTA = {
    "tier": "free",
    "limits": {"trips": 3, "posts": 100, "images_per_post": 5, "storage_bytes": 1073741824},
    "usage": {"trips": 1, "posts": 12, "storage_bytes": 34567890},
}
BLOG = {
    "id": 1,
    "subdomain": "lena",
    "title": "Unterwegs mit Lena",
    "review_state": "approved",
    "canonical_base_url": "https://lena.ortszeit.app/",
    "primary_domain": "lena.ortszeit.app",
}
TRIPS = [
    {
        "id": 7,
        "slug": "venedig-2026",
        "title": "Venedig 2026",
        "started_on": "2026-10-03",
        "ended_on": None,
        "visibility": "public",
        "post_count": 3,
        "is_closed": False,
    },
    {
        "id": 8,
        "slug": "familie",
        "title": "Familie",
        "started_on": "2026-09-13",
        "ended_on": "2026-09-14",
        "visibility": "password",
        "post_count": 1,
        "is_closed": True,
    },
]


def page(results, next_url=None):
    return {"count": len(results), "next": next_url, "previous": None, "results": results}


def sample_zip(prefix: str = "blog/", change: dict | None = None, drop: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(SAMPLE.rglob("*")):
            rel = path.relative_to(SAMPLE).as_posix()
            if path.is_file() and rel != drop:
                archive.writestr(prefix + rel, (change or {}).get(rel, path.read_bytes()))
    return buffer.getvalue()


class Server:
    """A tiny fake of the documented endpoints. Records every request."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], list] = {}

    def on(self, method, path, *responses):
        self.routes[(method, path)] = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get((request.method, request.url.path))
        if not queue:
            return httpx.Response(404, json={"detail": "Not found."})
        response = queue[0] if len(queue) == 1 else queue.pop(0)
        return response(request) if callable(response) else response

    def calls(self):
        return [(r.method, r.url.path) for r in self.requests]


@pytest.fixture
def server(monkeypatch, tmp_path):
    fake = Server()
    monkeypatch.setattr(cli, "_transport", httpx.MockTransport(fake))
    fake.slept = []
    monkeypatch.setattr(cli, "_sleep", fake.slept.append)
    monkeypatch.setenv("ORTSZEIT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ORTSZEIT_BASE_URL", BASE)
    monkeypatch.setenv("ORTSZEIT_NO_KEYRING", "1")
    monkeypatch.delenv("ORTSZEIT_API_KEY", raising=False)
    return fake


@pytest.fixture
def logged_in(server, monkeypatch):
    monkeypatch.setenv("ORTSZEIT_API_KEY", KEY)
    return server


# --- login / logout -----------------------------------------------------------


def test_login_falls_back_to_a_private_file(server, monkeypatch, capsys):
    server.on("GET", "/api/account/quota/", httpx.Response(200, json=QUOTA))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: KEY + "\n")
    assert cli.main(["login"]) == 0
    assert server.requests[0].headers["Authorization"] == f"Api-Key {KEY}"
    stored = cli.credentials_file()
    assert json.loads(stored.read_text()) == {"keys": {BASE: KEY}}
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    assert "plan: free" in capsys.readouterr().out
    assert cli.load_key(BASE) == (KEY, str(stored))

    assert cli.main(["logout"]) == 0
    assert not stored.exists()
    assert cli.load_key(BASE) == (None, "")


def test_login_prefers_the_keyring(server, monkeypatch):
    class FakeKeyring:
        def __init__(self):
            self.store = {}

        def set_password(self, service, user, secret):
            self.store[(service, user)] = secret

        def get_password(self, service, user):
            return self.store.get((service, user))

        def delete_password(self, service, user):
            del self.store[(service, user)]

    ring = FakeKeyring()
    monkeypatch.setattr(cli, "_keyring", lambda: ring)
    server.on("GET", "/api/account/quota/", httpx.Response(200, json=QUOTA))
    monkeypatch.setattr(sys, "stdin", io.StringIO(KEY + "\n"))
    assert cli.main(["login", "--stdin"]) == 0
    assert ring.store == {("ortszeit", BASE): KEY}
    assert not cli.credentials_file().exists()
    assert cli.load_key(BASE) == (KEY, "keyring")


def test_broken_keyring_falls_back_to_file(server, monkeypatch):
    class Locked:
        def set_password(self, *a):
            raise RuntimeError("keychain locked")

        def get_password(self, *a):
            raise RuntimeError("keychain locked")

    monkeypatch.setattr(cli, "_keyring", lambda: Locked())
    assert "readable only by you" in cli.store_key(BASE, KEY)
    assert cli.load_key(BASE)[0] == KEY


def test_env_var_overrides_stored_key(server, monkeypatch):
    cli.store_key(BASE, "stored")
    monkeypatch.setenv("ORTSZEIT_API_KEY", "from-env")
    assert cli.load_key(BASE) == ("from-env", "ORTSZEIT_API_KEY")


def test_login_with_a_bad_key_stores_nothing(server, monkeypatch, capsys):
    server.on("GET", "/api/account/quota/", httpx.Response(401, json={"detail": "Invalid API key."}))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "wrong")
    assert cli.main(["login"]) == 1
    assert not cli.credentials_file().exists()
    assert "Create a new key in Studio" in capsys.readouterr().err


# --- status / trips / posts ---------------------------------------------------


def test_status(logged_in, capsys):
    logged_in.on("GET", "/api/account/quota/", httpx.Response(200, json=QUOTA))
    logged_in.on("GET", "/api/blog/", httpx.Response(200, json=BLOG))
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Trips    1 of 3" in out and "Posts    12 of 100" in out
    assert "34.6 MB of 1.1 GB" in out
    assert "https://lena.ortszeit.app/" in out


def test_not_logged_in(server, capsys):
    assert cli.main(["status"]) == 1
    assert "ortszeit login" in capsys.readouterr().err
    assert server.requests == []


def test_posts_follows_pages_and_filters_by_trip(logged_in, capsys):
    logged_in.on("GET", "/api/trips/", httpx.Response(200, json=page(TRIPS)))
    first = page(
        [{"id": 1, "trip": 7, "title": "Ankunft", "slug": "ankunft", "published_at": "2026-10-03T20:00:00Z"}],
        next_url=f"{BASE}/api/posts/?page=2&trip=7",
    )
    second = page([{"id": 2, "trip": 7, "title": "", "slug": "2026-10-04", "published_at": None}])
    logged_in.on("GET", "/api/posts/", httpx.Response(200, json=first), httpx.Response(200, json=second))
    assert cli.main(["posts", "--trip", "venedig-2026"]) == 0
    post_requests = [r for r in logged_in.requests if r.url.path == "/api/posts/"]
    assert [dict(r.url.params) for r in post_requests] == [{"page": "1", "trip": "7"}, {"page": "2", "trip": "7"}]
    out = capsys.readouterr().out
    assert "2026-10-03" in out and "draft" in out and "2026-10-04" in out


def test_unknown_trip_slug(logged_in, capsys):
    logged_in.on("GET", "/api/trips/", httpx.Response(200, json=page(TRIPS)))
    assert cli.main(["posts", "--trip", "nope"]) == 1
    assert "venedig-2026, familie" in capsys.readouterr().err


# --- error handling -----------------------------------------------------------


def test_401_says_create_a_new_key(logged_in, capsys):
    logged_in.on("GET", "/api/trips/", httpx.Response(401, json={"detail": "Invalid API key."}))
    assert cli.main(["trips"]) == 1
    err = capsys.readouterr().err
    assert "revoked" in err and f"Create a new key in Studio ({BASE})" in err


def test_403_quota_shows_the_server_message(logged_in, tmp_path, capsys):
    note = tmp_path / "n.md"
    note.write_text("Hallo.\n")
    detail = "Your plan allows 100 posts. You have 100."
    logged_in.on("POST", "/api/posts/", httpx.Response(403, json={"detail": detail}))
    assert cli.main(["post", str(note)]) == 1
    assert f"Plan limit reached: {detail}" in capsys.readouterr().err
    assert len(logged_in.requests) == 1  # never retried


def test_429_waits_for_retry_after(logged_in, capsys):
    logged_in.on(
        "GET",
        "/api/trips/",
        httpx.Response(429, headers={"Retry-After": "7"}, json={"detail": "Slow down."}),
        httpx.Response(200, json=page(TRIPS)),
    )
    assert cli.main(["trips"]) == 0
    assert logged_in.slept == [7.0]
    assert "venedig-2026" in capsys.readouterr().out


def test_429_with_a_long_wait_gives_up(logged_in, capsys):
    logged_in.on("GET", "/api/trips/", httpx.Response(429, headers={"Retry-After": "3600"}))
    assert cli.main(["trips"]) == 1
    assert "Try again in 3600 seconds" in capsys.readouterr().err
    assert logged_in.slept == []


def test_post_is_not_retried_on_5xx(logged_in, tmp_path):
    note = tmp_path / "n.md"
    note.write_text("Hallo.\n")
    logged_in.on("POST", "/api/posts/", httpx.Response(502))
    assert cli.main(["post", str(note)]) == 1
    assert len(logged_in.requests) == 1


def test_get_is_retried_on_5xx(logged_in):
    logged_in.on("GET", "/api/trips/", httpx.Response(503), httpx.Response(200, json=page([])))
    assert cli.main(["trips"]) == 0
    assert logged_in.slept == [2.0]


# --- export / pull ------------------------------------------------------------


def job(status, **extra):
    return {
        "id": 4,
        "status": status,
        "kind": "free",
        "requested_at": "2026-11-10T08:00:00Z",
        "completed_at": None,
        "download_url": "",
        "error": "",
        **extra,
    }


def export_server(server, archive: bytes):
    done = job("done", download_url="https://files.test/export/4.zip?sig=abc")
    server.on("POST", "/api/export/", httpx.Response(202, json=job("pending")))
    server.on("GET", "/api/export/", httpx.Response(200, json=[job("running")]), httpx.Response(200, json=[done]))
    server.on("GET", "/export/4.zip", httpx.Response(200, content=archive))


def test_export_polls_then_downloads_without_the_key(logged_in, tmp_path):
    archive = sample_zip()
    export_server(logged_in, archive)
    out = tmp_path / "out.zip"
    assert cli.main(["export", "--out", str(out)]) == 0
    assert out.read_bytes() == archive
    assert logged_in.slept == [2.0, 3.0]  # polite, growing intervals
    download = logged_in.requests[-1]
    assert download.url.host == "files.test"
    assert "Authorization" not in download.headers  # a signed link: the key stays with the API


def test_export_joins_a_running_one(logged_in, tmp_path):
    logged_in.on("POST", "/api/export/", httpx.Response(409, json={"detail": "An export is already running."}))
    logged_in.on(
        "GET",
        "/api/export/",
        httpx.Response(200, json=[job("running")]),
        httpx.Response(200, json=[job("done", download_url="/dl/4.zip")]),  # relative link: resolved on the server
    )
    logged_in.on("GET", "/dl/4.zip", httpx.Response(200, content=sample_zip()))
    assert cli.main(["export", "--out", str(tmp_path / "x.zip")]) == 0
    assert (tmp_path / "x.zip").exists()
    assert logged_in.calls()[0] == ("POST", "/api/export/")
    assert logged_in.requests[-1].url == httpx.URL(f"{BASE}/dl/4.zip")


def test_failed_export(logged_in, capsys):
    logged_in.on("POST", "/api/export/", httpx.Response(202, json=job("pending")))
    logged_in.on("GET", "/api/export/", httpx.Response(200, json=[job("failed", error="disk full")]))
    assert cli.main(["export"]) == 1
    assert "disk full" in capsys.readouterr().err


def test_pull_writes_the_portable_layout(logged_in, tmp_path, capsys):
    export_server(logged_in, sample_zip())
    target = tmp_path / "blog"
    assert cli.main(["pull", str(target)]) == 0
    assert (target / "blog.yaml").read_bytes() == (SAMPLE / "blog.yaml").read_bytes()
    assert (target / "trips/venedig-2026/media/IMG_4100.jpg").is_file()
    assert "unchanged" in capsys.readouterr().out


def test_pull_is_idempotent_and_only_touches_changes(server, tmp_path):
    target = tmp_path / "blog"
    first = tmp_path / "1.zip"
    first.write_bytes(sample_zip())
    assert cli.sync_dir(target, cli.read_archive(first))["new"] > 5
    photo = target / "trips/venedig-2026/media/IMG_4100.jpg"
    mtime = photo.stat().st_mtime_ns
    (target / "notes.txt").write_text("mine")  # not from Ortszeit: never touched

    again = cli.sync_dir(target, cli.read_archive(first))
    assert again["new"] == again["changed"] == again["removed"] == 0
    assert photo.stat().st_mtime_ns == mtime

    second = tmp_path / "2.zip"
    second.write_bytes(
        sample_zip(
            change={"blog.yaml": b"ortszeit_schema: 1\ntitle: Neu\n"}, drop="posts/2026-11-10-entwurf-packliste.md"
        )
    )
    counts = cli.sync_dir(target, cli.read_archive(second))
    assert (counts["changed"], counts["removed"], counts["new"]) == (1, 1, 0)
    assert not (target / "posts/2026-11-10-entwurf-packliste.md").exists()
    assert (target / "notes.txt").read_text() == "mine"
    assert photo.stat().st_mtime_ns == mtime


def test_pull_from_a_local_zip_needs_no_key(server, tmp_path):
    archive = tmp_path / "e.zip"
    archive.write_bytes(sample_zip(prefix=""))  # archives without the blog/ prefix work too
    assert cli.main(["pull", str(tmp_path / "out"), "--zip", str(archive)]) == 0
    assert (tmp_path / "out/blog.yaml").is_file()
    assert server.requests == []


def test_pull_refuses_unsafe_paths(server, tmp_path, capsys):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("blog/blog.yaml", "ortszeit_schema: 1\n")
        z.writestr("blog/../../escape.txt", "x")
    assert cli.main(["pull", str(tmp_path / "out"), "--zip", str(archive)]) == 1
    assert "unsafe path" in capsys.readouterr().err
    assert not (tmp_path / "escape.txt").exists()


# --- post ---------------------------------------------------------------------


@pytest.fixture
def post_file(tmp_path):
    (tmp_path / "media").mkdir()
    (tmp_path / "media/a.jpg").write_bytes(b"\xff\xd8first")
    (tmp_path / "media/b.jpg").write_bytes(b"\xff\xd8second")
    path = tmp_path / "2026-11-10-hafen.md"
    path.write_text(
        "---\n"
        "title: Hafen\n"
        "date: 2026-11-10T14:20:00+01:00\n"
        "time_zone: Europe/Berlin\n"
        "draft: false\n"
        "place: {name: Hafen, osm: null, lat: 54.1, lon: 12.1}\n"
        "visibility: unlisted\n"
        "media:\n"
        "  - file: media/a.jpg\n"
        "    alt: Fähre\n"
        "  - file: media/b.jpg\n"
        "---\n\n"
        "Regen bis vier.\n"
    )
    return path


def test_post_dry_run_sends_nothing(server, post_file, capsys):
    assert cli.main(["post", str(post_file), "--trip", "venedig-2026", "--publish", "--dry-run"]) == 0
    assert server.requests == []
    out = capsys.readouterr()
    assert '"title": "Hafen"' in out.out and '"visibility": "unlisted"' in out.out
    assert "file=a.jpg" in out.out and "alt_text='Fähre'" in out.out
    assert "POST /api/posts/<new id>/publish/" in out.out
    assert "Dry run: nothing was sent." in out.out
    assert "place is not sent" in out.err


def test_post_order_create_upload_publish(logged_in, post_file):
    created = {"id": 31, "status": "draft", "canonical_url": "", "slug": "hafen"}
    logged_in.on("GET", "/api/trips/", httpx.Response(200, json=page(TRIPS)))
    logged_in.on("POST", "/api/posts/", httpx.Response(201, json=created))
    logged_in.on("POST", "/api/media/", httpx.Response(201, json={"id": 1}))
    logged_in.on(
        "POST",
        "/api/posts/31/publish/",
        httpx.Response(200, json={**created, "status": "published", "canonical_url": "https://x/"}),
    )
    assert cli.main(["post", str(post_file), "--trip", "venedig-2026", "--publish"]) == 0
    assert logged_in.calls() == [
        ("GET", "/api/trips/"),
        ("POST", "/api/posts/"),
        ("POST", "/api/media/"),
        ("POST", "/api/media/"),
        ("POST", "/api/posts/31/publish/"),
    ]
    body = json.loads(logged_in.requests[1].content)
    assert body == {
        "title": "Hafen",
        "body_md": "Regen bis vier.\n",
        "time_zone": "Europe/Berlin",
        "visibility": "unlisted",
        "trip": 7,
    }
    first, second = (r.content for r in logged_in.requests[2:4])
    assert b"first" in first and b'name="sort_order"\r\n\r\n0' in first and "Fähre".encode() in first
    assert b"second" in second and b'name="sort_order"\r\n\r\n1' in second
    assert b'name="post"\r\n\r\n31' in second


def test_post_stays_a_draft_by_default(logged_in, post_file, capsys):
    logged_in.on("POST", "/api/posts/", httpx.Response(201, json={"id": 31, "status": "draft"}))
    logged_in.on("POST", "/api/media/", httpx.Response(201, json={"id": 1}))
    assert cli.main(["post", str(post_file)]) == 0
    assert ("POST", "/api/posts/31/publish/") not in logged_in.calls()
    captured = capsys.readouterr()
    assert "Saved as draft: post 31" in captured.out
    assert "stays a draft" in captured.err


def test_post_upload_failure_says_what_exists(logged_in, post_file, capsys):
    logged_in.on("POST", "/api/posts/", httpx.Response(201, json={"id": 31, "status": "draft"}))
    logged_in.on(
        "POST",
        "/api/media/",
        httpx.Response(201, json={"id": 1}),
        httpx.Response(403, json={"detail": "Storage full: 1 GB of 1 GB used."}),
    )
    assert cli.main(["post", str(post_file), "--publish"]) == 1
    err = capsys.readouterr().err
    assert "Storage full" in err and "Draft 31 exists with 1 of 2 photos" in err
    assert ("POST", "/api/posts/31/publish/") not in logged_in.calls()


def test_post_missing_photo(server, tmp_path, capsys):
    path = tmp_path / "p.md"
    path.write_text("---\nmedia:\n  - file: media/nope.jpg\n---\nText\n")
    assert cli.main(["post", str(path), "--dry-run"]) == 1
    assert "media/nope.jpg not found" in capsys.readouterr().err


def test_post_password_visibility_is_refused(server, tmp_path, capsys):
    path = tmp_path / "p.md"
    path.write_text("---\nvisibility: password\n---\nText\n")
    assert cli.main(["post", str(path), "--dry-run"]) == 1
    assert "set on a trip in Studio" in capsys.readouterr().err


# --- open ---------------------------------------------------------------------


def test_open_uses_the_blog_address(logged_in, monkeypatch):
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    logged_in.on("GET", "/api/blog/", httpx.Response(200, json=BLOG))
    assert cli.main(["open"]) == 0
    assert cli.main(["open", "--studio"]) == 0
    assert opened == ["https://lena.ortszeit.app/", f"{BASE}/"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
