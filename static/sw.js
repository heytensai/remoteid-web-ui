const CACHE = 'drone-tracker-v1';

const PRECACHE = [
  './',
  './static/css/style.css',
  './static/js/units.js',
  './static/js/api.js',
  './static/js/map.js',
  './static/js/ui.js',
  './static/favicon.svg',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

const NEVER_CACHE = ['/api/', '/manifest.json', '/sw.js'];

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (NEVER_CACHE.some((p) => url.pathname.includes(p))) {
    event.respondWith(fetch(event.request));
    return;
  }
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
