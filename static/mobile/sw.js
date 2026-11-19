// Keeps only Nova's app shell (the page and its icons) so the home-screen app opens at once.
// It never touches /m/* - messages, events, pairing and status always go to the laptop, never through a cache.
const SHELL = "nova-shell-v1";
const FILES = ["/", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"];

self.addEventListener("install", e => { e.waitUntil(caches.open(SHELL).then(c => c.addAll(FILES)).then(() => self.skipWaiting())); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== SHELL).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/m/") || !FILES.includes(url.pathname)) return;   // not ours: the network
  // network first (a changed page shows up at once), the saved copy when the laptop can't be reached
  e.respondWith(fetch(e.request).then(r => { const copy = r.clone(); caches.open(SHELL).then(c => c.put(e.request, copy)); return r; })
    .catch(() => caches.match(e.request)));
});
