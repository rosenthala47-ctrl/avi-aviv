/* Service Worker — BarberTor
   מטרות: התקנת PWA + הצגת התראות פוש (תזכורות / תור חדש).            */
const CACHE = "ug-barber-v32";
const ASSETS = [
  "./",
  "./index.html",
  "./config.js",
  "./assets/styles.css",
  "./assets/js/util.js",
  "./assets/js/store.js",
  "./assets/js/auth.js",
  "./assets/js/notify.js",
  "./assets/js/fcm.js",
  "./assets/js/email.js",
  "./assets/js/app.js",
  "./assets/img/icon.svg",
  "./assets/img/icon-192.png",
  "./assets/img/icon-512.png",
  "./manifest.json",
];

self.addEventListener("install", (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS).catch(() => {})));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

/* network-first עבור הקבצים כדי לקבל עדכונים, עם נפילה למטמון */
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // אל תיגע בבקשות ל-Firebase/גופנים
  e.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(req).then((r) => r || caches.match("./index.html")))
  );
});

/* לחיצה על התראה — מיקוד/פתיחה של האפליקציה */
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) { if ("focus" in c) return c.focus(); }
      if (self.clients.openWindow) return self.clients.openWindow("./");
    })
  );
});
