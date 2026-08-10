/* Service Worker — BarberTor
   מטרות: התקנת PWA + הצגת התראות פוש (תזכורות / תור חדש).            */
const CACHE = "ug-barber-v106";
/* קליפת האפליקציה בלבד. קובצי ה-JS/CSS נטענים עם ‎?v=NN‎ (ראו tools/bump-version.sh)
   ולכן אין טעם לרשום אותם כאן — הם ייכנסו למטמון בטעינה הראשונה דרך ה-fetch,
   וכתובת חדשה בכל גרסה מבטיחה שלא יוגש קובץ ישן. */
const ASSETS = [
  "./",
  "./index.html",
  "./assets/img/icon.svg",
  "./assets/img/icon-192.png",
  "./assets/img/icon-512.png",
];

self.addEventListener("install", (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS).catch(() => {})));
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    // ניקוי מטמונים ישנים
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
    // "גשר עדכון" ללקוחות שכבר מותקנים: כשה-SW החדש תופס שליטה, מרעננים חלונות
    // פתוחים פעם אחת כדי שיטענו את הקוד החדש (עם ‎?v=NN‎). ה-SW הוא השכבה היחידה
    // שהדפדפן תמיד בודק מחדש, ולכן זה עובד גם על קוד ישן שאין בו בדיקת-עדכון.
    try {
      const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const c of wins) { if ("navigate" in c) { try { await c.navigate(c.url); } catch (e2) {} } }
    } catch (e3) {}
  })());
});

/* network-first עבור הקבצים כדי לקבל עדכונים, עם נפילה למטמון.
   config.js לעולם לא נשמר במטמון — שינויי מפתחות (EmailJS וכו') נכנסים מיד. */
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // אל תיגע בבקשות ל-Firebase/גופנים
  // config.js (מפתחות) ו-version.json (בדיקת עדכון) — תמיד מהרשת, לעולם לא מהמטמון
  const noStore = /\/config\.js$/.test(url.pathname) || /\/version\.json$/.test(url.pathname);
  e.respondWith(
    fetch(req)
      .then((res) => {
        if (!noStore) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        }
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
