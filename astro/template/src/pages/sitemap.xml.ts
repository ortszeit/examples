/* /sitemap.xml: every public page. Unlisted trips and posts stay out. */
import type { APIContext } from 'astro';
import { getCollection } from 'astro:content';
import { href, listedPosts, postPath, tripPath } from '../lib/ortszeit';
import { site } from '../site.config';

export async function GET(context: APIContext) {
  const base = context.site ?? new URL('http://localhost:4321');
  const trips = await getCollection('trips', ({ data }) => data.visibility === 'public');
  const pages = await getCollection('pages');
  const paths = [
    '/',
    '/archive/',
    ...(site.display.location !== 'hidden' ? ['/places/'] : []),
    ...trips.map(tripPath),
    ...(await listedPosts()).map(postPath),
    ...pages.map((page) => `/${page.id}/`),
  ];
  const urls = paths.map((path) => `  <url><loc>${new URL(href(path), base).href}</loc></url>`);
  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.join('\n')}
</urlset>
`;
  return new Response(xml, { headers: { 'Content-Type': 'application/xml; charset=utf-8' } });
}
