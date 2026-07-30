/* =========================================================================
   Email — שליחת מייל אישור הזמנה ללקוח דרך EmailJS.
   אופציונלי לחלוטין וחינמי (ללא כרטיס אשראי): אם לא הוגדרו מפתחות ב-config —
   התכונה כבויה וההזמנה ממשיכה לעבוד כרגיל.
   =========================================================================*/
window.UG = window.UG || {};
UG.Email = (function () {
  let ready = false, loading = null;

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
    if (!configured()) return false;
    if (!loading) loading = (async () => {
      try {
        if (typeof emailjs === "undefined") {
          await loadScript("https://cdn.jsdelivr.net/npm/@emailjs/browser@4/dist/email.min.js");
        }
        emailjs.init(cfg().publicKey);
        ready = true;
        console.log("[UG] EmailJS ready");
        return true;
      } catch (e) {
        loading = null;
        console.warn("[UG] Email SDK load failed:", e && e.message ? e.message : e);
        return false;
      }
    })();
    return loading;
  }

  async function sendBooking(params) {
    if (!configured()) return false;
    const ok = await ensure();
    if (!ok) return false;
    try {
      await emailjs.send(cfg().serviceId, cfg().templateId, params);
      console.log("[UG] Email sent to", params.to_email);
      return true;
    } catch (e) {
      console.warn("[UG] Email send failed:", e && (e.text || e.message) ? (e.text || e.message) : e);
      return false;
    }
  }

  if (configured()) ensure();

  return { configured, ensure, sendBooking };
})();
