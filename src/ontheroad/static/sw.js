// sw.js — app-shell caching ONLY.
// Never caches /api/* or WebSocket traffic, and never transcript data:
// the shell contains no user data, so it is safe to cache offline.

const CACHE = "otr-shell-v1";
const SHELL = [
  "/",
  "/index.html",
  "/styles.css",
  "/app.js",
  "/api.js",
  "/chat.js",
  "/manifest.webmanifest",
  "/vendor/marked.min.js",
  "/vendor/purify.min.js",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  // Never intercept API, preview, or health traffic — network only.
  if (
    event.request.method !== "GET" ||
    url.origin !== self.location.origin ||
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/preview/") ||
    url.pathname === "/healthz"
  ) {
    return; // fall through to the network
  }
  // Shell assets: network-first (fresh code when online), cache fallback offline.
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(event.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(event.request, { ignoreSearch: url.pathname === "/" }))
  );
});
