/* =========================================================================
   FCM — רישום המכשיר לקבלת התראות פוש גם כשהאפליקציה סגורה.
   כל התהליך אופציונלי: אם אין vapidKey / לא בענן / אין הרשאה — פשוט מדלגים,
   והאפליקציה ממשיכה לעבוד עם ההתראות המקומיות (כשהיא פתוחה).
   =========================================================================*/
window.UG = window.UG || {};
UG.FCM = (function () {
  let started = false;
  let inFlight = false;

  function loadScript(src) {
    return new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src; s.onload = res; s.onerror = rej; document.head.appendChild(s);
    });
  }

  async function start(userId, isOwner) {
    if (inFlight) return;
    inFlight = true;
    try {
      const vapidKey = UG_CONFIG.vapidKey;
      if (!vapidKey) return;                                   // FCM לא מוגדר
      if (!UG.Store || UG.Store.mode !== "cloud") return;      // רק במצב ענן
      if (!("serviceWorker" in navigator) || !("Notification" in window)) return;
      if (Notification.permission !== "granted") return;
      if (typeof firebase === "undefined") return;             // Firebase לא נטען

      if (!firebase.messaging) {
        await loadScript("https://www.gstatic.com/firebasejs/10.12.2/firebase-messaging-compat.js");
      }
      if (firebase.messaging.isSupported && !firebase.messaging.isSupported()) return;

      const messaging = firebase.messaging();
      const reg = await navigator.serviceWorker.register("firebase-messaging-sw.js", { scope: "./fcm-scope/" });
      const token = await messaging.getToken({ vapidKey: vapidKey, serviceWorkerRegistration: reg });
      if (token) {
        await UG.Store.savePushToken(userId, token, !!isOwner);
        started = true;
      }

      if (!started || !messaging.__onmsg) {
        messaging.__onmsg = true;
        messaging.onMessage((payload) => {
          const d = (payload && (payload.data || payload.notification)) || {};
          if (UG.Notify) UG.Notify.show(d.title || "BarberTor", d.body || "", { tag: d.tag });
        });
      }
    } catch (e) {
      console.warn("[UG] FCM לא זמין:", e && e.message ? e.message : e);
    } finally {
      inFlight = false;
    }
  }

  return { start, get ready() { return started; } };
})();
