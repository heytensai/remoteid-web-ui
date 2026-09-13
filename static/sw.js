const CACHE = 'drone-tracker-v4';

const PRECACHE = [
  '__URL_PREFIX__/',
  '__URL_PREFIX__/static/css/style.css',
  '__URL_PREFIX__/static/js/units.js',
  '__URL_PREFIX__/static/js/api.js',
  '__URL_PREFIX__/static/js/map.js',
  '__URL_PREFIX__/static/js/ui.js',
  '__URL_PREFIX__/static/vendor/leaflet/leaflet.css',
  '__URL_PREFIX__/static/vendor/leaflet/leaflet.js',
  '__URL_PREFIX__/static/vendor/flatpickr/flatpickr.min.css',
  '__URL_PREFIX__/static/vendor/flatpickr/flatpickr.min.js',
  '__URL_PREFIX__/static/vendor/font-awesome/css/all.min.css',
  '__URL_PREFIX__/static/vendor/font-awesome/webfonts/fa-brands-400.woff2',
  '__URL_PREFIX__/static/vendor/font-awesome/webfonts/fa-regular-400.woff2',
  '__URL_PREFIX__/static/vendor/font-awesome/webfonts/fa-solid-900.woff2',
  '__URL_PREFIX__/static/favicon.svg',
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

  // Bypass SW for cross-origin requests (map tile servers)
  if (url.origin !== self.location.origin) {
    return;
  }

  // Bypass SW for API and config endpoints
  if (NEVER_CACHE.some((p) => url.pathname.includes(p))) {
    event.respondWith(fetch(event.request, { cache: 'no-store' }));
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
    }).catch(() => caches.match(event.request, { ignoreSearch: true }))
  );
});

