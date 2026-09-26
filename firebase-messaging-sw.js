/* Service Worker של Firebase Cloud Messaging (FCM)
   מטפל בהתראות פוש כשהאפליקציה סגורה/ברקע — התראה מגיעה לטלפון כמו וואטסאפ. */
importScripts("https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/10.12.2/firebase-messaging-compat.js");

firebase.initializeApp({
  apiKey: "AIzaSyC_kyz97Ee0t42cDqfy4NcApxw14eceCQM",
  authDomain: "barbertor.firebaseapp.com",
  projectId: "barbertor",
  storageBucket: "barbertor.firebasestorage.app",
  messagingSenderId: "239278537111",
  appId: "1:239278537111:web:91867686ede5e9f2006cd2",
});

const messaging = firebase.messaging();

// הודעות מגיעות כ-data-only; אנחנו מציגים אותן כאן
messaging.onBackgroundMessage((payload) => {
  const d = (payload && payload.data) || {};
  self.registration.showNotification(d.title || "BarberTor", {
    body: d.body || "",
    icon: "assets/img/icon-192.png",
    badge: "assets/img/icon-192.png",
    dir: "rtl",
    lang: "he",
    tag: d.tag || undefined,
    renotify: true,
    vibrate: [90, 40, 90],
    data: d,
  });
});

// לחיצה על ההתראה — מיקוד/פתיחה של האפליקציה
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = new URL("./", self.location).href; // שורש האפליקציה
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) { if (c.url.startsWith(url) && "focus" in c) return c.focus(); }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});
