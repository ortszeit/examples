# Ortszeit examples

[Ortszeit](https://www.ortszeit.app/) is a hosted travel blog. Everything you
write there can be exported in an open, documented format, the
[portable format](https://www.ortszeit.app/docs/portable-format/): Markdown with
front matter, your photos, and a `blog.yaml`. The blog belongs to whoever writes
it, and this repository shows what that means in practice: two small ways to turn
an export into a website you host yourself.

| Example | What you need | What you get |
|---|---|---|
| [`ssg/`](ssg/) | only [uv](https://docs.astral.sh/uv/) | one readable Python script that builds a complete static blog |
| [`hugo/`](hugo/) | [Hugo](https://gohugo.io/) and uv | a Hugo site with an Ortszeit-like theme that you keep developing with Hugo |

Both read the same small export in [`sample-export/`](sample-export/), so you
can try them before exporting your own blog:

```sh
uv run ssg/ortszeit_ssg.py sample-export -o site --serve
```

These are examples, kept short enough to read. Ortszeit's own command-line tool
for self-hosting is planned separately.

## Licence

MIT, see [LICENSE](LICENSE). The sample export's texts and pictures are
generated test data.
