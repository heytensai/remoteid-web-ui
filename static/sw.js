const CACHE = 'drone-tracker-v3';

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
  console.log('SW install starting, precache:', PRECACHE);
  event.waitUntil(
    caches.open(CACHE).then((cache) => {
      return cache.addAll(PRECACHE);
    }).then(() => {
      console.log('SW precache done, skipWaiting');
      self.skipWaiting();
    }).catch((err) => {
      console.error('SW install failed:', err);
      throw err;
    })
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

  // Bypass SW for cross-origin requests (CDN resources)
  if (url.origin !== self.location.origin) {
    return;
  }

  // Bypass SW for API and config endpoints
  if (NEVER_CACHE.some((p) => url.pathname.includes(p))) {
    event.respondWith(fetch(event.request));
    return;
  }

  // Network-first for same-origin static assets, fall back to cache when offline
  event.respondWith(
    fetch(event.request).then((response) => {
      // Cache successful same-origin responses for offline use
      if (response.ok && url.origin === self.location.origin) {
        const clone = response.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, clone));
      }
      return response;
    }).catch(() => caches.match(event.request))
  );
});

