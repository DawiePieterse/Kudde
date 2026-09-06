// App-shell cache so Kudde Field still loads with zero signal. Data (the
// animal list, dashboard, event recording) always goes over the network
// when available via api.js/idb.js - this only guarantees the UI itself is
// installable/offline.
//
// Simpler than Boord's field service worker: stale-while-revalidate per
// file rather than refreshing the whole shell atomically. That means an
// update could in theory land with one file ahead of another for a moment -
// Boord hit this in production (see its service-worker.js) and fixed it by
// refreshing the shell as one unit. Worth doing here too once Kudde ships
// updates to real farms.
const CACHE_PREFIX = "kudde-field-";
const CACHE = "kudde-field-v1";
const SHELL = [
  "./",
  "./index.html",
  "./app.js",
  "./idb.js",
  "./manifest.json",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "../shared/styles.css",
  "../shared/api.js",
  "../shared/ptr.js",
  "../shared/tailwind.js",
  "../shared/vendor/fontawesome/css/all.min.css",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

// Drop only THIS screen's older caches - CacheStorage is shared per-origin,
// so deleting every non-matching key would wipe the admin app's offline
// shell the moment someone opens both on the same device.
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k.startsWith(CACHE_PREFIX) && k !== CACHE).map((k) => caches.delete(k))
    ))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/")) return; // never cache API calls
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;

  // Page loads are cached by path only - a cache lookup matches the query
  // string too, so a screen opened with one on the end would never find its
  // cached shell and would fail offline.
  const isPageLoad = event.request.mode === "navigate";
  const cacheKey = isPageLoad ? url.origin + url.pathname : event.request;

  const update = fetch(event.request)
    .then(async (res) => {
      if (res.ok) {
        const cache = await caches.open(CACHE);
        await cache.put(cacheKey, res.clone());
      }
      return res;
    })
    .catch(() => null); // offline: the cached copy below is the answer
  event.waitUntil(update);

  event.respondWith(
    caches.open(CACHE)
      .then((cache) => cache.match(cacheKey))
      .then((cached) => cached || update.then((res) => res || Response.error()))
  );
});
