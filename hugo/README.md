# Ortszeit to Hugo

An Ortszeit export belongs to you. You can keep publishing it with
[Hugo](https://gohugo.io/) without Ortszeit. `ortszeit_to_hugo.py` turns an
export (the [portable format][format], schema version 1) into a complete Hugo
project. That project includes `themes/ortszeit/`, a small theme with the
Ortszeit look. After the import you write new posts in Hugo and build with
Hugo, and the project no longer needs this repository.

## Quick start

You need [uv](https://docs.astral.sh/uv/) and Hugo **extended** 0.146 or
newer (the theme writes WebP). Run this from the repository root, using the
shared sample export:

```sh
uv run hugo/ortszeit_to_hugo.py sample-export -o mysite --lang de
cd mysite && hugo server
# open http://localhost:1313/
```

With your own export:

```sh
uv run hugo/ortszeit_to_hugo.py ~/Downloads/ortszeit-export.zip -o myblog \
    --lang en --base-url https://lena.example/
```

| Option | Meaning |
|---|---|
| `EXPORT` | The export `.zip`, the extracted `blog/` directory, or the folder that contains it. |
| `-o MYSITE` | The Hugo project. It is created, or updated if it already exists (see *Importing again*). |
| `--lang` | `en` (default) or `de`. Sets the site language, the theme's labels and the date format. Your posts are not translated. |
| `--base-url` | The public address. Hugo uses it for feeds, the sitemap and canonical links. If you leave it out, the importer uses `https://<custom_domain>/` when the export has one, and `http://localhost:1313/` otherwise. It is only written on the first import, so change it later in `hugo.toml`. |
| `--include-private` | Also imports password-protected trips and posts, as **unlisted** pages. **They are not protected.** The export never contains the password. |

Tests: `uv run hugo/tests/test_import.py`. The tests that build the site run
only when `hugo` is installed.

## What the importer creates

```
mysite/
  hugo.toml                       your site configuration (title, baseURL, language, display)
  .gitignore                      public/, resources/
  .ortszeit-import.json           the importer's manifest (see below)
  themes/ortszeit/                the theme, copied in
  assets/css/custom.css           only if your blog had custom CSS
  content/
    archive.md                    /archive/
    impressum.md                  /impressum/, only if blog.yaml has an imprint
    venedig-2026/
      _index.md                   the trip: a section       -> /venedig-2026/
      ankunft-am-bahnhof/
        index.md                  a post: a page bundle     -> /venedig-2026/ankunft-am-bahnhof/
        IMG_4100.jpg              its photos, next to it
    posts/
      _index.md                   posts without a trip (no page of its own)
      wieder-zu-hause/index.md    -> /posts/wieder-zu-hause/
```

The URLs are the same as on the hosted blog, so links keep working after the
move. There is no `[permalinks]` setting. The folder names are the Ortszeit
slugs, and Hugo builds each URL from its folder.

A post's front matter after the import:

```yaml
---
title: Ankunft am Bahnhof          # left out when the post has no title
date: 2026-10-03T16:40:00+02:00    # with the local offset
draft: false                       # drafts are imported with draft: true
build:
  publishResources: false          # only resized images reach public/
params:
  time_zone: Europe/Rome           # the post's Ortszeit (IANA zone), or null
  place: {name: Venezia Santa Lucia, osm: node/1932175437}
  coordinates: {lat: 45.4412, lon: 12.321}
  hide_location: false
  visibility: public               # public | unlisted
  photos:                          # order and alt text, files in this folder
    - {file: IMG_4100.jpg, alt: Der Canal Grande vor dem Bahnhof}
---
```

A trip's `_index.md` has `type: trip`, its dates (`started_on`, `ended_on`),
`visibility` and `co_authors`. It also has a `cascade`, so every page in the
trip folder is a post (`type: post`) and publishes no original files.

## What it respects

- **Ortszeit.** The theme shows date and time in the post's
  `params.time_zone` (`time.In`). A post written at 00:20 in Venice is dated
  4 October, even though the UTC instant falls on 3 October. A post without a
  zone shows only the date, with no clock time.
- **Display settings.** `params.display.location` (`hidden | place |
  coordinates`) and `params.display.time` in `hugo.toml` come from
  `blog.yaml`. For a post with `hide_location: true` the importer does not
  even copy the place or the coordinates into the project, because the
  project may end up in a public repository. Your export still has them.
- **Visibility.** Drafts stay drafts (`hugo server -D` shows them, `hugo`
  never publishes them). Unlisted posts get a page with `noindex`, but appear
  in no list, feed or sitemap. Password-protected trips and posts are not
  imported unless you pass `--include-private`.
- **Photos and location data** (see the next section).

## Photos: why no EXIF reaches your site

Photos often carry GPS in their EXIF data. The import protects that data in
three places:

1. **The importer re-encodes every photo with Pillow** before it copies the
   photo into the page bundle. The copy keeps its full size and gets JPEG
   quality 92 (PNG stays PNG). The importer applies the EXIF rotation to the
   pixels and keeps the colour profile. It drops everything else: EXIF, GPS,
   XMP, IPTC and PNG text.
2. **Hugo writes resized images without metadata.** We checked this with
   Hugo 0.155: a JPEG with camera and GPS tags, resized to WebP or JPEG, came
   out with no EXIF at all. The theme serves only these renditions (WebP at
   400, 800 and 1600 px).
3. **`build.publishResources: false`** on every post, and in the trip
   cascade for posts you add later. **Without this setting, Hugo copies every
   file of a page bundle into `public/` unchanged**, and we checked that
   too. With the setting, an original never reaches the public site, not even
   the cleaned copy.

Why not leave the cleaning to Hugo alone? Hugo protects only `public/`. A
Hugo project is meant to be committed, and many GitHub Pages sites are built
from a public repository. An original photo in a page bundle would publish
its location there. Re-encoding once costs one generation of JPEG at quality
92, a loss you will not see.

The tests check all of this. They give the sample photos GPS tags, import
them, build with `hugo --minify`, and then look for EXIF in every image in
`public/`.

**When you add a photo yourself**, steps 2 and 3 still protect the built
site. Step 1 does not run, so your repository holds the original with its
GPS data. If the repository is public, remove the metadata first, for example
with `exiftool -all= photo.jpg`.

## Importing again

You can run the importer again on a newer export, into the same folder. It
overwrites only what it wrote itself and you have not changed since.
`.ortszeit-import.json` stores a checksum for every file the importer wrote:

| File | Belongs to | On the next import |
|---|---|---|
| `hugo.toml`, `.gitignore` | **you** | only written if missing |
| files listed in `.ortszeit-import.json` (content, photos, `themes/ortszeit/`, `assets/css/custom.css`) | the importer, **until you edit one** | replaced if unchanged; an edited file is kept and reported |
| anything else (your new posts, `layouts/`, `static/`, …) | **you** | never touched |

A post that disappeared from the export, for example because it is now
password-protected, is removed if you have not changed it. Delete
`.ortszeit-import.json` once you never want to import again. After that, every
file is yours.

To change the look, **do not edit `themes/ortszeit/`**. Put a file with the
same path under your project's `layouts/` (for example
`layouts/_partials/footer.html`) and Hugo uses your file. Put your CSS in
`assets/css/custom.css`, which is appended after the theme's stylesheet. Then
a later import can still update the theme.

## Writing new posts

A post is a page bundle in a trip folder, or in `content/posts/` if it
belongs to no trip:

```sh
mkdir -p content/venedig-2026/murano
cp ~/Pictures/IMG_5001.jpg content/venedig-2026/murano/
```

`content/venedig-2026/murano/index.md`:

```yaml
---
title: Murano
date: 2026-10-06T09:15:00+02:00
params:
  time_zone: Europe/Rome
  place: {name: Murano}
  coordinates: {lat: 45.4586, lon: 12.3533}
  photos:
    - {file: IMG_5001.jpg, alt: Glasbläser am Kanal}
---

Text in markdown.
```

`params.time_zone` sets the Ortszeit. Write `date` with the offset that
applied there at that moment. For a new trip, create
`content/<trip-slug>/_index.md` modelled on an imported one, and keep its
`type: trip` and the `cascade` block.

## The theme

`themes/ortszeit/` follows the hosted Ortszeit theme (spec §5): system fonts
(serif for the text, monospace for the meta line), the colour tokens, dark
mode via `prefers-color-scheme`, the meta line `PLACE · DATE · TIME ·
COORDINATES`, and the route rule on trip pages. It has **no JavaScript** and
makes **no requests to other sites**. Hugo Pipes concatenates, minifies and
fingerprints the CSS. Labels are translated in `i18n/de.toml` and
`i18n/en.toml`.

| Page | Template |
|---|---|
| start page, newest 25 posts | `home.html` |
| trip, oldest first along the route rule | `section.html` |
| post, imprint | `page.html` |
| archive (trips, then posts by year) | `archive.html` (`layout: archive`) |
| RSS at `/feed.xml`, JSON Feed 1.1 at `/feed.json` | `home.rss.xml`, `home.jsonfeed.json` |
| `404.html`, `robots.txt`, `sitemap.xml` | `404.html`, `robots.txt`, Hugo's sitemap |

The footer says, quietly, "Built with Hugo, imported from Ortszeit". It also
credits GeoNames when a place name the site shows came from GeoNames.

## Deploying

Build with `hugo --minify`. The site is then in `public/`.

- **GitHub Pages.** In *Settings → Pages*, choose *GitHub Actions*, then add
  `.github/workflows/pages.yml`:

  ```yaml
  name: Pages
  on: { push: { branches: [main] }, workflow_dispatch: {} }
  permissions: { contents: read, pages: write, id-token: write }
  jobs:
    build:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: actions/configure-pages@v5
          id: pages
        - uses: peaceiris/actions-hugo@v3
          with: { hugo-version: "0.155.3", extended: true }
        - run: hugo --minify --baseURL "${{ steps.pages.outputs.base_url }}/"
        - uses: actions/upload-pages-artifact@v3
          with: { path: public }
    deploy:
      needs: build
      runs-on: ubuntu-latest
      environment: { name: github-pages, url: "${{ steps.deployment.outputs.page_url }}" }
      steps:
        - uses: actions/deploy-pages@v4
          id: deployment
  ```

  If `baseURL` in `hugo.toml` is already right (for example
  `https://<user>.github.io/<repo>/`), a plain `hugo --minify` is enough.
  Hugo then puts the path in front of every link.
- **Netlify / Cloudflare Pages.** Use the build command `hugo --minify`, the
  output directory `public` and the environment variable
  `HUGO_VERSION=0.155.3` (Netlify installs the extended edition; on
  Cloudflare Pages also check that the build image provides it).
- **Your own server:** `hugo --minify && rsync -a --delete public/ server:/var/www/blog/`.
  With nginx, also set `error_page 404 /404.html;`.

The hosted blog serves RSS at `/feed/`. Here RSS is at `/feed.xml`, and the
JSON feed stays at `/feed.json`. If your readers subscribed to `/feed/`, add a
redirect on your host. On Netlify or Cloudflare Pages, that is the line
`/feed/ /feed.xml 301` in `static/_redirects`.

## Differences and limits

Compared with **the hosted blog**:

- There are no maps, no place pages and no lightbox. In a photo strip, each
  photo links to its large rendition. Place names are not links.
- There is no password protection, because a static host cannot check one.
  The export has no passwords anyway.
- "Report content" and the platform's own legal links are left out. Your
  own imprint is kept.
- Unlisted pages get `noindex` and are kept out of every list. Anyone with
  the URL can still read them, as on the hosted blog.

Compared with **[`ssg/`](../ssg/)**: `ssg/` builds a finished site in one run
and keeps nothing to edit. This importer creates a project you keep
developing. Your posts are markdown files there, and Hugo builds the site
(incrementally, with image caching in `resources/`). `ssg/` writes JPEG
renditions, this theme writes WebP. `ssg/` also writes an Atom feed and can
optionally copy originals.

Other limits: only `de` and `en` labels. The importer reads only formats
Pillow can open, so HEIC needs a plugin; any other photo is skipped with a
message. The Hugo project uses Hugo's markdown (Goldmark), which supports more
than Ortszeit's subset. Raw HTML stays switched off. There is no way back:
the project cannot push to a hosted account. That is planned for the
`ortszeit` CLI (spec §15).

## License

MIT, see [`LICENSE`](../LICENSE). The copied theme carries its own
`LICENSE`.

## Deutsch

Ein Ortszeit-Export reicht, um den eigenen Blog mit Hugo weiterzuführen.
`ortszeit_to_hugo.py` erzeugt daraus ein vollständiges Hugo-Projekt:
`hugo.toml`, das Theme `ortszeit` und je Beitrag ein Page Bundle mit den
Fotos daneben. Jede Reise wird eine Section. Dazu kommen Archiv, Impressum,
RSS- und JSON-Feed. Die URLs bleiben wie beim gehosteten Blog.

```sh
uv run hugo/ortszeit_to_hugo.py mein-export.zip -o meinblog --lang de --base-url https://mein-blog.example/
cd meinblog && hugo server
```

- **Ortszeit:** Datum und Uhrzeit stehen in der Zeitzone des Beitrags
  (`params.time_zone`). Ohne Zone erscheint nur das Datum.
- **Datenschutz:** Fotos werden beim Import ohne EXIF/GPS neu kodiert. Hugo
  veröffentlicht nur verkleinerte WebP-Versionen ohne Metadaten. Originale
  bleiben mit `build.publishResources: false` aus `public/` heraus. Bei
  „Ort verbergen“ übernimmt der Import Ort und Koordinaten gar nicht erst in
  das Projekt.
- **Sichtbarkeit:** Entwürfe bleiben Entwürfe (`draft: true`).
  Passwortgeschützte Reisen und Beiträge werden nicht importiert. Mit
  `--include-private` kommen sie als „nicht gelistet“ ins Projekt, aber
  **ohne Schutz**.
- **Erneut importieren:** Der Importer ersetzt nur Dateien, die er selbst
  geschrieben hat und die Sie seitdem nicht geändert haben (Prüfsummen in
  `.ortszeit-import.json`). `hugo.toml` schreibt er nur beim ersten Mal.
  Anpassungen am Aussehen gehören in eigene Dateien unter `layouts/` und in
  `assets/css/custom.css`, nicht in `themes/ortszeit/`.
- **Weiterschreiben:** Für einen neuen Beitrag legen Sie einen Ordner in der
  Reise an, etwa `content/<reise>/<slug>/index.md`, und legen die Fotos
  daneben. In `params` stehen `time_zone` und die Liste `photos`.
- **Veröffentlichen:** Bauen Sie mit `hugo --minify` und laden Sie `public/`
  hoch, etwa über GitHub Pages (Action siehe oben), Netlify, Cloudflare Pages
  oder einen eigenen Server.

[format]: https://ortszeit.app/docs/portable-format/
