/* Service Worker של Firebase Cloud Messaging (FCM)
   מטפל בהתראות פוש כשהאפליקציה סגורה/ברקע — התראה מגיעה לטלפון כמו וואטסאפ. */
importScripts("https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/10.12.2/firebase-messaging-compat.js");

firebase.initializeApp({
  apiKey: "AIzaSyDPl_DjdAV873aHBBCoqnNk1YTZx6eG7kQ",
  authDomain: "gb201-e1c85.firebaseapp.com",
  projectId: "gb201-e1c85",
  storageBucket: "gb201-e1c85.firebasestorage.app",
  messagingSenderId: "92589759772",
  appId: "1:92589759772:web:8fb918b085df403bbe2ed9",
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
