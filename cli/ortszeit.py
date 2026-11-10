#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "httpx>=0.27",
#     "pyyaml>=6.0",
#     "keyring>=25",
# ]
# ///
"""A small command-line client for your Ortszeit account.

    uv run cli/ortszeit.py login
    uv run cli/ortszeit.py status
    uv run cli/ortszeit.py pull blog

It talks to the hosted service through the API documented at
https://ortszeit.app/docs/api/, authenticated with a user API key
(``Authorization: Api-Key <key>``) that you create in Studio. It reads and
writes the portable format (https://ortszeit.app/docs/portable-format/).
Nothing else is sent anywhere: no telemetry, no update check.

The file is laid out in the order you would read it:

1. where the API key lives (env var, system keyring, or a 0600 file)
2. the HTTP client: auth header, pagination, 401/403/429 handling
3. export and pull (download the archive, unpack only what changed)
4. post (create a draft from a Markdown file, upload photos, publish)
5. the command line
"""

from __future__ import annotations

import argparse
import contextlib
import email.utils
import getpass
import hashlib
import json
import mimetypes
import os
import sys
import tempfile
import time
import webbrowser
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit

import httpx
import yaml

try:  # keyring is optional: without it (or without a backend) the key goes to a 0600 file
    import keyring
    from keyring.backends import fail
    from keyring.errors import KeyringError
except ImportError:  # pragma: no cover
    keyring = None
    KeyringError = RuntimeError
# What a keyring backend raises when it is there but cannot be used (locked, no D-Bus, ...).
KEYRING_ERRORS = (KeyringError, OSError, RuntimeError)

DEFAULT_BASE_URL = "https://studio.ortszeit.app"
SERVICE = "ortszeit"  # keyring service name
MANIFEST = ".ortszeit-pull.json"  # what the last `pull` wrote, so the next one can tidy up
USER_AGENT = "ortszeit-examples-cli/0.1"

# Tests swap these for an httpx.MockTransport and a sleep that only records.
_transport: httpx.BaseTransport | None = None
_sleep = time.sleep


class CliError(Exception):
    """An error with a message meant for the user; main() prints it and exits 1."""


def say(message: str) -> None:
    print(message, file=sys.stderr)


# ---------------------------------------------------------------------------
# 1. The API key
# ---------------------------------------------------------------------------


def config_dir() -> Path:
    if override := os.environ.get("ORTSZEIT_CONFIG_DIR"):
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ortszeit"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "ortszeit"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ortszeit"


def credentials_file() -> Path:
    return config_dir() / "credentials.json"


def _keyring():
    """The keyring module if a usable backend exists, else None (headless Linux, CI, ...)."""
    if os.environ.get("ORTSZEIT_NO_KEYRING") or keyring is None:
        return None
    try:
        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
    except KEYRING_ERRORS:
        return None
    return keyring


