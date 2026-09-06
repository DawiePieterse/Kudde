// App-shell cache so Kudde Admin installs as a PWA and survives a brief
// network blip. Unlike the field app there's no offline data queue here -
// see app.js for why - this only caches the UI shell itself.
const CACHE_PREFIX = "kudde-admin-";
const CACHE = "kudde-admin-v1";
const SHELL = [
  "./",
  "./index.html",
  "./app.js",
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
    .catch(() => null);
  event.waitUntil(update);

  event.respondWith(
    caches.open(CACHE)
      .then((cache) => cache.match(cacheKey))
      .then((cached) => cached || update.then((res) => res || Response.error()))
  );
});
