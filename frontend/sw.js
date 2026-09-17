// Advance the cache identity whenever the shell or a static asset changes.
// Navigation is network-first so an online release never requires manual
// cache clearing to replace an old shell.
const CACHE_NAME = "storylingo-static-v27";
const STATIC_ASSETS = ["/index.html", "/style.css?v=30", "/app.js?v=43", "/platform.js?v=16", "/services/api.js?v=1", "/services/escape.js?v=1", "/services/pagination.js?v=1", "/services/toast.js?v=1", "/services/workflow.js?v=1", "/services/bootstrap.js?v=1", "/services/announcements.js?v=1", "/services/notifications.js?v=1", "/services/audiobooks.js?v=1", "/services/public-navigation.js?v=3", "/services/reader.js?v=2", "/admin/admin-console.js?v=1", "/components/index.js?v=2", "/seo.js?v=14", "/release.js?v=1", "/manifest.webmanifest", "/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET" || event.request.url.includes("/api/")) return;
  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).then((response) => {
      if (response.ok) {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put("/index.html", copy));
      }
      return response;
    }).catch(() => caches.match("/index.html")));
    return;
  }
  event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request).then((response) => {
    if (response.ok) {
      const copy = response.clone();
      caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
    }
    return response;
  })));
});
