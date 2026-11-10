import type { APIContext } from 'astro';
import { href } from '../lib/ortszeit';

export function GET(context: APIContext) {
  const sitemap = new URL(href('/sitemap.xml'), context.site ?? 'http://localhost:4321');
  return new Response(`User-agent: *\nAllow: /\n\nSitemap: ${sitemap.href}\n`);
}
