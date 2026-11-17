/* Service worker: makes the site installable, keeps a copy of the app shell for when the
   connection drops, and shows push notifications (daily reminder, nudges, new articles). */
const CACHE = "mentor-shell-v2";
const OFFLINE = "/static/offline.html";
const SHELL = ["/", "/static/style.css", "/static/app.js", "/manifest.webmanifest", OFFLINE,
  "/static/icons/icon-192.png", "/static/icons/badge-96.png"];

// When the mentor's computer is asleep or reconnecting, the tunnel answers with its own error page
// (404/502/503 and an ngrok-error-code header). Learners should see our friendly page instead.
const isTunnelError = (res) => res.headers.has("ngrok-error-code") || res.status === 404 || res.status >= 500;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Network first (so learners always get the latest version); cached copy only when offline.
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  const page = e.request.mode === "navigate";
  e.respondWith(
    fetch(e.request).then((res) => {
      if (page && isTunnelError(res)) return caches.match(OFFLINE).then((off) => off || res);
      if (res.ok && (SHELL.includes(url.pathname) || url.pathname.startsWith("/static/"))) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
      }
      return res;
    }).catch(() => page ? caches.match(OFFLINE) : caches.match(e.request))
  );
});

self.addEventListener("push", (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch (_) { data = { body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(data.title || "Data Architect Mentor", {
    body: data.body || "",
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/badge-96.png",
    tag: data.tag || "mentor",          // a newer reminder replaces an older one instead of stacking
    renotify: !!data.tag,
    data: { url: data.url || "/#home" },
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = new URL(e.notification.data?.url || "/#home", location.origin).href;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
    for (const w of wins) {
      if (new URL(w.url).origin === location.origin) { w.navigate(target); return w.focus(); }
    }
    return self.clients.openWindow(target);
  }));
});
