# Ortszeit examples

[Ortszeit](https://www.ortszeit.app/) is a hosted travel blog. Everything you
write there can be exported in an open, documented format, the
[portable format](https://www.ortszeit.app/docs/portable-format/): Markdown with
front matter, your photos, and a `blog.yaml`. The blog belongs to whoever writes
it, and this repository shows what that means in practice: three small ways to turn
an export into a website you host yourself.

| Example | What you need | What you get |
|---|---|---|
| [`ssg/`](ssg/) | only [uv](https://docs.astral.sh/uv/) | one readable Python script that builds a complete static blog |
| [`hugo/`](hugo/) | [Hugo](https://gohugo.io/) and uv | a Hugo site with an Ortszeit-like theme that you keep developing with Hugo |
| [`astro/`](astro/) | [Node.js](https://nodejs.org/) with npm, and uv | an Astro project with content collections and the Ortszeit look that you keep developing with Astro |
| [`custom-css/`](custom-css/) | a paid Ortszeit blog | four stylesheets to paste into *Custom CSS* in Studio, with the classes and tokens to target |
| [`cli/`](cli/) | uv and an Ortszeit API key (not yet: the service is in preview) | a command-line client: status, trips, posts, export, `pull` into a folder, and new posts from Markdown |

The three generators read the same small export in [`sample-export/`](sample-export/), so you
can try them before exporting your own blog:

```sh
uv run ssg/ortszeit_ssg.py sample-export -o site --serve
uv run hugo/ortszeit_to_hugo.py sample-export -o mysite && cd mysite && hugo server
uv run astro/ortszeit_to_astro.py sample-export -o myastro && cd myastro && npm install && npm run dev
```

Once the hosted service is open (it is in preview, with no signup yet), the
command-line client in [`cli/`](cli/) fetches your own blog into the same
layout:

```sh
uv run cli/ortszeit.py login
uv run cli/ortszeit.py pull blog && uv run astro/ortszeit_to_astro.py blog -o site
```

These are examples, kept short enough to read. Ortszeit's own command-line tool
for self-hosting is planned separately.

## Licence

MIT, see [LICENSE](LICENSE). The sample export's texts and pictures are
generated test data.
