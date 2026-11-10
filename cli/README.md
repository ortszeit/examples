# ortszeit: a command-line client for your Ortszeit blog

> **Not usable yet.** This tool talks to the hosted Ortszeit service, which is
> in preview and not open for signup. Until launch there is no account to log
> in to. The code and its tests are here so you can read them now.

`ortszeit.py` is one Python file that uses Ortszeit's [API][api] with your
own API key. It lists your trips and posts, shows your plan and usage, exports
your blog, keeps a local copy of it in the [portable format][format], and
creates posts from Markdown files.

It is an example, not the planned `ortszeit` CLI for self-hosting. It sends
nothing anywhere except to the server you point it at: no telemetry, no update
check.

## Try it

You need [uv](https://docs.astral.sh/uv/). It reads the dependencies from the
script header (PEP 723): `httpx`, `pyyaml` and `keyring`.

```sh
uv run cli/ortszeit.py login            # paste the API key you created in Studio
uv run cli/ortszeit.py status
uv run cli/ortszeit.py pull blog && uv run astro/ortszeit_to_astro.py blog -o site
```

### Install it as a command

`uv tool install` needs a package, and this is a single file. Use an alias
instead (put it in `~/.zshrc` or `~/.bashrc`):

```sh
alias ortszeit='uv run --script /path/to/ortszeit-examples/cli/ortszeit.py'
```

Or link the file into a directory on your `PATH`. Its first line is
`#!/usr/bin/env -S uv run --script`, so it runs like any other command:

```sh
ln -s "$PWD/cli/ortszeit.py" ~/.local/bin/ortszeit
```

## Commands

| Command | What it does |
|---|---|
| `login [--stdin]` | Asks for your API key (hidden input), checks it against the server, and stores it. |
| `logout` | Forgets the stored key. The key keeps working until you revoke it in Studio. |
| `status [--json]` | Your blog's address, your plan, and usage against its limits (trips, posts, storage, photos per post). |
| `trips [--json]` | Your trips: slug, dates, visibility, number of posts, title. |
| `posts [--trip SLUG] [--status draft\|published] [--json]` | Your posts, drafts included. |
| `export [--out FILE]` | Exports the whole blog as a `.zip` and downloads it. |
| `pull DIR [--zip FILE]` | Exports the blog and unpacks it into `DIR`, rewriting only files that changed. |
| `post FILE.md [--trip SLUG] [--publish] [--dry-run]` | Creates a post from a Markdown file and uploads the photos it lists. A draft unless you pass `--publish`. |
| `open [--studio]` | Opens your blog (or Studio) in the browser. |

Global option: `--base-url URL` (or `ORTSZEIT_BASE_URL`), default
`https://studio.ortszeit.app`. The API docs currently show the schema under
`accounts.ortszeit.app`; if `studio.` does not answer for you yet, pass
`--base-url https://accounts.ortszeit.app`.

### Your API key

Create a key in Studio. The CLI sends it as `Authorization: Api-Key <key>`,
only to the API and never to the export download link (that link is signed
and needs no login).

Where the key is kept, in this order:

1. `ORTSZEIT_API_KEY`, if set. Good for CI; it overrides anything stored.
2. The system keyring (macOS Keychain, Windows Credential Manager, Secret
   Service on Linux), when one is available.
3. Otherwise `credentials.json` in your config directory
   (`~/Library/Application Support/ortszeit/` on macOS, `%APPDATA%\ortszeit\`
   on Windows, `~/.config/ortszeit/` elsewhere), readable only by you (mode
   0600). Set `ORTSZEIT_NO_KEYRING=1` to always use the file.

**Rotating a key:** create a new key in Studio, run `ortszeit login` with it,
check `ortszeit status`, then revoke the old key in Studio. Support for two
active keys at a time, so that both work during the switch, is coming soon.

If the server rejects the key (revoked, expired or mistyped), every command
says so and asks you to create a new key in Studio.

### export and pull

The export runs on the server in the background. The CLI starts it (or
follows the one already running: only one export at a time per blog), checks
on it every few seconds and less often as it goes on, and downloads the archive
when it is done. A free plan's archive has your texts and every photo's
metadata but not the photo files; a paid plan's archive has the files too
(see [Export][export]).

`pull DIR` unpacks the archive so that `DIR/blog.yaml` is the blog's root, as
the generators in this repository expect:

```sh
ortszeit pull blog && uv run ssg/ortszeit_ssg.py blog -o site
ortszeit pull blog && uv run hugo/ortszeit_to_hugo.py blog -o mysite
ortszeit pull blog && uv run astro/ortszeit_to_astro.py blog -o site
```

Files that did not change are not rewritten, so a second `pull` touches only
what is new (at least `blog.yaml` and `posts.json`, which carry the export
time). A post you deleted on Ortszeit is deleted from `DIR` too, but only
files that an earlier `pull` wrote: the list is in `DIR/.ortszeit-pull.json`,
and anything else in `DIR` is left alone. `--zip FILE` unpacks an export you
already have and needs no login.

### post

The file uses the post format from the [portable format][format]. Photos are
listed in `media` with paths relative to the file:

```markdown
---
title: Aarhus harbour
time_zone: Europe/Copenhagen
visibility: public
media:
  - file: media/IMG_4021.jpg
    alt: Ferry coming in past the harbour cranes
---

Rain until four, then the harbour went gold.
```

The CLI creates a draft, uploads the photos in the listed order, and with
`--publish` publishes the post after the last photo. That is the order the
API asks for: on publish the server dates the post from the photos' EXIF
capture time. Upload the photos with their EXIF data; the CLI sends the files
as they are and does not resize them.

- Sent: `title`, the body, `time_zone`, `hide_location`, `visibility`
  (`public` or `unlisted`; leave it out to inherit from the trip), and
  `--trip`.
- Not sent: `date` (the server sets it on publish), `place` (the server names
  the place from the photos' GPS; pick a finer one in Studio) and `draft`
  (use `--publish`). A trip password is set in Studio, so `visibility:
  password` is refused.
- `--dry-run` shows the requests it would send and sends nothing; it needs no
  login.

Creating a post and uploading a photo cannot be safely repeated. If an upload
fails halfway, the CLI stops and tells you which draft exists with how many
photos; finish it in Studio rather than running the command again.

### Errors and limits

- **401**: the key was revoked, expired or mistyped. Create a new key in
  Studio and run `login` again.
- **403**: a plan limit. The server's own message is shown as it is, for
  example how many posts your plan allows. Retrying does not help.
- **429**: too many requests. The CLI waits as long as the server's
  `Retry-After` header asks (up to five minutes) and tries again.
- Server errors and network failures are retried for reads. Writes are not
  retried, so nothing is created twice.

The API is [not a stable public interface yet][api]: it changes with the apps.
If a command breaks after an Ortszeit release, please open an issue. The
portable format is the stable contract, so `export` and `pull` output stays
readable.

## Not included

- Editing or deleting posts, trips and photos, closing trips, and choosing a
  place. The API has these; the example keeps to the common cases.
- Import. The API has it; start it in Studio.
- Creating an API key. Keys are created in Studio.

## Tests

```sh
uv run cli/tests/test_cli.py
```

The tests mock every HTTP request, with responses shaped like the documented
API.

## Deutsch

> **Noch nicht nutzbar.** Das Werkzeug spricht mit dem gehosteten Dienst
> Ortszeit. Der ist in der Vorschau und noch nicht für Anmeldungen offen. Bis
> zum Start gibt es kein Konto, mit dem man sich anmelden könnte.

`ortszeit.py` ist ein Kommandozeilen-Client für den eigenen Ortszeit-Blog, in
einer einzigen Python-Datei. Er nutzt die [API][api] mit dem eigenen
API-Schlüssel, den man in Studio anlegt. Er zeigt Reisen, Beiträge, Tarif und
Verbrauch, exportiert den Blog, hält eine lokale Kopie im [portablen
Format][format] aktuell und legt Beiträge aus Markdown-Dateien an. Er sendet
keine Telemetrie.

```sh
uv run cli/ortszeit.py login     # API-Schlüssel aus Studio einfügen (Eingabe unsichtbar)
uv run cli/ortszeit.py status    # Tarif, Verbrauch und Grenzen
uv run cli/ortszeit.py pull blog && uv run astro/ortszeit_to_astro.py blog -o site
uv run cli/ortszeit.py post beitrag.md --trip venedig-2026 --dry-run
```

- **Schlüssel:** `ORTSZEIT_API_KEY` hat Vorrang. Sonst liegt der Schlüssel im
  Schlüsselbund des Systems oder, wenn es keinen gibt, in einer Datei, die
  nur du lesen kannst. Zum Wechseln legst du in Studio einen neuen Schlüssel
  an, meldest dich damit an und widerrufst danach den alten. Dass zwei
  Schlüssel gleichzeitig aktiv sein dürfen, kommt bald.
- **`pull`** schreibt nur geänderte Dateien neu und löscht nur Dateien, die
  ein früheres `pull` geschrieben hat.
- **`post`** legt einen Entwurf an, lädt die Fotos in der angegebenen
  Reihenfolge hoch und veröffentlicht erst mit `--publish`. `--dry-run` zeigt,
  was gesendet würde.
- **Fehler:** Bei 401 legst du in Studio einen neuen Schlüssel an. Bei 403
  steht die Meldung des Servers zum erreichten Limit da. Bei 429 wartet das
  Werkzeug so lange, wie der Server verlangt.

Die Ausgabe des Werkzeugs ist englisch. Die API ist noch keine stabile
Schnittstelle; das portable Format ist es.

Als Befehl einrichten: `alias ortszeit='uv run --script /pfad/zu/cli/ortszeit.py'`.

[api]: https://ortszeit.app/docs/api/
[format]: https://ortszeit.app/docs/portable-format/
[export]: https://ortszeit.app/docs/export/
