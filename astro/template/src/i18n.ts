/*
 * The labels of the site, in German and English. `lang` in site.config.ts
 * picks one. Add a language by adding an object of the same shape.
 */
import { site } from './site.config';

const en = {
  archive: 'Archive',
  places: 'Places',
  trips: 'Trips',
  feed: 'Feed',
  ongoing: 'ongoing',
  draft: 'Draft',
  everyPost: 'Every post',
  nothing: 'Nothing published here yet.',
  nothingTrip: 'Nothing published on this trip yet.',
  noPlaces: 'No places to show.',
  notFound: 'This page does not exist.',
  backHome: 'Back to the start page',
  placeNames: 'Place names',
  // "Built with <Astro>, imported from <Ortszeit>": the text around the two links
  builtWith: ['Built with ', ', imported from ', ''],
  posts: (n: number) => (n === 1 ? `${n} post` : `${n} posts`),
  monthShort: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
  monthLong: [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
  ],
  dayMonth: (day: number, month: string) => `${day} ${month}`,
  longDate: (day: number, month: string, year: number) => `${day} ${month} ${year}`,
};

const de: typeof en = {
  archive: 'Archiv',
  places: 'Orte',
  trips: 'Reisen',
  feed: 'Feed',
  ongoing: 'läuft noch',
  draft: 'Entwurf',
  everyPost: 'Alle Beiträge',
  nothing: 'Hier ist noch nichts veröffentlicht.',
  nothingTrip: 'Auf dieser Reise ist noch nichts veröffentlicht.',
  noPlaces: 'Keine Orte zu zeigen.',
  notFound: 'Diese Seite gibt es nicht.',
  backHome: 'Zur Startseite',
  placeNames: 'Ortsnamen',
  builtWith: ['Erstellt mit ', ', importiert aus ', ''],
  posts: (n: number) => (n === 1 ? `${n} Beitrag` : `${n} Beiträge`),
  monthShort: ['Jan.', 'Feb.', 'März', 'Apr.', 'Mai', 'Juni', 'Juli', 'Aug.', 'Sep.', 'Okt.', 'Nov.', 'Dez.'],
  monthLong: [
    'Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
    'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember',
  ],
  dayMonth: (day: number, month: string) => `${day}. ${month}`,
  longDate: (day: number, month: string, year: number) => `${day}. ${month} ${year}`,
};

export const t = site.lang === 'de' ? de : en;
