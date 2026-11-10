/*
 * The content of your blog, as Astro content collections.
 *
 *   posts   src/content/posts/<trip>/<post>/index.md, its photos beside it
 *           (<trip> is the trip's slug, or "posts" for posts without a trip)
 *   trips   src/content/trips/<trip>.yaml
 *   pages   src/content/pages/<page>.md, e.g. the imprint
 *
 * The fields are the Ortszeit export's front matter, in the shape the
 * importer writes them. Posts you write yourself follow the same schema, and
 * `astro check` / `astro build` tell you when a field is wrong.
 */
import { defineCollection, reference } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';

/** An IANA zone such as Europe/Rome, one this runtime can convert to. */
const timeZone = z.string().refine(
  (zone) => {
    try {
      new Intl.DateTimeFormat('en', { timeZone: zone });
      return true;
    } catch {
      return false;
    }
  },
  { message: 'time_zone must be an IANA time zone such as Europe/Rome' },
);

const posts = defineCollection({
  loader: glob({
    pattern: '**/index.md',
    base: './src/content/posts',
    // venedig-2026/burano/index.md -> "venedig-2026/burano"
    generateId: ({ entry }) => entry.replace(/\/index\.md$/, ''),
  }),
  schema: ({ image }) =>
    z.object({
      title: z.string().optional(), // titles are optional on Ortszeit
      // The instant, written with the offset that applied where it was written.
      date: z.coerce.date(),
      // The post's Ortszeit. Date and time are shown in this zone; without
      // one, a post shows only its date.
      time_zone: timeZone.nullable().default(null),
      draft: z.boolean().default(false), // built only by `astro dev`
      trip: reference('trips').optional(), // decides the URL: /<trip>/<post>/
      place: z
        .object({
          name: z.string(),
          osm: z.string().nullable().optional(), // e.g. node/1932175437
          geonames: z.union([z.number(), z.string()]).nullable().optional(),
        })
        .optional(),
      coordinates: z.object({ lat: z.number(), lon: z.number() }).optional(),
      hide_location: z.boolean().default(false),
      visibility: z.enum(['public', 'unlisted']).default('public'),
      // In this order. `file` is relative to the post, so astro:assets
      // finds, resizes and re-encodes it.
      photos: z
        .array(
          z.object({
            file: image(),
            alt: z.string().nullable().default(null),
            taken_at: z.coerce.date().nullable().optional(),
          }),
        )
        .default([]),
    }),
});

const trips = defineCollection({
  loader: glob({ pattern: '*.yaml', base: './src/content/trips' }),
  schema: z.object({
    title: z.string(),
    started_on: z.coerce.date().nullable().default(null),
    ended_on: z.coerce.date().nullable().default(null), // null: still travelling
    visibility: z.enum(['public', 'unlisted']).default('public'),
    co_authors: z.array(z.string()).default([]),
  }),
});

const pages = defineCollection({
  loader: glob({ pattern: '*.md', base: './src/content/pages' }),
  schema: z.object({ title: z.string() }),
});

export const collections = { posts, trips, pages };
