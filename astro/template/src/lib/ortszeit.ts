/*
 * What the pages share: which posts are shown, their URLs, and the Ortszeit,
 * a post's date on the wall clock where it was written.
 */
import { getCollection, type CollectionEntry } from 'astro:content';
import { t } from '../i18n';
import { site } from '../site.config';

export type Post = CollectionEntry<'posts'>;
export type Trip = CollectionEntry<'trips'>;

export interface SiteConfig {
  title: string;
  description: string;
  lang: 'de' | 'en';
  display: {
    location: 'hidden' | 'place' | 'coordinates'; // what the meta line shows of a place
    time: boolean; // false: dates without the clock time
  };
}

// --- which posts ------------------------------------------------------------

/** Every post that gets a page, newest first. Drafts only in `astro dev`. */
export async function allPosts(): Promise<Post[]> {
  const posts = await getCollection('posts', ({ data }) => import.meta.env.DEV || !data.draft);
  return posts.sort((a, b) => b.data.date.valueOf() - a.data.date.valueOf());
}

/** Unlisted posts have a page, but no place in lists, feeds or the sitemap. */
export const isListed = (post: Post) => post.data.visibility === 'public';

export async function listedPosts(): Promise<Post[]> {
  return (await allPosts()).filter(isListed);
}

/** The posts a trip page shows, oldest first along the route. An unlisted
 * trip lists its own unlisted posts too, like the hosted blog. */
export async function tripPosts(trip: Trip): Promise<Post[]> {
  const posts = (await allPosts()).filter((p) => p.data.trip?.id === trip.id);
  return posts.filter((p) => trip.data.visibility !== 'public' || isListed(p)).reverse();
}

// --- URLs (the same as on the hosted blog) ----------------------------------

/** A path on this site, with the `base` from astro.config.mjs in front. */
export function href(path: string): string {
  return import.meta.env.BASE_URL.replace(/\/$/, '') + path;
}

export const postSlug = (post: Post) => post.id.split('/').at(-1) ?? post.id;
export const tripOf = (post: Post) => post.data.trip?.id ?? 'posts';
export const postPath = (post: Post) => `/${tripOf(post)}/${postSlug(post)}/`;
export const tripPath = (trip: Trip) => `/${trip.id}/`;

// --- the Ortszeit ------------------------------------------------------------

export interface Local {
  year: number;
  month: number; // 1-12
  day: number;
  hour: number;
  minute: number;
  second: number;
  offset: string; // +02:00
}

/** An instant on the wall clock of `zone` (UTC when there is none). */
export function localTime(date: Date, zone: string | null): Local {
  const format = new Intl.DateTimeFormat('en-US', {
    timeZone: zone ?? 'UTC',
    hourCycle: 'h23',
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: 'numeric',
    second: 'numeric',
    timeZoneName: 'longOffset',
  });
  const parts = Object.fromEntries(format.formatToParts(date).map((p) => [p.type, p.value]));
  const name = parts.timeZoneName ?? 'GMT';
  return {
    year: Number(parts.year),
    month: Number(parts.month),
    day: Number(parts.day),
    hour: Number(parts.hour),
    minute: Number(parts.minute),
    second: Number(parts.second),
    offset: name === 'GMT' ? '+00:00' : name.slice(3),
  };
}

export const postTime = (post: Post) => localTime(post.data.date, post.data.time_zone);

const pad = (n: number) => String(n).padStart(2, '0');

export const dayMonth = (l: Local) => t.dayMonth(l.day, t.monthShort[l.month - 1]);
export const longDate = (l: Local) => t.longDate(l.day, t.monthLong[l.month - 1], l.year);
export const clockTime = (l: Local) => `${pad(l.hour)}:${pad(l.minute)}`;

/** The value of <time datetime>: with the hour only when the hour is shown. */
export function stamp(l: Local, withClock: boolean): string {
  const day = `${l.year}-${pad(l.month)}-${pad(l.day)}`;
  return withClock ? `${day}T${pad(l.hour)}:${pad(l.minute)}:${pad(l.second)}${l.offset}` : day;
}

/** A date from trip.yaml (a calendar day, no time). */
export const calendarDay = (d: Date) => localTime(d, 'UTC');

// --- what a post may show ---------------------------------------------------

/** From site.display and the post's hide_location. */
export function settings(post: Post) {
  const { location, time } = site.display;
  const place = Boolean(post.data.place) && !post.data.hide_location && location !== 'hidden';
  const coordinates = place && Boolean(post.data.coordinates) && location === 'coordinates';
  const clock = Boolean(post.data.time_zone) && time;
  return { place, coordinates, clock };
}

/** A post's name in lists and feeds: its title, or its local date. */
export const label = (post: Post) => post.data.title ?? longDate(postTime(post));

/** 45.441N 12.321E */
export function formatCoordinates({ lat, lon }: { lat: number; lon: number }): string {
  return `${Math.abs(lat).toFixed(3)}${lat >= 0 ? 'N' : 'S'} ${Math.abs(lon).toFixed(3)}${lon >= 0 ? 'E' : 'W'}`;
}

/** "3. Oktober 2026 – 6. Oktober 2026 · 3 Beiträge" under a trip's title. */
export function tripRange(trip: Trip, count: number): string {
  const { started_on, ended_on } = trip.data;
  if (!started_on) return t.posts(count);
  const end = ended_on ? longDate(calendarDay(ended_on)) : t.ongoing;
  return `${longDate(calendarDay(started_on))} – ${end} · ${t.posts(count)}`;
}
