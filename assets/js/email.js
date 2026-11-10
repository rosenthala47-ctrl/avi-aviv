/* =========================================================================
   Email — שליחת מייל אישור הזמנה ללקוח דרך EmailJS.
   אופציונלי לחלוטין וחינמי (ללא כרטיס אשראי): אם לא הוגדרו מפתחות ב-config —
   התכונה כבויה וההזמנה ממשיכה לעבוד כרגיל.
   =========================================================================*/
window.UG = window.UG || {};
UG.Email = (function () {
  let ready = false, loading = null, lastError = "";

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
    if (!configured()) { lastError = "EmailJS not configured"; return false; }
    if (!loading) loading = (async () => {
      try {
        if (typeof emailjs === "undefined") {
          await loadScript("https://cdn.jsdelivr.net/npm/@emailjs/browser@4/dist/email.min.js");
        }
        emailjs.init(cfg().publicKey);
        ready = true;
        lastError = "";
        return true;
      } catch (e) {
        loading = null;
        lastError = "SDK: " + (e && e.message ? e.message : String(e));
        return false;
      }
    })();
    return loading;
  }

  async function sendBooking(params) {
    if (!configured()) return { sent: false, error: "not configured" };
    const ok = await ensure();
    if (!ok) return { sent: false, error: lastError || "SDK load failed" };
    try {
      await emailjs.send(cfg().serviceId, cfg().templateId, params);
      return { sent: true };
    } catch (e) {
      const msg = e && (e.text || e.message) ? (e.text || e.message) : String(e);
      return { sent: false, error: msg };
    }
  }

  if (configured()) ensure();

  return { configured, ensure, sendBooking };
})();
