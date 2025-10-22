// Museerr Smart Service Worker
const CACHE_VERSION = 'v3-' + Date.now(); // Forces update each new build
const CACHE_NAME = `museerr-${CACHE_VERSION}`;

const APP_SHELL = [
  '/',
  '/search',
  '/static/style.css',
  '/static/app.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/manifest.webmanifest'
];

// ------------------------------
// Install — Cache core assets
// ------------------------------
self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(APP_SHELL))
      .catch(err => console.warn('[SW] Install failed:', err))
  );
});

// ------------------------------
// Activate — Clean old caches
// ------------------------------
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.map(k => {
        if (!k.startsWith(CACHE_NAME.split('-')[0])) {
          console.log('[SW] Deleting old cache:', k);
          return caches.delete(k);
        }
      })
    ))
    .then(() => self.clients.claim())
  );
});

// ------------------------------
// Fetch Handler — Smarter Strategy
// ------------------------------
function isHTML(request) {
  return request.headers.get('accept')?.includes('text/html');
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  if (url.pathname.startsWith('/api') || url.pathname.startsWith('/ws')) return;

  if (isHTML(req)) {
    event.respondWith(
      fetch(req)
        .then(resp => {
          const copy = resp.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(req, copy));
          return resp;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      fetch(req)
        .then(resp => {
          const copy = resp.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(req, copy));
          return resp;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  event.respondWith(
    caches.match(req)
      .then(cached => cached || fetch(req))
      .catch(() => caches.match('/'))
  );
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
