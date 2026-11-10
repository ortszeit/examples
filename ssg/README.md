# Ortszeit example static-site generator

The blog belongs to whoever writes it. An Ortszeit export is a directory of
markdown files, YAML and photos (the [portable format][format], schema
version 1). This folder shows that the export is enough to run your blog
without Ortszeit: `ortszeit_ssg.py` is one Python file, about 870 lines with
comments, that turns an export into a static website you can host anywhere.

It is a **teaching example**: short enough to read in one sitting and easy to
change. It does not replace the planned `ortszeit` CLI (spec §15).

## Try it

You need [uv](https://docs.astral.sh/uv/). It reads the dependencies from the
script header (PEP 723): `markdown-it-py`, `pyyaml` and `pillow`.

```sh
uv run ssg/ortszeit_ssg.py sample-export -o /tmp/lena --lang de --serve
# then open http://localhost:8000/
```

`../sample-export/` (shared by all examples in this repository) is a small archive written by
Ortszeit's real exporter. It has one public
trip with three posts and three photos, a password-protected trip, a draft, a
post with a hidden location and a post with no time zone. The photos are
generated with Pillow.

```
usage: ortszeit_ssg.py SOURCE -o OUTPUT [--base-url URL] [--lang {de,en}]
                       [--include-private] [--include-originals] [--serve] [--port PORT]
```

| Option | Meaning |
|---|---|
| `SOURCE` | The export `.zip`, the extracted `blog/` directory, or the folder that contains it. |
| `-o OUTPUT` | Output directory. It is replaced on every build, but only if this script created it. |
| `--base-url` | The public address, for example `https://lena.example` or `https://user.github.io/blog`. Feeds, sitemap and canonical links use it, and a path such as `/blog` becomes the prefix for every link. If you leave it out, the generator uses `https://<custom_domain>` when the export has one, and `http://localhost:8000` otherwise. |
| `--lang` | Language of the page labels (`en` default, `de`). Your posts are not translated. |
| `--include-private` | Also publishes password-protected trips and posts, as unlisted pages. **They are not protected**: the export never contains the password. |
| `--include-originals` | Also copies the original photos (with EXIF) and links them, but only for posts whose coordinates the blog shows anyway. |
| `--serve` | Builds the site, then serves it on `127.0.0.1:--port` to preview. Do not use this server for hosting. |

Tests: `uv run ssg/tests/test_ortszeit_ssg.py`

## What it builds

```
index.html                 newest 25 public posts
archive/                   trips, then every public post by year
<trip>/                    one trip, oldest first, along the route rule
<trip>/<post>/             a post, with its photo derivatives next to it
posts/<post>/              posts that belong to no trip
impressum/                 only if blog.yaml has an imprint
feed.xml  atom.xml  feed.json  (RSS 2.0, Atom, JSON Feed 1.1)
sitemap.xml  robots.txt  404.html
```

The URLs match the hosted blog, so links keep working after you move.

## What it respects

- **Ortszeit.** Dates and times are shown in the post's `time_zone`, as on
  the hosted blog. A post written at 00:20 in Venice is dated the 4th, even
  though the UTC instant falls on the 3rd. A post with no zone shows only the
  date, with no clock time.
- **`display`** from `blog.yaml`. `location: hidden | place | coordinates`
  controls what the meta line shows, and `time: false` removes the clock
  time.
- **`hide_location`** on a post. Neither the place nor the coordinates are
  shown, and its original photo is never copied.
- **Visibility.** Drafts are never published. `unlisted` posts get a page
  (marked `noindex`) but appear in no list, feed or sitemap. `password` trips
  and posts are left out unless you pass `--include-private`.
- **Photos.** Photos are re-encoded at 400/800/1600 px. This also removes EXIF
  data, including GPS, so a derivative cannot reveal a location the author
  hid. A free-tier export has no photo files, so its posts appear without
  photos.
- **Robustness.** Unknown keys are ignored, and optional keys that older
  archives lack get their documented defaults. If `ortszeit_schema` is not
  `1`, the generator stops with a clear error.

The look follows the Ortszeit theme (spec §5): the same colour tokens, light
and dark mode, system fonts, the monospace meta line and the route rule on
trip pages. The pages have no JavaScript, no web fonts and no requests to
other sites. In a photo strip, each photo links to its large version instead
of opening a lightbox. Custom CSS from the export is added after the theme,
without `@import` and without links to other sites. The footer says the site
was built with the example generator. Ortszeit does not appear as the
operator.

## Deploying

The output is plain files. Build with the address the site will have:

```sh
uv run ortszeit_ssg.py export.zip -o site --base-url https://lena.example
```

- **GitHub Pages.** Push `site/` to a `gh-pages` branch, or use the
  `actions/upload-pages-artifact` workflow. For a project site, pass the
  path: `--base-url https://<user>.github.io/<repo>`. `404.html` is used
  automatically.
- **Netlify / Cloudflare Pages.** Set the publish directory to `site/`, or
  upload it with `netlify deploy --dir site` or
  `wrangler pages deploy site`. No build command is needed if you commit the
  output.
- **Your own nginx:**

  ```nginx
  server {
      server_name lena.example;
      root /var/www/lena;
      index index.html;
      error_page 404 /404.html;
      location ~ \.(jpg|css)$ { expires 30d; }
  }
  ```

The hosted blog serves RSS at `/feed/`. Here the RSS feed is at `/feed.xml`.
If your readers subscribed to the old address, add a redirect on your host.

## Limits (compared with the planned CLI)

This script builds from an export and does nothing else. The CLI in spec §15
will be the real tool. Missing here:

- Writing (`init`, `new`, `add` with EXIF reading), publishing, `ping` to
  discovery, and `push` back to a hosted account.
- Maps, place pages and the JS lightbox. The meta line has no links to places.
- Every build processes all photos again. There is no caching and no
  incremental build. Only JPEG output, and only formats Pillow can open
  (HEIC needs a plugin).
- No themes and no pagination. Only `de` and `en` labels.
- No password protection, because the export has no passwords. A static
  host cannot check one anyway.
- The markdown subset matches Ortszeit's: emphasis, links, lists and quotes.
  Raw HTML is shown as text.

## License

MIT (see `LICENSE` in this folder). The rest of the repository is licensed
separately. The planned CLI is meant to be MIT as well (spec §15 and §18.6).

## Deutsch

Ein Ortszeit-Export reicht, um den eigenen Blog ohne Ortszeit zu betreiben.
Dieses Skript ist dafür ein lesbares Beispiel. Es erzeugt aus dem Export
(ZIP oder Ordner) eine statische Website: Startseite, Reisen, Beiträge,
Archiv, Impressum, RSS-, Atom- und JSON-Feed sowie eine Sitemap. Datum und
Uhrzeit stehen in der Ortszeit des Beitrags. Die Darstellungs-Einstellungen
des Blogs und „Ort verbergen“ werden eingehalten. Entwürfe und
passwortgeschützte Reisen werden nicht veröffentlicht. Fotos werden neu
kodiert, dabei fallen EXIF- und GPS-Daten weg.

```sh
uv run ssg/ortszeit_ssg.py mein-export.zip -o site --lang de --base-url https://mein-blog.example
```

Das Ergebnis lässt sich auf jedem Webspace hochladen, etwa bei GitHub Pages,
Netlify, Cloudflare Pages oder auf einem eigenen nginx. Es ist ein Lehrbeispiel
und ersetzt nicht die geplante `ortszeit`-CLI.

[format]: https://ortszeit.app/docs/portable-format/
