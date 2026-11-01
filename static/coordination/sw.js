// BuildLink service worker: keeps the site tools usable without signal.
// - Static files: fetched from the network first; the cache is the offline fallback.
//   The ?v= query is part of the cache key, so bumping it in base.html replaces old files.
// - Pages: always fetched from the network first. Only the on-site tools listed in
//   OFFLINE_PAGES are saved, so they still open when there is no signal.
// - Form posts are never cached here; operations.js queues them in IndexedDB instead.
const CACHE = 'buildlink-v2';
const OFFLINE_URL = '/offline/';
const OFFLINE_PAGES = [
  /^\/dashboard\/$/,
  /^\/attendance\/clock\/$/,
  /^\/projects\/\d+\/attendance\/scan\/$/,
  /^\/projects\/\d+\/reports\/new\/$/,
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.add(OFFLINE_URL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(CACHE).then((cache) => fetch(request).then((response) => {
        if (response.ok) cache.put(request, response.clone());
        return response;
      }).catch(async () => (await cache.match(request)) || cache.match(request, { ignoreSearch: true }))),
    );
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).then((response) => {
        const savable = response.ok && !response.redirected && OFFLINE_PAGES.some((pattern) => pattern.test(url.pathname));
        if (savable) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      }).catch(async () => (await caches.match(request)) || caches.match(OFFLINE_URL)),
    );
  }
});
