/* RSS 2.0 at /feed.xml: the newest 20 listed posts, full text. */
import rss from '@astrojs/rss';
import type { APIContext } from 'astro';
import { href, label, listedPosts, postPath } from '../lib/ortszeit';
import { site } from '../site.config';

export async function GET(context: APIContext) {
  const posts = (await listedPosts()).slice(0, 20);
  return rss({
    title: site.title,
    description: site.description || site.title,
    site: context.site ?? 'http://localhost:4321',
    items: posts.map((post) => ({
      title: label(post),
      link: href(postPath(post)),
      pubDate: post.data.date,
      content: post.rendered?.html ?? '',
    })),
    customData: `<language>${site.lang}</language>`,
  });
}
