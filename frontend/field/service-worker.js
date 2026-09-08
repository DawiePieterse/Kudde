// App-shell cache so Kudde Field still loads with zero signal. Data (the
// animal list, dashboard, event recording) always goes over the network
// when available via api.js/idb.js - this only guarantees the UI itself is
// installable/offline.
const CACHE_PREFIX = "kudde-field-";
const CACHE = "kudde-field-v2";
const REVALIDATE_TIMEOUT_MS = 10000;
const SHELL = [
  "./",
  "./index.html",
  "./app.js",
  "./idb.js",
  "./manifest.json",
  "./icons/icon-180.png",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "../shared/styles.css",
  "../shared/api.js",
  "../shared/ptr.js",
  "../shared/tailwind.js",
  "../shared/vendor/fontawesome/css/all.min.css",
  // The icon font itself, not just the stylesheet that asks for it. Every
  // control on these screens is a Font Awesome glyph - the event buttons,
  // the offline banner's wifi symbol, the activity feed - so a shell cached
  // without this loads offline as a page of empty boxes.
  "../shared/vendor/fontawesome/webfonts/fa-solid-900.woff2",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

// Drop only THIS screen's older caches - CacheStorage is shared per-origin,
// so deleting every non-matching key would wipe the other app's offline
// shell the moment someone opens both on the same device.
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k.startsWith(CACHE_PREFIX) && k !== CACHE).map((k) => caches.delete(k))
    ))
  );
  self.clients.claim();
});

// Absolute URLs for everything in SHELL, so the fetch handler can tell a
// shell request from an ordinary one. Resolved against this file's own
// location, so "./" is this screen's page and "../shared/x" the shared copy.
const SHELL_URLS = new Set(SHELL.map((path) => new URL(path, self.location.href).toString()));

let shellRefresh = null;

// Re-fetch the WHOLE shell and commit it in one go.
//
// Writing each revalidated file back on its own - which is what this used to
// do - can leave a device holding one release's index.html beside another
// release's app.js. That is not a gentle degradation: the older JavaScript
// reaches for an element the newer HTML no longer has, throws before
// anything renders, and the screen paints white. It happened to Boord's
// Admin app on a farm server on 2026-09-04, and from the outside it was
// indistinguishable from the server being down. Kudde's shell is shipped by
// the same installer to the same kind of always-open device, so it is the
// same failure waiting for the first update.
//
// Nothing is committed unless every file arrives, so a refresh cut short by
// a dropped connection leaves the previous, consistent set in place rather
// than a half-updated one.
function refreshShell() {
  if (!shellRefresh) {
    const urls = [...SHELL_URLS];
    shellRefresh = Promise.all(urls.map((u) => fetch(u, { cache: "reload" }).catch(() => null)))
      .then(async (responses) => {
        if (responses.some((res) => !res || !res.ok)) return;
        const cache = await caches.open(CACHE);
        await Promise.all(responses.map((res, i) => cache.put(urls[i], res)));
      })
      .catch(() => { /* keep the shell that is already there */ })
      .then(() => { shellRefresh = null; });
  }
  return shellRefresh;
}

// Whether the copy just fetched is a different build from the cached one.
//
// Compared by validator rather than by body: this runs on every shell
// request, and reading two copies of app.js to compare them would cost more
// than the caching saves. Starlette's StaticFiles sends both an ETag and a
// Last-Modified, so on this server the first check answers.
//
// When neither is available the answer is "unchanged", deliberately.
// Guessing "changed" would re-fetch the entire shell on every single
// request, which is a far worse failure than a stale one - and staleness is
// already visible, since every screen prints its version in the header.
async function shellFileChanged(cache, url, fresh) {
  const cached = await cache.match(url);
  if (!cached) return true;
  for (const header of ["etag", "last-modified", "content-length"]) {
    const before = cached.headers.get(header);
    const after = fresh.headers.get(header);
    if (before && after) return before !== after;
  }
  return false;
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/")) return; // never cache API calls
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;

  // Page loads are cached by path only - a cache lookup matches the query
  // string too, so a screen opened with one on the end would never find its
  // cached shell and would fail offline. It also stops one cache entry
  // piling up per distinct query string.
  const isPageLoad = event.request.mode === "navigate";
  const cacheKey = isPageLoad ? url.origin + url.pathname : event.request;
  // A shell file is looked up by URL either way; only the key differs.
  const shellUrl = isPageLoad ? cacheKey : url.href;
  const isShell = SHELL_URLS.has(shellUrl);

  // The revalidation carries a deadline, because on an unreachable network
  // these background fetches never settle, and a browser allows only a
  // handful of connections per host - uncapped they pile up and starve the
  // app's own API requests of sockets, which on the field app means the
  // offline outbox stops draining.
  const revalidateAbort = new AbortController();
  const revalidateTimer = setTimeout(() => revalidateAbort.abort(), REVALIDATE_TIMEOUT_MS);
  const update = fetch(event.request, { signal: revalidateAbort.signal })
    .then(async (res) => {
      if (res.ok) {
        const cache = await caches.open(CACHE);
        if (isShell) {
          // One shell file moving means the whole shell moved. Refresh them
          // together or not at all - see refreshShell above for why.
          if (await shellFileChanged(cache, shellUrl, res)) await refreshShell();
        } else {
          await cache.put(cacheKey, res.clone());
        }
      }
      return res;
    })
    .catch(() => null) // offline: the cached copy below is the answer
    .finally(() => clearTimeout(revalidateTimer));
  event.waitUntil(update);

  // Matched against THIS screen's cache, not the global caches.match(),
  // which searches every cache on the origin and would happily answer with
  // the other screen's stale copy of a shared file - both cache api.js.
  event.respondWith(
    caches.open(CACHE)
      .then((cache) => cache.match(cacheKey))
      .then((cached) => cached || update.then((res) => res || Response.error()))
  );
});
