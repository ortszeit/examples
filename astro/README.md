# Ortszeit to Astro

An Ortszeit export belongs to you. You can keep publishing it with
[Astro](https://astro.build/) without Ortszeit. `ortszeit_to_astro.py` turns
an export (the [portable format][format], schema version 1) into a complete
Astro project: content collections with a schema for the export's fields,
your posts with their photos beside them, and a small template with the
Ortszeit look. After the import you write new posts in Astro and build with
Astro, and the project no longer needs this repository.

## Quick start

You need [uv](https://docs.astral.sh/uv/) and Node.js 22.23 or newer with
npm. Run this from the repository root, using the shared sample export:

```sh
uv run astro/ortszeit_to_astro.py sample-export -o mysite --lang de
cd mysite && npm install && npm run dev
# open http://localhost:4321/
```

With your own export:

```sh
uv run astro/ortszeit_to_astro.py ~/Downloads/ortszeit-export.zip -o myblog \
    --lang en --base-url https://lena.example/
```

| Option | Meaning |
|---|---|
| `EXPORT` | The export `.zip`, the extracted `blog/` directory, or the folder that contains it. |
| `-o MYSITE` | The Astro project. It is created, or updated if it already exists (see *Importing again*). |
| `--lang` | `en` (default) or `de`. Sets the site language, the labels and the date format. Your posts are not translated. |
| `--base-url` | The public address, for the feed, the sitemap and canonical links. If you leave it out, the importer uses `https://<custom_domain>/` when the export has one, and `http://localhost:4321/` otherwise. It is only written on the first import, so change it later in `astro.config.mjs`. |
| `--include-private` | Also imports password-protected trips and posts, as **unlisted** pages. **They are not protected.** The export never contains the password. |

The versions are pinned exactly in `package.json`: Astro 7.4.8,
`@astrojs/rss` 4.0.19, `@astrojs/markdown-satteri` 0.4.7 (Astro's Markdown
processor, see *What it respects*), and for `npm run check`
`@astrojs/check` 0.9.12 with TypeScript 6.0.3. Update them when you want to,
with `npx @astrojs/upgrade`.

Astro's command line sends anonymous usage data unless you turn that off:
`npx astro telemetry disable` (or `ASTRO_TELEMETRY_DISABLED=1`). The site
you build sends nothing anywhere.

Tests: `uv run astro/tests/test_import.py`. The tests that build the site
run `npm install`, `astro build` and `astro check` in a temporary copy. They
are skipped when npm is missing or the install fails, and with
`ORTSZEIT_SKIP_BUILD=1`.

## What the importer creates

```
mysite/
  package.json, tsconfig.json     yours from the start (written once)
  astro.config.mjs                your configuration: site URL, Markdown (written once)
  .gitignore                      node_modules/, dist/, .astro/
  .ortszeit-import.json           the importer's manifest (see below)
  LICENSE-ortszeit-template.txt   the template's licence (MIT); your posts stay yours
  src/
    site.config.ts                from blog.yaml: title, bio, language, display (written once)
    content.config.ts             the collections and their schema
    i18n.ts                       labels and date formats, de and en
    lib/ortszeit.ts               Ortszeit time, URLs, what a post may show
    lib/html-as-text.mjs          HTML in posts is shown as text
    layouts/Base.astro            head, header, navigation, footer
    components/                   Meta (the meta line), PostUnit, Photos
    pages/                        see "The template"
    styles/ortszeit.css           the Ortszeit look
    styles/custom.css             your CSS, or the custom CSS from your export
    content/
      trips/venedig-2026.yaml     a trip                     -> /venedig-2026/
      posts/
        venedig-2026/
          ankunft-am-bahnhof/
            index.md              a post                     -> /venedig-2026/ankunft-am-bahnhof/
            IMG_4100.jpg          its photos, next to it
        posts/
          wieder-zu-hause/index.md   a post without a trip   -> /posts/wieder-zu-hause/
      pages/impressum.md          /impressum/, only if blog.yaml has an imprint
```

The URLs are the same as on the hosted blog, so links keep working after the
move. A post's URL is `/<trip>/<folder>/`, where `<trip>` comes from the
post's `trip` field (or `posts` without one) and `<folder>` is the folder
name, the Ortszeit slug.

A post's front matter after the import:

```yaml
---
title: Ankunft am Bahnhof          # left out when the post has no title
date: 2026-10-03T16:40:00+02:00    # with the local offset
time_zone: Europe/Rome             # the post's Ortszeit (IANA zone), or null
draft: false                       # drafts are imported with draft: true
trip: venedig-2026                 # a reference to src/content/trips/
place: {name: Venezia Santa Lucia, osm: node/1932175437}
coordinates: {lat: 45.4412, lon: 12.321}
hide_location: false
visibility: public                 # public | unlisted
photos:                            # order and alt text, files in this folder
  - {file: ./IMG_4100.jpg, alt: Der Canal Grande vor dem Bahnhof}
---
```

`src/content.config.ts` describes exactly these fields with Astro's schema
(Zod): `date` is a date, `time_zone` must be a zone Node knows, `trip` must
name an existing trip, and every `photos[].file` must exist, because
`image()` hands it to astro:assets. A typo in a post you write later fails
`npm run check` and `npm run build` with the file and the field, instead of
producing a wrong page. A trip file has `title`, `started_on`, `ended_on`
(`null` while the trip goes on), `visibility` and `co_authors`.

## What it respects

- **Ortszeit.** Dates and times are shown in the post's `time_zone`
  (`localTime` in `src/lib/ortszeit.ts`, using `Intl`). A post written at
  00:20 in Venice is dated 4 October, even though the UTC instant falls on
  3 October. A post without a zone shows only the date, with no clock time.
- **Display settings.** `display.location` (`hidden | place | coordinates`)
  and `display.time` in `src/site.config.ts` come from `blog.yaml`. For a
  post with `hide_location: true` the importer does not even copy the place
  or the coordinates into the project, because the project may end up in a
  public repository. Your export still has them.
- **Visibility.** Drafts stay drafts: `npm run dev` shows them, marked as a
  draft, and `npm run build` leaves them out. Unlisted posts get a page with
  `noindex`, but appear in no list, feed or sitemap; an unlisted trip still
  lists its own posts, as on the hosted blog. Password-protected trips and
  posts are not imported unless you pass `--include-private`.
- **HTML in posts is shown as text**, as on Ortszeit. Astro's Markdown
  normally passes HTML through. `src/lib/html-as-text.mjs`, a small plugin
  for Sätteri (Astro's Markdown processor), turns it into text, so `<b>`
  reads "&lt;b&gt;" and a pasted `<script>` never runs. Remove it from
  `astro.config.mjs` if you want HTML in your own posts.
- **Photos and location data** (see the next section).

## Photos: why no EXIF reaches your site

Photos often carry GPS in their EXIF data. The import protects that data in
two places:

1. **The importer re-encodes every photo with Pillow** before it copies the
   photo next to its post. The copy keeps its full size and gets JPEG
   quality 92 (PNG stays PNG). The importer applies the EXIF rotation to the
   pixels and keeps the colour profile. It drops everything else: EXIF, GPS,
   XMP, IPTC and PNG text.
2. **astro:assets writes renditions without metadata.** `Photos.astro`
   asks for WebP at 400, 800 and 1600 px (never wider than the original)
   and links each photo to its largest rendition, never to the file in
   `src/`. Astro copies only the renditions into `dist/`, not the original.

Why not leave the cleaning to Astro alone? Astro protects only `dist/`. The
project is meant to be committed, and many GitHub Pages sites are built from
a public repository. An original photo next to a post would publish its
location there. Re-encoding once costs one generation of JPEG at quality 92,
a loss you will not see.

The tests check this. They give the sample photos GPS tags, import them,
build the site, and then look for EXIF in every image in `dist/` and for any
JPEG or PNG there at all.

**When you add a photo yourself**, step 2 still protects the built site.
Step 1 does not run, so your repository holds the original with its GPS
data. If the repository is public, remove the metadata first, for example
with `exiftool -all= photo.jpg`.

## Importing again

You can run the importer again on a newer export, into the same folder. It
overwrites only what it wrote itself and you have not changed since.
`.ortszeit-import.json` stores a checksum for every file the importer wrote:

| File | Belongs to | On the next import |
|---|---|---|
| `package.json`, `tsconfig.json`, `astro.config.mjs`, `src/site.config.ts`, `.gitignore` | **you** | only written if missing |
| files listed in `.ortszeit-import.json` (content, photos, the template in `src/`) | the importer, **until you edit one** | replaced if unchanged; an edited file is kept and reported |
| anything else (your new posts, pages, components, `public/`, …) | **you** | never touched |

A post that disappeared from the export, for example because it is now
password-protected, is removed if you have not changed it. Delete
`.ortszeit-import.json` once you never want to import again. After that,
every file is yours.

To change the look, put your rules in `src/styles/custom.css`; it is loaded
after `ortszeit.css`, so overriding a token in `:root` is enough for a new
colour. Changing a template file (a page, a component) is fine too. The
importer then keeps your version and stops updating that one file.

## Writing new posts

A post is a folder with an `index.md` under `src/content/posts/`, in its
trip's folder, or in `posts/` if it belongs to no trip:

```sh
mkdir -p src/content/posts/venedig-2026/murano
cp ~/Pictures/IMG_5001.jpg src/content/posts/venedig-2026/murano/
```

`src/content/posts/venedig-2026/murano/index.md`:

```yaml
---
title: Murano
date: 2026-10-06T09:15:00+02:00
time_zone: Europe/Rome
trip: venedig-2026
place: {name: Murano}
coordinates: {lat: 45.4586, lon: 12.3533}
photos:
  - {file: ./IMG_5001.jpg, alt: Glasbläser am Kanal}
---

Text in markdown.
```

`time_zone` sets the Ortszeit. Write `date` with the offset that applied
there at that moment. Every other field has a default (`draft: false`,
`visibility: public`, `hide_location: false`, no photos). For a new trip,
add `src/content/trips/<trip-slug>.yaml` modelled on an imported one, and
keep the folder name and the `trip` field the same.

## The template

The template follows the hosted Ortszeit theme (spec §5), with the same
stylesheet as the Hugo example: system fonts (serif for the text, monospace
for the meta line), the "Koralle & Ozean" colour tokens, dark mode via
`prefers-color-scheme`, the meta line `PLACE · DATE · TIME · COORDINATES`,
and the route rule on trip pages. It ships **no JavaScript** and makes **no
requests to other sites**: no web fonts, no CDN, no analytics.

| Page | File |
|---|---|
| start page, newest 25 posts | `src/pages/index.astro` |
| trip, oldest first along the route rule | `src/pages/[trip]/index.astro` |
| post | `src/pages/[trip]/[post].astro` |
| archive (trips, then posts by year) | `src/pages/archive.astro` |
| places, A to Z, with their posts | `src/pages/places.astro` |
| imprint and other pages from `src/content/pages/` | `src/pages/[...page].astro` |
| RSS at `/feed.xml` (`@astrojs/rss`) | `src/pages/feed.xml.ts` |
| `sitemap.xml`, `robots.txt`, `404.html` | `src/pages/sitemap.xml.ts`, `robots.txt.ts`, `404.astro` |

The sitemap is a small endpoint rather than `@astrojs/sitemap`, because it
has to leave out unlisted pages, which only the collections know. The places
page is a list, not a map, so it needs no script and no tiles from another
site; it is left out when `display.location` is `hidden`.

The footer says, quietly, "Built with Astro, imported from Ortszeit". It
also credits GeoNames when a place name the site shows came from GeoNames.

## Deploying

Build with `npm run build`. The site is then in `dist/`.

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
        - uses: actions/setup-node@v4
          with: { node-version: 24 }
        - run: npm install && npm run build
          env: { ASTRO_TELEMETRY_DISABLED: 1 }
        - uses: actions/upload-pages-artifact@v3
          with: { path: dist }
    deploy:
      needs: build
      runs-on: ubuntu-latest
      environment: { name: github-pages, url: "${{ steps.deployment.outputs.page_url }}" }
      steps:
        - uses: actions/deploy-pages@v4
          id: deployment
  ```

  For a project site (`https://<user>.github.io/<repo>/`), set
  `site: 'https://<user>.github.io'` and `base: '/<repo>'` in
  `astro.config.mjs`. Every link in the template goes through `href()`, so
  the base is put in front of it.
- **Netlify / Cloudflare Pages.** Build command `npm run build`, output
  directory `dist`.
- **Your own server:** `npm run build && rsync -a --delete dist/ server:/var/www/blog/`.
  With nginx, also set `error_page 404 /404.html;`.

The hosted blog serves RSS at `/feed/`. Here RSS is at `/feed.xml`. If your
readers subscribed to `/feed/`, add a redirect on your host. On Netlify or
Cloudflare Pages, that is the line `/feed/ /feed.xml 301` in
`public/_redirects`.

## Differences and limits

Compared with **the hosted blog**:

- There are no maps and no lightbox. In a photo strip, each photo links to
  its large rendition. The places page lists places as text.
- There is no password protection, because a static host cannot check one.
  The export has no passwords anyway.
- "Report content" and the platform's own legal links are left out. Your
  own imprint is kept.
- Unlisted pages get `noindex` and are kept out of every list. Anyone with
  the URL can still read them, as on the hosted blog.

Compared with **[`hugo/`](../hugo/)**: the same import rules, the same
manifest, the same look. Hugo is one binary and builds faster; Astro needs
Node and npm, and in return the schema checks every post you write, and the
pages are components in TypeScript you can extend with anything from the
npm world. There is no JSON Feed here, only RSS. Compared with
**[`ssg/`](../ssg/)**, which builds a finished site in one run and keeps
nothing to edit, both give you a project to keep developing.

Other limits: only `de` and `en` labels. The importer reads only formats
Pillow can open, so HEIC needs a plugin; any other photo is skipped with a
message. Astro's Markdown supports more than Ortszeit's subset (smart
punctuation, for example, is on). There is no way back: the project
cannot push to a hosted account. That is planned for the `ortszeit` CLI
(spec §15).

## License

MIT, see [`LICENSE`](../LICENSE). The generated project carries the
template's licence as `LICENSE-ortszeit-template.txt`; your posts, photos
and settings are not covered by it.

## Deutsch

Ein Ortszeit-Export reicht, um den eigenen Blog mit Astro weiterzuführen.
`ortszeit_to_astro.py` erzeugt daraus ein vollständiges Astro-Projekt:
Content Collections mit einem Schema für die Felder des Exports, je Beitrag
einen Ordner mit den Fotos daneben (astro:assets verkleinert sie), je Reise
eine YAML-Datei, dazu Startseite, Reisen, Archiv, Orte, Impressum und
RSS-Feed. Die URLs bleiben wie beim gehosteten Blog.

```sh
uv run astro/ortszeit_to_astro.py mein-export.zip -o meinblog --lang de --base-url https://mein-blog.example/
cd meinblog && npm install && npm run dev
```

- **Ortszeit:** Datum und Uhrzeit stehen in der Zeitzone des Beitrags
  (`time_zone`). Ohne Zone erscheint nur das Datum.
- **Datenschutz:** Fotos werden beim Import ohne EXIF/GPS neu kodiert.
  Astro veröffentlicht nur verkleinerte WebP-Versionen ohne Metadaten, nie
  das Original. Bei „Ort verbergen“ übernimmt der Import Ort und
  Koordinaten gar nicht erst in das Projekt.
- **Sichtbarkeit:** Entwürfe bleiben Entwürfe (`npm run dev` zeigt sie,
  `npm run build` nicht). Passwortgeschützte Reisen und Beiträge werden
  nicht importiert. Mit `--include-private` kommen sie als „nicht gelistet“
  ins Projekt, aber **ohne Schutz**.
- **HTML in Beiträgen** erscheint als Text, wie auf Ortszeit.
- **Erneut importieren:** Der Importer ersetzt nur Dateien, die er selbst
  geschrieben hat und die Sie seitdem nicht geändert haben (Prüfsummen in
  `.ortszeit-import.json`). `astro.config.mjs`, `src/site.config.ts` und
  `package.json` schreibt er nur beim ersten Mal. Eigenes CSS gehört in
  `src/styles/custom.css`.
- **Weiterschreiben:** Für einen neuen Beitrag legen Sie
  `src/content/posts/<reise>/<slug>/index.md` an und die Fotos daneben.
  Das Schema in `src/content.config.ts` prüft jedes Feld.
- **Veröffentlichen:** Bauen Sie mit `npm run build` und laden Sie `dist/`
  hoch, etwa über GitHub Pages (Action siehe oben), Netlify, Cloudflare
  Pages oder einen eigenen Server.

[format]: https://ortszeit.app/docs/portable-format/
