// Level-1 offline: the app shell opens without signal, and the last catalogue is shown as an estimate.
const SHELL = "kirana-shell-v2";
const API = "kirana-api-v2";
const FILES = ["/", "/static/app.css", "/static/app.js", "/static/i18n.js", "/static/icon.svg", "/static/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => ![SHELL, API].includes(k)).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("message", (e) => {
  // On logout the page asks us to drop cached prices so the next person on a shared phone never sees them.
  if (e.data === "clear-api") e.waitUntil(caches.delete(API));
});
self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname === "/api/catalogue") {
    e.respondWith(fetch(req).then((r) => {
      if (r.ok) { const copy = r.clone(); caches.open(API).then((c) => c.put(req, copy)); }
      return r;
    }).catch(() => caches.match(req).then((m) => m || Response.error())));
    return;
  }
  if (url.pathname.startsWith("/api/")) return; // everything else is always live
  e.respondWith(fetch(req).then((r) => {
    if (r.ok && (FILES.includes(url.pathname))) { const copy = r.clone(); caches.open(SHELL).then((c) => c.put(req, copy)); }
    return r;
  }).catch(() => caches.match(req).then((m) => m || caches.match("/"))));
});
