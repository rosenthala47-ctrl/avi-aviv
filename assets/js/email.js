/* =========================================================================
   Email — שליחת מייל אישור הזמנה ללקוח דרך EmailJS.
   אופציונלי לחלוטין וחינמי (ללא כרטיס אשראי): אם לא הוגדרו מפתחות ב-config —
   התכונה כבויה וההזמנה ממשיכה לעבוד כרגיל.
   =========================================================================*/
window.UG = window.UG || {};
UG.Email = (function () {
  let ready = false, failed = false, loading = null;

  function cfg() { return (window.UG_CONFIG && UG_CONFIG.emailjs) || {}; }
  function configured() {
    const c = cfg();
    return !!(c.publicKey && c.serviceId && c.templateId);
  }
  function loadScript(src) {
    return new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src; s.onload = res; s.onerror = rej; document.head.appendChild(s);
    });
  }

  async function ensure() {
    if (ready) return true;
    if (failed || !configured()) return false;
    if (!loading) loading = (async () => {
      try {
        if (typeof emailjs === "undefined") {
          await loadScript("https://cdn.jsdelivr.net/npm/@emailjs/browser@4/dist/email.min.js");
        }
        emailjs.init({ publicKey: cfg().publicKey });
        ready = true;
        return true;
      } catch (e) {
        failed = true;
        console.warn("[UG] Email לא זמין:", e && e.message ? e.message : e);
        return false;
      }
    })();
    return loading;
  }

  // params — משתני התבנית ב-EmailJS (כולל to_email לנמען הדינמי)
  async function sendBooking(params) {
    if (!configured()) return false;
    const ok = await ensure();
    if (!ok) return false;
    try {
      await emailjs.send(cfg().serviceId, cfg().templateId, params);
      return true;
    } catch (e) {
      console.warn("[UG] שליחת מייל נכשלה:", e && (e.text || e.message) ? (e.text || e.message) : e);
      return false;
    }
  }

  return { configured, ensure, sendBooking };
})();