def _read_file_keys() -> dict[str, str]:
    try:
        return json.loads(credentials_file().read_text())["keys"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _write_file_keys(keys: dict[str, str]) -> None:
    path = credentials_file()
    if not keys:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump({"keys": keys}, handle, indent=2)
    os.chmod(path, 0o600)  # also when the file already existed with wider permissions


def store_key(base_url: str, key: str) -> str:
    """Save the key for this server; returns where it went."""
    if (kr := _keyring()) is not None:
        try:
            kr.set_password(SERVICE, base_url, key)
            keys = _read_file_keys()  # don't leave an older copy behind in the file
            if keys.pop(base_url, None) is not None:
                _write_file_keys(keys)
            return "the system keyring"
        except KEYRING_ERRORS as exc:
            say(f"note: keyring not usable ({exc.__class__.__name__}); using a file instead")
    keys = _read_file_keys()
    keys[base_url] = key
    _write_file_keys(keys)
    return f"{credentials_file()} (readable only by you)"


def load_key(base_url: str) -> tuple[str | None, str]:
    """Return (key, source). ORTSZEIT_API_KEY wins over anything stored."""
    if key := os.environ.get("ORTSZEIT_API_KEY", "").strip():
        return key, "ORTSZEIT_API_KEY"
    if (kr := _keyring()) is not None:
        with contextlib.suppress(*KEYRING_ERRORS):
            if key := kr.get_password(SERVICE, base_url):
                return key, "keyring"
    if key := _read_file_keys().get(base_url):
        return key, str(credentials_file())
    return None, ""


def forget_key(base_url: str) -> bool:
    removed = False
    if (kr := _keyring()) is not None:
        with contextlib.suppress(*KEYRING_ERRORS):  # nothing stored there
            kr.delete_password(SERVICE, base_url)
            removed = True
    keys = _read_file_keys()
    if keys.pop(base_url, None) is not None:
        _write_file_keys(keys)
        removed = True
    return removed


# ---------------------------------------------------------------------------
# 2. The HTTP client
# ---------------------------------------------------------------------------


class ApiError(CliError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _detail(response: httpx.Response) -> str:
    """The server's own message: {"detail": ...} or DRF field errors {"field": ["msg"]}."""
    try:
        body = response.json()
    except ValueError:
        return response.reason_phrase or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        if isinstance(body.get("detail"), str):
            return body["detail"]
        for field, messages in body.items():
            message = messages[0] if isinstance(messages, list) and messages else messages
            return f"{field}: {message}"
    return str(body)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After", "").strip()
    if value.isdigit():
        return float(value)
    try:
        moment = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (moment - datetime.now(UTC)).total_seconds())


class Api:
    MAX_ATTEMPTS = 4
    MAX_WAIT = 300  # seconds; a longer Retry-After is reported instead of slept through

    def __init__(self, base_url: str, key: str | None):
        self.base_url = base_url.rstrip("/")
        self.key = key
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if key:
            headers["Authorization"] = f"Api-Key {key}"
        self.client = httpx.Client(base_url=self.base_url, headers=headers, timeout=60, transport=_transport)

    def request(self, method: str, path: str, *, allow: tuple[int, ...] = (), **kwargs) -> httpx.Response:
        if not self.key:
            raise CliError("Not logged in. Run `ortszeit login`, or set ORTSZEIT_API_KEY.")
        delay = 2.0
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            last = attempt == self.MAX_ATTEMPTS
            try:
                response = self.client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                # Only GETs are retried: a POST that reached the server may have created something.
                if method == "GET" and not last:
                    _sleep(delay)
                    delay *= 2
                    continue
                raise CliError(f"Cannot reach {self.base_url}: {exc}") from exc
            if response.status_code == 429 and not last:
                wait = _retry_after(response)
                wait = delay if wait is None else wait
                if wait > self.MAX_WAIT:
                    raise ApiError(429, f"Rate limited. Try again in {int(wait)} seconds.")
                say(f"Rate limited, waiting {wait:.0f}s as the server asked ...")
                _sleep(wait)
                delay *= 2
                continue
            if response.status_code >= 500 and method == "GET" and not last:
                _sleep(delay)
                delay *= 2
                continue
            break
        if response.is_success or response.status_code in allow:
            return response
        raise self._error(response)

    def _error(self, response: httpx.Response) -> ApiError:
        status, detail = response.status_code, _detail(response)
        if status == 401:
            return ApiError(
                401,
                "The API key was not accepted: it was revoked, has expired, or is mistyped.\n"
                f"Create a new key in Studio ({self.base_url}) and run `ortszeit login` again.",
            )
        if status == 403:  # a plan limit: the server names it, we don't paraphrase
            return ApiError(403, f"Plan limit reached: {detail}")
        if status == 429:
            return ApiError(429, f"Rate limited: {detail}")
        return ApiError(status, f"{detail} (HTTP {status})")

    def get(self, path: str, **params) -> dict | list:
        return self.request("GET", path, params={k: v for k, v in params.items() if v is not None}).json()

    def get_all(self, path: str, **params) -> list[dict]:
        """Every item of a paginated list ({count, next, previous, results})."""
        items, page = [], 1
        while True:
            data = self.get(path, page=page, **params)
            items.extend(data["results"])
            if not data.get("next"):
                return items
            page += 1

    def trip_id(self, slug: str) -> int:
        trips = self.get_all("/api/trips/")
        for trip in trips:
            if trip["slug"] == slug:
                return trip["id"]
        known = ", ".join(t["slug"] for t in trips) or "none"
        raise CliError(f"No trip with the slug {slug!r}. Your trips: {known}")


# ---------------------------------------------------------------------------
# 3. Export and pull
# ---------------------------------------------------------------------------


def run_export(api: Api, timeout: float) -> dict:
    """Start an export (or join the one already running) and wait until it is done."""
    response = api.request("POST", "/api/export/", allow=(409,))
    if response.status_code == 409:  # one export at a time per blog: follow the running one
        jobs = [j for j in api.get("/api/export/") if j["status"] in ("pending", "running")]
        if not jobs:
            raise CliError(f"The server refused a new export: {_detail(response)}")
        job = max(jobs, key=lambda j: j["requested_at"])
        say(f"An export is already running (job {job['id']}); waiting for it.")
    else:
        job = response.json()
        say(f"Export started (job {job['id']}).")
    delay, waited, shown = 2.0, 0.0, None
    while job["status"] in ("pending", "running"):
        if job["status"] != shown:
            say(f"  {job['status']} ...")
            shown = job["status"]
        if waited >= timeout:
            raise CliError(f"The export is still {job['status']} after {int(waited)}s. Run the command again later.")
        _sleep(delay)
        waited += delay
        delay = min(delay * 1.5, 30.0)  # polite: a large blog takes minutes, not seconds
        job = next((j for j in api.get("/api/export/") if j["id"] == job["id"]), job)
    if job["status"] != "done":
        raise CliError(f"The export failed: {job.get('error') or 'no reason given'}")
    return job


def download(api: Api, job: dict, out: Path) -> Path:
    """Fetch the archive from the job's signed link. The link needs no login, so no key is sent."""
    url = urljoin(api.base_url + "/", job["download_url"])
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    with (
        httpx.Client(transport=_transport, timeout=300, follow_redirects=True) as plain,
        plain.stream("GET", url, headers={"User-Agent": USER_AGENT}) as response,
    ):
        if response.status_code in (403, 404, 410):
            raise CliError("The download link has expired (links last seven days). Start a new export.")
        if not response.is_success:
            raise CliError(f"Download failed: HTTP {response.status_code}")
        with part.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
    part.replace(out)
    return out


def read_archive(zip_path: Path) -> dict[str, bytes]:
    """{path relative to blog/: bytes}. Accepts archives with or without the blog/ prefix."""
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise CliError(f"{zip_path} is not a zip archive") from exc
    files: dict[str, bytes] = {}
    with archive:
        names = [n for n in archive.namelist() if not n.endswith("/")]
        prefix = "blog/" if names and all(n.startswith("blog/") for n in names) else ""
        for name in names:
            parts = PurePosixPath(name).parts
            if name.startswith("/") or ".." in parts or ":" in parts[0]:
                raise CliError(f"Refusing unsafe path in archive: {name}")
            files[name[len(prefix) :]] = archive.read(name)
    if "blog.yaml" not in files:
        raise CliError(f"{zip_path} has no blog.yaml; it is not an Ortszeit export")
    return files


def sync_dir(target: Path, files: dict[str, bytes]) -> dict[str, int]:
    """Write files into target, touching only what changed.

    Files the previous pull wrote and this archive no longer has (a deleted
    post, say) are removed. Anything else in the directory is left alone.
    """
    target.mkdir(parents=True, exist_ok=True)
    manifest = target / MANIFEST
    try:
        previous = set(json.loads(manifest.read_text())["files"])
    except (OSError, ValueError, KeyError, TypeError):
        previous = set()
    counts = {"new": 0, "changed": 0, "unchanged": 0, "removed": 0}
    for rel, data in sorted(files.items()):
        path = target / rel
        if path.is_file():
            if hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(data).digest():
                counts["unchanged"] += 1
                continue
            counts["changed"] += 1
        else:
            counts["new"] += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic: a reader never sees half a file
    for rel in sorted(previous - files.keys()):
        path = target / rel
        if path.is_file():
            path.unlink()
            counts["removed"] += 1
            parent = path.parent
            while parent != target and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
    manifest.write_text(json.dumps({"files": sorted(files)}, indent=1) + "\n")
    return counts


# ---------------------------------------------------------------------------
# 4. post: a Markdown file in the portable format -> a post on the blog
# ---------------------------------------------------------------------------


def read_post_file(path: Path) -> tuple[dict, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(f"Cannot read {path}: {exc.strerror}") from exc
    if not text.startswith("---"):
        return {}, text.strip() + "\n"
    try:
        _, head, body = text.split("---", 2)
        meta = yaml.safe_load(head) or {}
    except (ValueError, yaml.YAMLError) as exc:
        raise CliError(f"{path}: the front matter is not valid YAML") from exc
    if not isinstance(meta, dict):
        raise CliError(f"{path}: the front matter must be a mapping")
    return meta, body.strip() + "\n"


def plan_post(path: Path, trip: str | None, publish: bool) -> dict:
    """Turn the file into the requests `post` will send. No network involved."""
    meta, body = read_post_file(path)
    notes = []
    payload: dict = {"title": meta.get("title") or "", "body_md": body}
    if meta.get("time_zone"):
        payload["time_zone"] = str(meta["time_zone"])
    if "hide_location" in meta:
        payload["hide_location"] = bool(meta["hide_location"])
    visibility = meta.get("visibility")
    if visibility == "password":
        raise CliError("visibility: password is set on a trip in Studio, not on a post. Use public or unlisted.")
    if visibility is not None:
        if visibility not in ("public", "unlisted"):
            raise CliError(f"visibility must be public or unlisted, not {visibility!r}")
        payload["visibility"] = visibility
    if "place" in meta:
        notes.append("place is not sent: the server names the place from your photos' GPS.")
    if "date" in meta:
        notes.append("date is not sent: on publish the server dates the post from the photos' EXIF (or now).")
    if meta.get("draft") is False and not publish:
        notes.append("draft: false in the file, but the post stays a draft unless you pass --publish.")
    media = []
    for index, item in enumerate(meta.get("media") or []):
        if not isinstance(item, dict) or not item.get("file"):
            raise CliError(f"media[{index}] needs a file: key")
        file = (path.parent / str(item["file"])).resolve()
        if not file.is_file():
            raise CliError(f"media[{index}]: {item['file']} not found next to {path.name}")
        media.append({"path": file, "alt_text": item.get("alt") or "", "sort_order": index})
    return {"payload": payload, "media": media, "trip": trip, "publish": publish, "notes": notes}


def show_plan(plan: dict) -> None:
    payload = dict(plan["payload"])
    if plan["trip"]:
        payload["trip"] = f"<id of trip {plan['trip']!r}, looked up when sending>"
    if len(payload["body_md"]) > 200:
        payload["body_md"] = payload["body_md"][:200] + f"... ({len(plan['payload']['body_md'])} characters)"
    print("1. POST /api/posts/ (creates a draft)")
    print("   " + json.dumps(payload, ensure_ascii=False, indent=2).replace("\n", "\n   "))
    for step, item in enumerate(plan["media"], start=2):
        size = item["path"].stat().st_size
        print(
            f"{step}. POST /api/media/ file={item['path'].name} ({size / 1e6:.1f} MB) post=<new id>"
            f" sort_order={item['sort_order']} alt_text={item['alt_text']!r}"
        )
    if plan["publish"]:
        print(f"{len(plan['media']) + 2}. POST /api/posts/<new id>/publish/")


def send_post(api: Api, plan: dict) -> dict:
    """Create the draft, upload every photo, then publish: the documented order.

    Publishing before the photos are there would date the post wrongly, and
    neither create nor upload is safe to repeat, so on a failure halfway we
    stop and say exactly what exists.
    """
    payload = dict(plan["payload"])
    if plan["trip"]:
        payload["trip"] = api.trip_id(plan["trip"])
    post = api.request("POST", "/api/posts/", json=payload).json()
    say(f"Created draft {post['id']}.")
    for done, item in enumerate(plan["media"]):
        mime = mimetypes.guess_type(item["path"].name)[0] or "application/octet-stream"
        try:
            api.request(
                "POST",
                "/api/media/",
                files={"file": (item["path"].name, item["path"].read_bytes(), mime)},
                data={"post": str(post["id"]), "alt_text": item["alt_text"], "sort_order": str(item["sort_order"])},
            )
        except CliError as exc:
            raise CliError(
                f"{exc}\nDraft {post['id']} exists with {done} of {len(plan['media'])} photos. "
                "Finish it in Studio rather than running this again, or you get a second draft."
            ) from exc
        say(f"Uploaded {item['path'].name}.")
    if plan["publish"]:
        post = api.request("POST", f"/api/posts/{post['id']}/publish/").json()
    return post


# ---------------------------------------------------------------------------
# 5. The command line
# ---------------------------------------------------------------------------


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "unlimited"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return ""


def _limit(value) -> str:
    return "unlimited" if value is None else str(value)


def cmd_login(api: Api, args) -> int:
    if os.environ.get("ORTSZEIT_API_KEY"):
        say("note: ORTSZEIT_API_KEY is set and will be used instead of the key you store now.")
    if args.stdin:
        key = sys.stdin.readline().strip()
    else:
        say(f"Create an API key in Studio ({api.base_url}), then paste it here. Input is hidden.")
        key = getpass.getpass("API key: ").strip()
    if not key:
        raise CliError("No key entered.")
    checked = Api(api.base_url, key)
    quota = checked.get("/api/account/quota/")  # cheap, and tells us the key works
    where = store_key(api.base_url, key)
    print(f"Logged in to {api.base_url} (plan: {quota['tier']}). Key stored in {where}.")
    return 0


def cmd_logout(api: Api, args) -> int:
    if forget_key(api.base_url):
        print(f"Removed the stored key for {api.base_url}.")
    else:
        print(f"No key was stored for {api.base_url}.")
    print("The key itself still works until you revoke it in Studio.")
    if os.environ.get("ORTSZEIT_API_KEY"):
        print("ORTSZEIT_API_KEY is still set in your environment.")
    return 0


def cmd_status(api: Api, args) -> int:
    quota = api.get("/api/account/quota/")
    blog = api.request("GET", "/api/blog/", allow=(404,))
    blog = blog.json() if blog.status_code == 200 else None
    if args.json:
        print(json.dumps({"quota": quota, "blog": blog}, indent=2))
        return 0
    limits, usage = quota["limits"], quota["usage"]
    if blog:
        print(f"Blog     {blog['title']}  {blog['canonical_base_url']}")
        if blog.get("review_state") not in (None, "approved"):
            print(f"Review   {blog['review_state']}")
    else:
        print("Blog     none yet; create it in Studio")
    print(f"Plan     {quota['tier']}")
    print(f"Trips    {usage['trips']} of {_limit(limits.get('trips'))}")
    print(f"Posts    {usage['posts']} of {_limit(limits.get('posts'))}")
    print(f"Storage  {_fmt_bytes(usage['storage_bytes'])} of {_fmt_bytes(limits.get('storage_bytes'))}")
    print(f"Photos   up to {_limit(limits.get('images_per_post'))} per post")
    print(f"Key      from {args.key_source}")
    return 0


def cmd_trips(api: Api, args) -> int:
    trips = api.get_all("/api/trips/")
    if args.json:
        print(json.dumps(trips, indent=2, ensure_ascii=False))
        return 0
    for trip in trips:
        dates = f"{trip.get('started_on') or '?'} – {trip.get('ended_on') or 'open'}"
        print(f"{trip['slug']:<24} {dates:<25} {trip['visibility']:<9} {trip['post_count']:>4} posts  {trip['title']}")
    if not trips:
        print("No trips yet.")
    return 0


def cmd_posts(api: Api, args) -> int:
    trips = {t["id"]: t["slug"] for t in api.get_all("/api/trips/")}
    trip_id = None
    if args.trip:
        trip_id = next((i for i, s in trips.items() if s == args.trip), None)
        if trip_id is None:
            raise CliError(f"No trip with the slug {args.trip!r}. Your trips: {', '.join(trips.values()) or 'none'}")
    posts = api.get_all("/api/posts/", trip=trip_id, status=args.status)
    if args.json:
        print(json.dumps(posts, indent=2, ensure_ascii=False))
        return 0
    for post in posts:
        when = (post.get("published_at") or "")[:10] or "draft"
        print(f"{post['id']:>6}  {when:<10}  {trips.get(post.get('trip'), '-'):<20}  {post['title'] or post['slug']}")
    if not posts:
        print("No posts.")
    return 0


def cmd_export(api: Api, args) -> int:
    job = run_export(api, args.timeout)
    out = args.out or Path(f"ortszeit-export-{date.today().isoformat()}.zip")
    download(api, job, out)
    kind = "with photo files" if job.get("kind") == "full" else "texts and photo metadata, no photo files"
    print(f"Saved {out} ({kind}).")
    return 0


def cmd_pull(api: Api, args) -> int:
    if args.zip:
        files = read_archive(args.zip)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            files = read_archive(download(api, run_export(api, args.timeout), Path(tmp) / "export.zip"))
    counts = sync_dir(args.dir, files)
    print(
        f"{args.dir}: {counts['new']} new, {counts['changed']} changed, "
        f"{counts['removed']} removed, {counts['unchanged']} unchanged."
    )
    return 0


def cmd_post(api: Api, args) -> int:
    plan = plan_post(args.file, args.trip, args.publish)
    for note in plan["notes"]:
        say(f"note: {note}")
    if args.dry_run:
        show_plan(plan)
        print("Dry run: nothing was sent.")
        return 0
    post = send_post(api, plan)
    state = "Published" if post.get("status") == "published" else "Saved as draft"
    print(f"{state}: post {post['id']} {post.get('canonical_url') or ''}".rstrip())
    return 0


def cmd_open(api: Api, args) -> int:
    if args.studio:
        url = api.base_url + "/"
    else:
        response = api.request("GET", "/api/blog/", allow=(404,))
        if response.status_code == 404:
            raise CliError("You have no blog yet. `ortszeit open --studio` opens Studio, where you create it.")
        url = response.json()["canonical_base_url"]
    print(url)
    webbrowser.open(url)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ortszeit",
        description="Command-line client for your Ortszeit blog (uses your API key; no telemetry).",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("ORTSZEIT_BASE_URL", DEFAULT_BASE_URL),
        help=f"server address (env ORTSZEIT_BASE_URL, default {DEFAULT_BASE_URL})",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("login", help="store your API key (checked against the server first)")
    p.add_argument("--stdin", action="store_true", help="read the key from standard input instead of a prompt")
    p.set_defaults(func=cmd_login, needs_key=False)

    p = sub.add_parser("logout", help="forget the stored API key")
    p.set_defaults(func=cmd_logout, needs_key=False)

    p = sub.add_parser("status", help="your blog, plan, and usage against its limits")
    p.add_argument("--json", action="store_true", help="print the raw API responses")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("trips", help="list your trips")
    p.add_argument("--json", action="store_true", help="print the raw API objects")
    p.set_defaults(func=cmd_trips)

    p = sub.add_parser("posts", help="list your posts, drafts included")
    p.add_argument("--trip", metavar="SLUG", help="only posts in this trip")
    p.add_argument("--status", choices=["draft", "published"], help="only drafts or only published posts")
    p.add_argument("--json", action="store_true", help="print the raw API objects")
    p.set_defaults(func=cmd_posts)

    p = sub.add_parser("export", help="export the whole blog as a .zip in the portable format")
    p.add_argument("--out", type=Path, metavar="FILE", help="where to save it (default ortszeit-export-DATE.zip)")
    p.add_argument("--timeout", type=float, default=3600, help="seconds to wait for the export (default 3600)")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("pull", help="export and unpack into DIR, rewriting only files that changed")
    p.add_argument("dir", type=Path, metavar="DIR")
    p.add_argument("--zip", type=Path, metavar="FILE", help="unpack this export instead of making a new one")
    p.add_argument("--timeout", type=float, default=3600, help="seconds to wait for the export (default 3600)")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("post", help="create a post from a Markdown file with front matter (a draft by default)")
    p.add_argument("file", type=Path, metavar="FILE.md")
    p.add_argument("--trip", metavar="SLUG", help="put the post in this trip")
    p.add_argument("--publish", action="store_true", help="publish after the photos are uploaded")
    p.add_argument("--dry-run", action="store_true", help="show what would be sent, send nothing")
    p.set_defaults(func=cmd_post)

    p = sub.add_parser("open", help="open your blog in the browser")
    p.add_argument("--studio", action="store_true", help="open Studio instead")
    p.set_defaults(func=cmd_open)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    base_url = args.base_url.rstrip("/")
    if urlsplit(base_url).scheme not in ("http", "https"):
        say(f"error: --base-url must start with https:// (got {base_url!r})")
        return 2
    key, args.key_source = load_key(base_url)
    needs_key = getattr(args, "needs_key", True) and not (args.command == "open" and args.studio)
    needs_key = needs_key and not (args.command == "post" and args.dry_run)
    needs_key = needs_key and not (args.command == "pull" and args.zip)
    try:
        if needs_key and not key:
            raise CliError("Not logged in. Run `ortszeit login`, or set ORTSZEIT_API_KEY.")
        return args.func(Api(base_url, key), args)
    except CliError as exc:
        say(f"error: {exc}")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
