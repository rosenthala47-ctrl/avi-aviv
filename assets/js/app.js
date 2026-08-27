/* =========================================================================
   App — ניהול מסכים, תצוגה וחיווט אירועים
   =========================================================================*/
(function () {
  // הגנה מפני ריצה כפולה: רשת הביטחון ב-index.html עשויה לטעון מחדש את app.js
  // אם הטעינה הראשונה נכשלה (תקלת רשת חולפת). כאן מוודאים שהקוד לא ירוץ פעמיים.
  if (window.__ugAppInit) return;
  window.__ugAppInit = true;
  const u = UG.util;
  const Store = UG.Store;
  const Notify = UG.Notify;
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = u.escapeHtml;

  /* גרסת האפליקציה — מוצגת בהגדרות כדי לוודא שקיבלתם את העדכון האחרון.
     יש לעדכן יחד עם CACHE ב-sw.js. */
  const APP_VERSION = "149";

  /* ---------- זיהוי המספרה מהקישור (רב-משתמשי) ---------- */
  function resolveShopId() {
    let h = (location.hash || "").replace(/^#/, "").trim().toLowerCase();
    if (h === "new" || h === "signup") return "__new__";   // מסך הרשמה/פתיחה
    h = h.replace(/[^a-z0-9-]/g, "");
    if (h) return h;                                        // מספרה לפי כתובת אישית (כולל #main = אורי)
    // בלי כתובת אישית: בדומיין הישן של אורי מציגים את המספרה שלו (תאימות לקישורים קיימים).
    if ((location.hostname || "").toLowerCase().indexOf("ori-grushko") !== -1) return "main";
    // בדומיין המוצר (פתיחת האפליקציה): אם למכשיר יש מספרה בבעלותו — פותחים ישר
    // את הניהול שלה (כניסת הספר, בלי צורך להקליד כתובת). אחרת — מסך הפתיחה של BarberTor.
    // חשוב: לעולם לא פותחים דרך האפליקציה עמוד הזמנה של לקוח — זה שמור לקישור ייעודי בלבד.
    try { const mine = (localStorage.getItem("ug_my_shop") || "").trim(); if (/^[a-z0-9-]+$/.test(mine)) return mine; } catch (e) {}
    // לקוח שהתקין את האפליקציה מקישור של מספרה: ההפעלה מהאייקון מגיעה בלי כתובת
    // (start_url במניפסט), לכן חוזרים למספרה האחרונה שנצפתה במכשיר במקום למסך פתיחה.
    try { const last = (localStorage.getItem("ug_last_shop") || "").trim(); if (/^[a-z0-9-]+$/.test(last)) return last; } catch (e) {}
    return "__new__";
  }
  const SHOP = resolveShopId();
  const AUTHKEY = "ug_owner_auth__" + SHOP;
  const ROUTEKEY = "ug_route__" + SHOP;
  // אם הגענו למספרה שבבעלות המכשיר דרך פתיחת האפליקציה (כתובת ריקה) — תמיד במצב ניהול,
  // כדי שהספר לא ינחת בטעות בעמוד הלקוח. מצב לקוח מתקבל רק דרך קישור עם כתובת (#handle).
  try {
    if (!(location.hash || "").replace(/^#/, "").trim() &&
        (localStorage.getItem("ug_my_shop") || "").trim() === SHOP &&
        SHOP !== "__new__" && SHOP !== "main") {
      localStorage.setItem(ROUTEKEY, "owner");
    }
  } catch (e) {}

  /* ---------- תאימות למעטפת אפליקציה (Capacitor / Cordova / WebView) ---------- */
  // האם רצים בתוך מעטפת אפליקציה נייטיב (ולא בדפדפן רגיל)
  function isNativeShell() {
    return !!(window.Capacitor || window.cordova) || !/^https?:$/.test(location.protocol);
  }
  // מעטפת Cordova בלבד (לא Capacitor) — פתיחת קישורים חיצוניים דורשת "_system"
  function isCordovaOnly() {
    return !!window.cordova && !window.Capacitor;
  }
  // בסיס הכתובת לשיתוף: מעדיף publicBaseUrl מה-config (חובה במעטפת אפליקציה),
  // אחרת הכתובת הנוכחית (דפדפן רגיל).
  function shareBase() {
    const cfg = ((window.UG_CONFIG && UG_CONFIG.publicBaseUrl) || "").trim().replace(/\/+$/, "");
    if (cfg) return cfg + "/";
    return location.origin + location.pathname;
  }
  function clientLink() {
    // כולל #main גם למספרה של אורי — כדי שהקישור יעבוד גם בדומיין המוצר (שם הכתובת הריקה = מסך פתיחה)
    return shareBase() + "#" + SHOP;
  }
  /* האם המספרה הזו משתמשת במודל החדש מבוסס-חשבון (זיהוי חובה ללקוח + אבטחת ספר)?
     נשלט מ-config.authShops. "*" = כל המספרות. */
  function newAuthShop() {
    const list = (window.UG_CONFIG && UG_CONFIG.authShops) || [];
    return list.indexOf("*") !== -1 || list.indexOf(SHOP) !== -1;
  }

  /* עמוד לקוח מצומצם: כל התוכן (מוצרים/ביקורות/גלריה/רשתות) בעמוד "בית" אחד,
     עם כפתור "הזמנת תור" שמוביל לעמוד ההזמנה. נבדק על "try" ואושר — מופעל
     כעת על כל המספרות. */
  function condensedClient() {
    return true;
  }

  /* האם להציג מקטע בעמוד הלקוח. ברירת מחדל: מוצג. הספר יכול לכבות מקטעים
     מ״הגדרות → מה מוצג בעמוד הלקוח״ (נשמר כ-false על ה-shop). */
  function cShow(st, key) { return !(st && st.shop && st.shop[key] === false); }

  /* רקע/לוגו — נטענים מצומת המדיה הנפרד (טעינה ברקע, לא חוסמת פתיחה). מעדיפים
     את המדיה, ונופלים-לאחור לערך הישן שבתוך המספרה (עד למיגרציה) — כך תמונה
     לעולם לא נעלמת, וגם מספרות ותיקות שעדיין לא הועברו מוצגות כרגיל. */
  function shopMediaVal(st, kind) {
    const m = (Store.getMedia && Store.getMedia()) || {};
    return (m[kind] || (st && st.shop && st.shop[kind]) || "");
  }
  function shopCover(st) { return shopMediaVal(st, "cover"); }
  function shopLogo(st) { return shopMediaVal(st, "logo"); }

  /* פרטי הלקוח של תור (שם/טלפון/מייל). במספרה מאובטחת הם נשמרים בצומת פרטי שרק
     הבעלים קורא; כאן מעדיפים אותם, ונופלים-לאחור לערך הישן שבתוך התור (מספרות
     שאינן מאובטחות, ותורים ישנים). כך הבעלים תמיד רואה את השם, והצומת הציבורי
     נשאר בלי פרטים מזהים. עבור לא-בעלים (או לקוח) הצומת הפרטי ריק → מוחזר ריק. */
  function bkPriv(b) { return (b && Store.getBookingPriv && Store.getBookingPriv(b.id)) || null; }
  function bkName(b) { const p = bkPriv(b); return (p && p.name) || (b && b.userName) || ""; }
  function bkPhone(b) { const p = bkPriv(b); return (p && p.phone) || (b && b.phone) || ""; }
  function bkEmail(b) { const p = bkPriv(b); return (p && p.email) || (b && b.email) || ""; }

  /* דף מנהל מסודר: רשימת שורות אחידה עם ניווט פנימי + סרגל לשוניות מצומצם.
     נבדק על "try" ומאוגוסט 2026 חל על כל המספרות. */
  function tidyOwner() { return true; }

  /* שורת רשימה אחידה בדף המנהל המסודר: אייקון צבעוני, כותרת, תת-כותרת,
     ערך נוכחי וחץ. o.img = תמונה במקום אמוג׳י; o.nav = מאפייני הניווט;
     o.ltr = הערך נכתב משמאל לימין (טווח שעות, אחרת העברית הופכת אותו). */
  function setRow(o) {
    const bg = o.color || "var(--surface-3)";
    const ico = o.img
      ? `<span class="sr-ico sr-ico-img" style="background:${bg}"><img src="${o.img}" alt=""></span>`
      : `<span class="sr-ico" style="background:${bg}">${o.ico || ""}</span>`;
    return `<button class="set-row" ${o.nav || ""}>${ico}
      <span class="sr-body"><span class="sr-label">${esc(o.label)}</span>${o.sub ? `<span class="sr-sub">${esc(o.sub)}</span>` : ""}</span>
      ${o.val ? `<span class="sr-val"${o.ltr ? ` dir="ltr"` : ""}>${esc(o.val)}</span>` : ""}
      <span class="sr-chev">‹</span>
    </button>`;
  }
  // כותרת עמוד-משנה + כפתור חזרה
  function subBack(label) {
    return `<button class="btn btn-ghost btn-sm home-back" data-act="sub-back">‹ ${esc(label || "חזרה")}</button>`;
  }
  // מצב פתיחה של קבוצות ההגדרות (נשמר בזיכרון בין רינדורים)
  const setGroupOpen = { biz: true };
  // פתיחת כתובת חיצונית בצורה שתעבוד בדפדפן ובכל מעטפת (מפה/יומן/וואטסאפ)
  function openExternal(url) {
    try { if (isCordovaOnly()) { window.open(url, "_system"); return; } } catch (e) {}
    window.open(url, "_blank", "noopener");
  }

  /* ---------- מצב תצוגה מקומי (לא נשמר בשרת) ---------- */
  const view = {
    route: (function () { const r = localStorage.getItem(ROUTEKEY); return r === "owner" || r === "client" ? r : "client"; })(), // client | owner
    clientTab: (function () { const def = condensedClient() ? "home" : "book"; try { const t = localStorage.getItem("ug_ctab__" + SHOP); return ["book", "gallery", "products", "reviews", "mine", "home"].includes(t) ? t : def; } catch (e) { return def; } })(),   // נשמר כדי לא לאבד מיקום ברענון
    ownerTab: (function () { try { return localStorage.getItem("ug_otab__" + SHOP) || "cal"; } catch (e) { return "cal"; } })(),  // cal | hours | services | bookings | clients | report | publish | settings
    settingsPage: null,  // במבנה המסודר: קטגוריית הגדרות פתוחה (business/booking/brand/...) או null=רשימה
    settingsItem: null,  // הפריט הפתוח בתוך הקטגוריה (רמה שלישית) או null=רשימת הפריטים
    subPage: null,       // עמוד-משנה בתוך לשוניות הניהול (שעות/פרסום) או null=הרשימה
    selService: null,
    selStaff: "",        // ספר מועדף שהלקוח בחר (בקשה בלבד)
    selDate: null,       // יום נבחר בצד הלקוח
    selSlot: null,
    rescheduleId: null,  // מזהה תור בעת שינוי מועד (החלפת מועד לתור קיים)
    oDate: null,         // יום נבחר בצד הבעלים (תצוגת יומן)
    statMonth: null,     // חודש נבחר בדוח ("YYYY-MM")
    onboarding: false,   // מסך פתיחת מספרה
    notFound: false,     // מספרה לא קיימת
  };
  let ownerSeen = null;     // Set של מזהי תורים שהבעלים כבר ראה (זיהוי תור חדש)
  let clientCancelSeen = null;   // Set של תורים מבוטלים שהלקוח כבר טופלו (זיהוי ביטול חדש)
  let ownerCancelSeen = null;    // Set של ביטולים ע״י לקוחות שהבעלים כבר קיבל עליהם התראה
  let authAvail = false;    // האם התחברות מאובטחת (Firebase Auth) זמינה
  let spamDismissed = 0;     // timestamp שבו הבעלים סגר את באנר הספאם
  let identity = loadIdentity();

  // כניסת מנהל נסתרת — 3 הקשות רצופות על הלוגו
  let logoTaps = 0, logoTapTimer = null;
  /* תצוגת לקוח לבעלים — לראות בדיוק מה הלקוחות רואים, בלי להתנתק מהניהול.
     זהו מצב זמני בזיכרון (view.ownerPreview); רענון מחזיר לניהול. */
  function previewAsClient() {
    view.ownerPreview = true;
    view.route = "client";
    view.clientTab = "book";
    render();
    try { window.scrollTo(0, 0); } catch (e) {}
  }
  function exitPreview() {
    view.ownerPreview = false;
    view.route = "owner";
    render();
  }

  function onLogoTap() {
    // בתצוגת לקוח של הבעלים — לחיצה על הלוגו מחזירה מיד לניהול
    if (view.ownerPreview) { exitPreview(); return; }
    logoTaps++;
    clearTimeout(logoTapTimer);
    logoTapTimer = setTimeout(() => { logoTaps = 0; }, 1200);
    if (logoTaps >= 3) { logoTaps = 0; clearTimeout(logoTapTimer); promptOwner(); }
  }

  function loadIdentity() {
    try {
      const i = JSON.parse(localStorage.getItem("ug_identity") || "null");
      if (i && i.userId) return i;
    } catch (e) {}
    const fresh = { userId: u.uid(), firstName: "", lastName: "", name: "", phone: "" };
    localStorage.setItem("ug_identity", JSON.stringify(fresh));
    return fresh;
  }
  function saveIdentity() { localStorage.setItem("ug_identity", JSON.stringify(identity)); }

  /* מניפסט דינמי לכל מספרה — הבאג שתוקן כאן: המניפסט הסטטי מפנה ל-"./" בלי
     כתובת המספרה, ולכן לחיצה על האייקון שהלקוח התקין פתחה את מסך פתיחת מספרה
     חדשה במקום עמוד ההזמנה שלו. כאן נבנה מניפסט עם start_url הכולל #<shopId>
     ועם שם המספרה, כך שהאייקון נפתח תמיד במקום הנכון. */
  function applyShopManifest() {
    try {
      if (SHOP === "__new__") return;                   // מסך פתיחת מספרה — המניפסט הרגיל
      const link = document.getElementById("ug-manifest");
      if (!link) return;
      const st = Store.get();
      const shopName = (st && st.shop && st.shop.name) || "BarberTor";
      const base = location.origin + location.pathname.replace(/[^/]*$/, "");
      const mf = {
        id: "./#" + SHOP,
        name: shopName,
        short_name: shopName.slice(0, 12),
        description: "הזמנת תורים · " + shopName,
        lang: "he", dir: "rtl",
        start_url: base + "#" + SHOP,
        scope: base,
        display: "standalone",
        orientation: "portrait",
        background_color: "#0a0c10",
        theme_color: "#0a0c10",
        icons: [
          { src: base + "assets/img/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
          { src: base + "assets/img/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
          { src: base + "assets/img/icon-192.png", sizes: "192x192", type: "image/png", purpose: "maskable" },
          { src: base + "assets/img/icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      };
      const blob = new Blob([JSON.stringify(mf)], { type: "application/manifest+json" });
      const url = URL.createObjectURL(blob);
      if (link.dataset.blob) { try { URL.revokeObjectURL(link.dataset.blob); } catch (e) {} }
      link.dataset.blob = url;
      link.href = url;
    } catch (e) { /* נכשל — נשאר המניפסט הסטטי */ }
  }

  // רישום המכשיר לפוש (FCM) — כדי לקבל התראות גם כשהאפליקציה סגורה
  function ensureFcm() {
    if (UG.FCM && Notify.permission() === "granted") {
      UG.FCM.start(identity.userId, view.route === "owner");
    }
  }

  /* =======================================================================
     ניווט "אחורה" חכם (כפתור/החלקה של המערכת) — מחזיר בכל פעם למסך הקודם
     שבו היה המשתמש, צמוד, עד המסך הראשון; שם נפתחת שאלת יציאה.
     ההקלטה אוטומטית מתוך render(): כל מעבר בין "עמודים" (לשוניות / שלבי
     האשף / בחירת שירות / מנהל↔לקוח) נשמר, ובחירות בתוך אותו עמוד (יום/שעה)
     לא מציפות את מחסנית ה"אחורה".
     =======================================================================*/
  const viewStack = [];       // מחסנית העמודים הקודמים (לשחזור בלחיצת "אחורה")
  let navCur = null;          // תמונת המסך הנוכחי
  let navRestoring = false;   // נמנע מהקלטה בזמן שחזור
  function snapView() {
    return {
      route: view.route, clientTab: view.clientTab, ownerTab: view.ownerTab,
      onboarding: view.onboarding, wizStep: view.onboarding ? wiz.step : null,
      selService: view.selService, selDate: view.selDate, selSlot: view.selSlot,
      rescheduleId: view.rescheduleId, oDate: view.oDate, statMonth: view.statMonth,
    };
  }
  // חתימת "עמוד" — רק שינויים שנחשבים ניווט אמיתי יוצרים צעד "אחורה".
  // בחירת שירות/יום/שעה היא בתוך אותו עמוד (חלקן אוטומטית) ולכן לא נספרת כניווט.
  function pageSig(s) {
    return [s.route, s.clientTab, s.ownerTab,
      s.onboarding ? "wiz" + s.wizStep : ""].join("|");
  }
  // נקרא בסוף כל render(): שומר את העמוד הקודם אם עברנו לעמוד חדש
  function syncNav() {
    if (navRestoring) return;
    const snap = snapView();
    if (navCur === null) { navCur = snap; return; }
    if (pageSig(navCur) === pageSig(snap)) { navCur = snap; return; }  // אותו עמוד — רק עדכון תת-מצב
    viewStack.push(navCur);
    if (viewStack.length > 80) viewStack.shift();
    navCur = snap;
  }
  function restoreSnap(s) {
    navRestoring = true;
    view.route = s.route; view.clientTab = s.clientTab; view.ownerTab = s.ownerTab;
    view.onboarding = !!s.onboarding;
    view.selService = s.selService || null;
    view.selDate = s.selDate || null;
    view.selSlot = s.selSlot || null;
    view.rescheduleId = s.rescheduleId || null;
    view.oDate = s.oDate || null;
    view.statMonth = s.statMonth || null;
    if (s.onboarding && s.wizStep != null) wiz.step = s.wizStep;
    navCur = s;
    render();
    navRestoring = false;
  }
  function modalOpen() { const m = $("#modalBack"); return m && m.classList.contains("open"); }

  function onPopState() {
    try { history.pushState(null, ""); } catch (e) {}   // מלכודת מחדש כדי לא לצאת מהאפליקציה
    if (modalOpen()) { closeModal(); return; }           // חלון פתוח → סגירה
    if (viewStack.length) { restoreSnap(viewStack.pop()); return; }   // חזרה לעמוד הקודם
    showExitConfirm();                                   // המסך הראשון → שאלת יציאה
  }
  function setupBackGuard() {
    try { history.pushState(null, ""); } catch (e) {}
    window.addEventListener("popstate", onPopState);
  }
  function showExitConfirm() {
    if (modalOpen()) return;   // כבר פתוח — לא לפתוח שוב
    openModal(`
      <div class="m-title">יציאה מהאפליקציה</div>
      <div class="m-sub">להישאר או לצאת?</div>
      <div style="height:12px"></div>
      <button class="btn btn-primary" data-act="stay">הישארות באפליקציה</button>
      <button class="btn btn-danger" data-act="do-exit" style="margin-top:8px">יציאה</button>
    `);
  }
  function performExit() {
    closeModal();
    window.removeEventListener("popstate", onPopState);
    try { if (window.navigator && navigator.app && navigator.app.exitApp) { navigator.app.exitApp(); return; } } catch (e) {}
    try { history.back(); } catch (e) {}
    setTimeout(() => { try { window.close(); } catch (e) {} }, 80);
  }

  /* =======================================================================
     הוספה ליומן (קובץ .ics עם תזכורת שעה לפני)
     =======================================================================*/
  function icsDate(d) {
    const p = (n) => String(n).padStart(2, "0");
    return d.getUTCFullYear() + p(d.getUTCMonth() + 1) + p(d.getUTCDate()) + "T" +
      p(d.getUTCHours()) + p(d.getUTCMinutes()) + "00Z";
  }
  function addToCalendar(id) {
    const st = Store.get();
    const b = st.bookings.find((x) => x.id === id);
    if (!b) return;
    const start = u.dateTime(b.date, b.start), end = u.dateTime(b.date, b.end);
    const dates = icsDate(start) + "/" + icsDate(end);      // YYYYMMDDTHHMMSSZ/…
    const text = b.serviceName + " — " + st.shop.name;
    const details = "תור ל" + b.serviceName + " · " + u.fmtPrice(b.price);
    const url = "https://calendar.google.com/calendar/render?action=TEMPLATE" +
      "&text=" + encodeURIComponent(text) +
      "&dates=" + dates +
      "&details=" + encodeURIComponent(details) +
      (st.shop.address ? "&location=" + encodeURIComponent(st.shop.address) : "");
    openExternal(url);
    toast("נפתח Google Calendar עם התור 📅", "sky", "📅");
  }

  /* =======================================================================
     שיתוף עם חברים
     =======================================================================*/
  async function shareApp() {
    const st = Store.get();
    const url = clientLink();
    const text = "קביעת תור למספרת " + st.shop.name + " 💈✂️";
    try {
      if (navigator.share) { await navigator.share({ title: st.shop.name, text: text, url: url }); return; }
    } catch (e) { return; }
    try {
      if (navigator.clipboard) { await navigator.clipboard.writeText(text + " " + url); toast("הקישור הועתק — הדביקו בצ׳אט", "good", "🔗"); return; }
    } catch (e) {}
    openExternal("https://wa.me/?text=" + encodeURIComponent(text + " " + url));
  }

  /* ---------- כרטיס ביקורת (משותף למנהל וללקוח) ---------- */
  function reviewCardHtml(r) {
    const rating = Number(r.rating) || 0;
    return `
      <div class="card" style="padding:13px 15px">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px">
          <b style="font-size:14.5px">${esc(r.userName || "לקוח")}</b>
          <span class="rev-stars">${"★".repeat(rating)}<span class="dim">${"★".repeat(5 - rating)}</span></span>
        </div>
        ${r.text ? `<p style="font-size:13.5px;color:var(--muted);margin-top:6px;line-height:1.5">${esc(r.text)}</p>` : ""}
        ${r.serviceName ? `<div class="hint" style="margin-top:6px">${esc(r.serviceName)}</div>` : ""}
      </div>`;
  }

  /* =======================================================================
     טוסט ומודאל
     =======================================================================*/
  function toast(msg, kind, ico) {
    const wrap = $("#toasts");
    const t = document.createElement("div");
    t.className = "toast " + (kind || "");
    t.innerHTML = `<span class="t-ico">${ico || "✅"}</span><span>${esc(msg)}</span>`;
    wrap.appendChild(t);
    setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 320); }, 3200);
  }
  function openModal(html) {
    $("#modal").innerHTML = `<div class="m-handle"></div>` + html;
    $("#modalBack").classList.add("open");
  }
  function closeModal() { $("#modalBack").classList.remove("open"); }

  /* =======================================================================
     ראוטינג
     =======================================================================*/
  function go(route) {
    view.route = route;
    if (route === "owner") localStorage.setItem(AUTHKEY, "1");
    localStorage.setItem(ROUTEKEY, route);
    if (route === "owner" && !ownerSeen) {
      const st = Store.get();
      ownerSeen = new Set(st.bookings.map((b) => b.id)); // בסיס — לא להתריע על קיימים
    }
    if (route === "owner") ensureFcm(); // רישום מכשיר המנהל לקבלת פוש על תור חדש
    render();
  }

  /* אל תבצע רינדור-מלא בזמן שהמשתמש מקליד בשדה בתוך המסך */
  function isEditingRoot() {
    const a = document.activeElement;
    return a && $("#root") && $("#root").contains(a) && /INPUT|SELECT|TEXTAREA/.test(a.tagName);
  }

  /* =======================================================================
     חישוב זמינות תורים
     =======================================================================*/
  // רשת שעות אחידה ליום נתון (מרווחי slotStep, למשל 45 דק׳).
  // מחזיר לכל משבצת: האם תפוסה (תור קיים), האם חסומה ע״י הבעלים, האם עברה.
  // שעות שהבעלים פתח ליום זה מעבר לשעות הפעילות (מהמערך opens)
  function openedFor(st, dateKey) {
    return (st.opens || []).filter((k) => k.indexOf(dateKey + "|") === 0)
      .map((k) => k.split("|")[1]);
  }

  /* "סגירת הרשמה" — כמה דקות לפני התור מפסיקים להציג אותו ללקוחות אם הוא
     עדיין פנוי. 0 / לא מוגדר = כבוי. משמש גם באפליקציה וגם בשכבת הנתונים. */
  function hideFreeCutoffMin(st) {
    return Math.max(0, Number((st && st.shop && st.shop.hideFreeBeforeMin) || 0));
  }

  /* רשת השעות שהלקוח רואה: שעות הפעילות (פחות חסומות) + שעות שנפתחו ידנית.
     dur = משך השירות שנבחר (דקות). כשמעבירים אותו, החפיפה מול תורים קיימים
     ובדיקת שעת הסגירה נעשות לאורך כל משך התור — כך שירות ארוך (למשל 90 דק׳)
     לא יוצג כפנוי במקום שאין בו באמת מקום. בלי dur — התנהגות ברירת המחדל (צעד). */
  function gridSlots(dateKey, dur) {
    const st = Store.get();
    if ((st.closedDates || []).includes(dateKey)) return [];   // יום חופשה/סגירה
    const dow = u.parseKey(dateKey).getDay();
    const sched = st.schedule[dow];
    const active = !!(sched && sched.active);
    const opened = openedFor(st, dateKey);
    if (!active && !opened.length) return [];
    const open = active ? u.toMin(sched.open) : 0, close = active ? u.toMin(sched.close) : 0;
    const step = st.shop.slotStep || 45;
    const span = Math.max(step, Number(dur) || step);   // אורך התור בפועל — לפחות צעד אחד
    const now = new Date();
    const isToday = u.isSameDay(u.parseKey(dateKey), now);
    const nowMin = now.getHours() * 60 + now.getMinutes();
    const blocks = new Set(st.blocks || []);
    const blockMins = [...blocks].filter((k) => k.indexOf(dateKey + "|") === 0).map((k) => u.toMin(k.split("|")[1]));
    const dayBookings = st.bookings.filter((b) => b.status !== "cancelled" && b.date === dateKey);
    // חפיפה מול תורים קיימים לאורך כל משך השירות (span), לא רק צעד אחד
    const bookingAt = (t) => dayBookings.find((b) => {
      const bs = u.toMin(b.start), be = u.toMin(b.end);
      return t < be && (t + span) > bs;
    }) || null;
    const blockedSpan = (t) => blockMins.some((bm) => bm >= t && bm < t + span);   // חסימה כלשהי בתוך המשך
    /* משבצת פנויה שנותרו לה פחות מ-cutoff דקות — מוסתרת מהלקוח. מחשבים לפי
       חותמת זמן מלאה (ולא דקות ביום) כדי שזה יעבוד גם כשהחלון חוצה חצות. */
    const cutoffMs = hideFreeCutoffMin(st) * 60000;
    const nowTs = now.getTime();
    const hiddenAt = (start, booking) => {
      if (!cutoffMs || booking) return false;
      const lead = u.dateTime(dateKey, start).getTime() - nowTs;
      return lead > 0 && lead < cutoffMs;
    };
    // fits = השירות מסתיים עד שעת הסגירה (רלוונטי לשעות פעילות; שעה שנפתחה ידנית פטורה)
    const mk = (start, t, blocked, fits) => {
      const booking = bookingAt(t);
      return { start, booking, blocked: blocked || blockedSpan(t), fits: fits, past: isToday && t <= nowMin, hidden: hiddenAt(start, booking) };
    };
    const byStart = new Map();
    // שעות בתוך הפעילות
    if (active) for (let t = open; t + step <= close; t += step) {
      const start = u.toHHMM(t);
      byStart.set(start, mk(start, t, blocks.has(dateKey + "|" + start), t + span <= close));
    }
    // שעות שנפתחו ידנית מחוץ לפעילות — פטורות מבדיקת שעת הסגירה (הבעלים פתח אותן במפורש)
    opened.forEach((start) => {
      if (byStart.has(start)) return;
      byStart.set(start, mk(start, u.toMin(start), false, true));
    });
    return [...byStart.values()].sort((a, z) => u.toMin(a.start) - u.toMin(z.start));
  }

  /* רשת יום מלאה לתצוגת הבעלים (00:00–24:00) — כדי לפתוח כל שעה ביממה,
     כולל לילה ומוקדם בבוקר. available = זמין ללקוחות; inHours = בתוך שעות הפעילות. */
  function ownerDayGrid(dateKey) {
    const st = Store.get();
    const dow = u.parseKey(dateKey).getDay();
    const sched = st.schedule[dow];
    const active = !!(sched && sched.active);
    const open = active ? u.toMin(sched.open) : 0, close = active ? u.toMin(sched.close) : 0;
    const step = st.shop.slotStep || 45;
    const phase = active ? (open % step) : 0;      // יישור לרשת שעות הפעילות
    const dayStart = 0, dayEnd = 24 * 60;
    const now = new Date();
    const isToday = u.isSameDay(u.parseKey(dateKey), now);
    const nowMin = now.getHours() * 60 + now.getMinutes();
    const blocks = new Set(st.blocks || []);
    const opens = new Set(st.opens || []);
    const dayBookings = st.bookings.filter((b) => b.status !== "cancelled" && b.date === dateKey);
    const cutoffMs = hideFreeCutoffMin(st) * 60000;
    const nowTs = now.getTime();
    let t = dayStart; while (((t % step) + step) % step !== phase) t++;
    const slots = [];
    for (; t + step <= dayEnd; t += step) {
      const start = u.toHHMM(t), end = t + step;
      const inHours = active && t >= open && end <= close;
      const opened = opens.has(dateKey + "|" + start);
      const blocked = blocks.has(dateKey + "|" + start);
      const booking = dayBookings.find((b) => { const bs = u.toMin(b.start), be = u.toMin(b.end); return t < be && end > bs; }) || null;
      const available = (inHours && !blocked) || opened;
      // נסגר להרשמה: פנוי וזמין, אבל קרוב מדי למועד לפי הגדרת "סגירת ההרשמה"
      const lead = u.dateTime(dateKey, start).getTime() - nowTs;
      const signupClosed = !!cutoffMs && !booking && available && lead > 0 && lead < cutoffMs;
      slots.push({ start, booking, inHours, opened, past: isToday && t <= nowMin,
        available, signupClosed });
    }
    return slots;
  }

  function nextDays(n) {
    const arr = [];
    const d = new Date(); d.setHours(0, 0, 0, 0);
    for (let i = 0; i < n; i++) {
      const dd = new Date(d); dd.setDate(d.getDate() + i);
      arr.push(u.dateKey(dd));
    }
    return arr;
  }

  /* =======================================================================
     כותרת עליונה משותפת
     =======================================================================*/
  function currentTheme() { return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark"; }
  function toggleTheme() {
    const t = currentTheme() === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("ug_theme", t); } catch (e) {}
    render();
  }

  function topbar(sub, opts) {
    opts = opts || {};
    const st = Store.get();
    const themeIco = currentTheme() === "light" ? "🌙" : "☀️";
    return `
    <div class="topbar">
      <div class="brand">
        <div class="logo-dot${shopLogo(st) ? " has-img" : ""}" title="">${shopLogo(st)
          ? `<img class="logo-img" src="${esc(shopLogo(st))}" alt="">`
          : esc((st.shop.name || "מ")[0])}</div>
        <div class="titles">
          <h1>${esc(st.shop.name)}</h1>
          <p>${esc(sub)}</p>
        </div>
      </div>
      <div class="spacer"></div>
      <button class="icon-btn" data-act="toggle-theme" title="מצב תצוגה">${themeIco}</button>
    </div>`;
  }

  /* =======================================================================
     צד לקוח
     =======================================================================*/
  /* ---------- זיהוי לקוח (מודל חדש: Google/טלפון) ----------
     דורש שהלקוח עבר במפורש את מסך הזיהוי (gateDone) — לא מספיק שם+טלפון ישנים
     שנשמרו מהזמנה קודמת, כדי שגם לקוחות קיימים יזדהו פעם אחת. */
  function clientIdentified() { return !!(identity && identity.gateDone && identity.name && identity.phone); }

  // החלת פרטי הלקוח מחשבון Google + שחזור טלפון מהזמנה קודמת (אם יש)
  function applyGoogleClientIdentity(user) {
    if (!user) return;
    identity.userId = "g_" + user.uid;
    const dn = (user.displayName || "").trim();
    if (dn) {
      identity.name = dn;
      const parts = dn.split(/\s+/);
      identity.firstName = parts[0] || "";
      identity.lastName = parts.slice(1).join(" ") || "";
    }
    identity.email = user.email || identity.email || "";
    identity.googleAuthed = true;
    // אותו userId בכל מכשיר → נשחזר טלפון מהזמנה קודמת ולא נשאל שוב
    const prev = (Store.get().bookings || []).filter((b) => b.userId === identity.userId && b.phone).pop();
    if (prev && prev.phone) identity.phone = prev.phone;
    if (identity.phone) identity.gateDone = true;   // יש טלפון → הזיהוי הושלם
    saveIdentity();
  }

  async function clientGoogleSignIn() {
    if (!(UG.Auth && authAvail)) { toast("התחברות Google אינה זמינה כרגע", "", "⚠️"); return; }
    try {
      rememberGoogleIntent("client");
      const user = await UG.Auth.signInWithGoogle();
      if (user) { clearGoogleIntent(); applyGoogleClientIdentity(user); render(); }
    } catch (e) { clearGoogleIntent(); toast(UG.Auth.humanError(e), "", "⚠️"); }
  }

  function agSavePhone() {
    const name = (($("#ag-name") && $("#ag-name").value.trim()) || "");
    const phoneRaw = ($("#ag-phone") && $("#ag-phone").value.trim()) || "";
    if (!name) { toast("נא להזין שם מלא", "", "✋"); return; }
    if (!u.isValidPhone(phoneRaw)) { toast("מספר טלפון לא תקין", "", "📵"); return; }
    identity.name = name;
    const parts = name.split(/\s+/);
    identity.firstName = parts[0] || ""; identity.lastName = parts.slice(1).join(" ") || "";
    identity.phone = u.fmtPhone(phoneRaw);
    if (!identity.userId) identity.userId = u.uid();
    identity.gateDone = true;   // הלקוח עבר את מסך הזיהוי במפורש
    saveIdentity();
    try { localStorage.setItem(PRIVACY_KEY, "1"); } catch (e) {}   // הזיהוי דרך המסך = הסכמה
    view.authPhoneForm = false;
    toast("ברוכים הבאים! 🙂", "good", "✓");
    render();
  }

  // מסך זיהוי הלקוח — לפני שמאפשרים להזמין
  function clientAuthGate(st) {
    const shopName = (st.shop && st.shop.name) || "המספרה";
    const phoneStep = (identity.googleAuthed && !identity.phone) || view.authPhoneForm;
    if (phoneStep) {
      // תמיד מציגים גם שם וגם טלפון עם ערך ממולא (אם קיים) — כדי שהלקוח יראה
      // מה שמור עליו ויוכל לתקן. זה גם מונע דריסה בטעות של פרטים קיימים.
      return `
      <div class="screen active">
        ${topbar("כניסה", {})}
        <div class="content"><div class="auth-gate">
          <div class="ag-emoji">📱</div>
          <h2>${identity.name ? "אישור הפרטים" : "כמה פרטים קצרים"}</h2>
          <p>${identity.name ? `היי ${esc(identity.firstName || identity.name)}! ` : ""}אשרו שהפרטים נכונים — הספר יראה אותם כשתקבעו תור.</p>
          <div class="field"><label>שם מלא <span class="req">*</span></label>
            <input class="input" id="ag-name" placeholder="שם מלא" value="${esc(identity.name || "")}"></div>
          <div class="field"><label>טלפון נייד <span class="req">*</span></label>
            <input class="input" id="ag-phone" type="tel" inputmode="tel" placeholder="050-0000000" value="${esc(identity.phone || "")}"></div>
          <button class="btn btn-primary" data-act="ag-save-phone">${identity.googleAuthed ? "סיום" : "המשך"}</button>
          ${view.authPhoneForm && !identity.googleAuthed ? `<button class="btn btn-ghost" data-act="ag-back" style="margin-top:8px">חזרה</button>` : ""}
        </div></div>
      </div>`;
    }
    return `
      <div class="screen active">
        ${topbar("כניסה", {})}
        <div class="content"><div class="auth-gate">
          <div class="ag-logo">${shopLogo(st) ? `<img src="${esc(shopLogo(st))}" alt="">` : esc((shopName || "מ")[0])}</div>
          <h2>${esc(shopName)}</h2>
          <p>היכנסו כדי לקבוע תור ולעקוב אחרי התורים שלכם.</p>
          <button class="btn btn-google" data-act="ag-google"><span class="g-ico">${googleIcoSvg()}</span>המשך עם Google</button>
          <div class="auth-or"><span>או</span></div>
          <button class="btn" data-act="ag-phone-form">המשך עם טלפון</button>
          <p class="hint" style="margin-top:18px">
            <a href="privacy.html" target="_blank" rel="noopener" style="color:var(--muted)">מדיניות פרטיות</a>
            · <a href="terms.html" target="_blank" rel="noopener" style="color:var(--muted)">תנאי שימוש</a>
          </p>
        </div></div>
      </div>`;
  }

  function renderClient() {
    const st = Store.get();
    // מודל חדש: זיהוי חובה לפני הזמנה (לא חל על "תצוגת לקוח" של הבעלים)
    if (newAuthShop() && !view.ownerPreview && !clientIdentified()) {
      return clientAuthGate(st);
    }
    const activeServices = st.services.filter((s) => s.active !== false);
    if (!view.selService || !activeServices.find((s) => s.id === view.selService)) {
      view.selService = activeServices[0] ? activeServices[0].id : null;
    }
    const previewBar = view.ownerPreview ? `
      <div class="preview-bar">
        <span>👁️ תצוגת לקוח — כך הלקוחות רואים את הדף</span>
        <button class="btn btn-sm" data-act="exit-preview">חזרה לניהול ›</button>
      </div>` : "";

    // מבנה מצומצם: בית | התורים שלי, וכפתור "הזמנת תור" בעמוד הבית
    if (condensedClient()) {
      if (!["home", "book", "mine"].includes(view.clientTab)) view.clientTab = "home";
      let cbody;
      if (view.clientTab === "book") cbody = clientBook(st, activeServices);
      else if (view.clientTab === "mine") cbody = clientMine(st);
      else cbody = clientHome(st, activeServices);
      return `
      <div class="screen active">
        ${previewBar}
        ${topbar("קביעת תור", {})}
        <div class="content" id="cscroll">${cbody}</div>
        <div class="tabbar">
          <button data-tab="home" class="${view.clientTab === "home" || view.clientTab === "book" ? "active" : ""}">
            <span class="tb-ico">🏠</span>בית</button>
          <button data-tab="mine" class="${view.clientTab === "mine" ? "active" : ""}">
            <span class="tb-ico">🎟️</span>התורים שלי</button>
        </div>
      </div>`;
    }

    // אילו לשוניות מוצגות — לפי מוצרים קיימים והמתגים שהספר בחר בהגדרות
    const tabGallery = cShow(st, "showGallery");
    const tabProducts = activeProducts(st).length > 0 && cShow(st, "showProducts");
    const tabReviews = cShow(st, "showReviews");
    // אם הלשונית הפעילה כובתה — חזרה לקביעת תור
    if ((view.clientTab === "gallery" && !tabGallery) ||
        (view.clientTab === "products" && !tabProducts) ||
        (view.clientTab === "reviews" && !tabReviews) ||
        view.clientTab === "home") view.clientTab = "book";   // "בית" קיים רק במבנה המצומצם
    let body;
    if (view.clientTab === "gallery") body = clientGallery();
    else if (view.clientTab === "reviews") body = clientReviews();
    else if (view.clientTab === "products") body = clientProducts(st);
    else if (view.clientTab === "mine") body = clientMine(st);
    else body = clientBook(st, activeServices);
    return `
    <div class="screen active">
      ${previewBar}
      ${topbar("קביעת תור", {})}
      <div class="content" id="cscroll">${body}</div>
      <div class="tabbar">
        <button data-tab="book" class="${view.clientTab === "book" ? "active" : ""}">
          <span class="tb-ico">🗓️</span>קביעת תור</button>
        ${tabGallery ? `<button data-tab="gallery" class="${view.clientTab === "gallery" ? "active" : ""}">
          <span class="tb-ico">🖼️</span>גלריה</button>` : ""}
        ${tabProducts ? `<button data-tab="products" class="${view.clientTab === "products" ? "active" : ""}">
          <span class="tb-ico">🛍️</span>מוצרים</button>` : ""}
        ${tabReviews ? `<button data-tab="reviews" class="${view.clientTab === "reviews" ? "active" : ""}">
          <span class="tb-ico">⭐</span>ביקורות</button>` : ""}
        <button data-tab="mine" class="${view.clientTab === "mine" ? "active" : ""}">
          <span class="tb-ico">🎟️</span>התורים שלי</button>
      </div>
    </div>`;
  }

  /* ===== עמוד "בית" מצומצם — כפתור הזמנה בראש + כל התוכן בעמוד אחד ===== */
  function clientHome(st, services) {
    const hasCover = !!shopCover(st);
    // תמונת נושא רחבה בראש הדף, שנמזגת אל רקע העמוד; מעליה כותרת המספרה + כפתור הזמנה
    const hero = `
      <div class="home-hero ${hasCover ? "has-cover" : "no-cover"}">
        ${hasCover ? `<img class="home-hero-img" src="${esc(shopCover(st))}" alt="">` : ""}
        <div class="home-hero-body">
          <div class="home-hero-title">${esc(st.shop.name || "")}</div>
          ${st.shop.tagline ? `<div class="home-hero-sub">${esc(st.shop.tagline)}</div>` : ""}
          <button class="btn home-book-cta" data-tab="book"><span class="hbc-emoji">🗓️</span> להזמנת תור</button>
        </div>
      </div>`;
    return `
      ${hero}
      ${rescheduleBanner(st)}
      ${alertBanner(st)}
      ${notifBanner()}
      ${arrivalBanner(st)}
      ${reviewBanner(st)}
      ${aboutCard(st)}
      ${cShow(st, "showGallery") ? homeGallery() : ""}
      ${cShow(st, "showHours") ? hoursCard(st) : ""}
      ${mapsCard(st)}
      ${installCard()}
      ${cShow(st, "showShare") ? shareCard() : ""}
      ${cShow(st, "showProducts") ? homeProducts(st) : ""}
      ${cShow(st, "showReviews") ? homeReviews(st) : ""}
      <p class="hint" style="text-align:center;margin-top:22px">
        מנהלים מספרה? <a href="#new" data-act="open-signup" style="color:var(--sky)">פתחו מערכת תורים משלכם ›</a>
      </p>
      <p class="hint" style="text-align:center;margin-top:8px">
        <a href="privacy.html" target="_blank" rel="noopener" style="color:var(--muted)">מדיניות פרטיות</a>
        · <a href="terms.html" target="_blank" rel="noopener" style="color:var(--muted)">תנאי שימוש</a>
        · <span data-act="delete-my-data" style="color:var(--muted);text-decoration:underline;cursor:pointer">מחיקת הנתונים שלי</span>
      </p>
    `;
  }

  // מוצרים כמקטע בעמוד הבית — ריק אם אין מוצרים (בלי "אין מוצרים")
  function homeProducts(st) {
    const products = activeProducts(st);
    if (!products.length) return "";
    const waOk = !!waIntl(st.shop.phone || "");
    return `
      <div class="section-title">🛍️ המוצרים שלנו</div>
      ${products.map((p) => `
        <div class="card prod-card">
          ${p.image ? `<div class="prod-card-img" data-act="product-zoom" data-id="${p.id}"><img src="${esc(p.image)}" alt="${esc(p.name)}"><span class="prod-zoom-hint">🔍</span></div>` : ""}
          <div class="prod-card-body">
            <div class="prod-card-top">
              <span class="prod-card-name">${esc(p.name)}</span>
              <span class="prod-card-price">${u.fmtPrice(p.price)}</span>
            </div>
            ${p.description ? `<p class="prod-card-desc">${esc(p.description)}</p>` : ""}
            ${waOk
              ? `<button class="btn btn-wa" data-act="product-interest" data-id="${p.id}">💬 מעניין אותי — פרטים לקנייה</button>`
              : `<p class="hint">לפרטים ולרכישה — פנו אל המספרה.</p>`}
          </div>
        </div>`).join("")}
    `;
  }

  // ביקורות כמקטע בעמוד הבית (בלי כרטיס "קצת עלינו" שכבר מוצג למעלה)
  function homeReviews(st) {
    const reviews = (st.reviews || []).slice().sort((a, z) => (z.createdAt || 0) - (a.createdAt || 0));
    const avg = reviews.length ? reviews.reduce((s, r) => s + Number(r.rating || 0), 0) / reviews.length : 0;
    let html = `
      <div class="section-title">⭐ ביקורות</div>
      <div class="reviews-hero">
        <div class="rh-avg">${reviews.length ? avg.toFixed(1) : "—"}</div>
        ${starsRow(Math.round(avg))}
        <div class="rh-count">${reviews.length} ${reviews.length === 1 ? "ביקורת" : "ביקורות"}</div>
      </div>
      <button class="btn btn-primary" data-act="add-review" style="margin-bottom:16px">＋ כתיבת ביקורת</button>`;
    if (reviews.length) html += reviews.map((r) => reviewCardHtml(r)).join("");
    return html;
  }

  // גלריה כמקטע בעמוד הבית — ריק אם אין תמונות
  function homeGallery() {
    const photos = Store.getGallery();
    if (!photos.length) return "";
    return `
      <div class="section-title">🖼️ גלריית תספורות</div>
      <div class="gallery-grid">
        ${photos.map((p) => `
          <button class="gphoto" data-photo="${p.id}">
            <img src="${esc(p.dataUrl)}" alt="${esc(p.caption || "תספורת")}" loading="lazy">
            ${p.caption ? `<span class="gcap">${esc(p.caption)}</span>` : ""}
          </button>`).join("")}
      </div>`;
  }

  /* ---------- גלריה (תמונות בלבד) ---------- */
  function clientGallery() {
    const photos = Store.getGallery();
    if (!photos.length) return emptyState("🖼️", "הגלריה בקרוב", "בעל העסק עדיין לא העלה תמונות של תספורות");
    return `
      <div class="section-title">גלריית תספורות</div>
      <div class="gallery-grid">
        ${photos.map((p) => `
          <button class="gphoto" data-photo="${p.id}">
            <img src="${esc(p.dataUrl)}" alt="${esc(p.caption || "תספורת")}" loading="lazy">
            ${p.caption ? `<span class="gcap">${esc(p.caption)}</span>` : ""}
          </button>`).join("")}
      </div>`;
  }

  /* ---------- ביקורות (מסך נפרד ללקוח) ---------- */
  function starsRow(n) {
    n = Math.max(0, Math.min(5, n));
    return `<span class="rev-stars big">${"★".repeat(n)}<span class="dim">${"★".repeat(5 - n)}</span></span>`;
  }
  function clientReviews() {
    const st = Store.get();
    const reviews = (st.reviews || []).slice().sort((a, z) => (z.createdAt || 0) - (a.createdAt || 0));
    const avg = reviews.length ? reviews.reduce((s, r) => s + Number(r.rating || 0), 0) / reviews.length : 0;
    let html = aboutCard(st) + `
      <div class="reviews-hero">
        <div class="rh-avg">${reviews.length ? avg.toFixed(1) : "—"}</div>
        ${starsRow(Math.round(avg))}
        <div class="rh-count">${reviews.length} ${reviews.length === 1 ? "ביקורת" : "ביקורות"}</div>
      </div>
      <button class="btn btn-primary" data-act="add-review" style="margin-bottom:16px">＋ כתיבת ביקורת</button>`;
    if (!reviews.length) html += emptyState("⭐", "אין עדיין ביקורות", "היו הראשונים לכתוב ביקורת!");
    else html += reviews.map((r) => reviewCardHtml(r)).join("");
    return html;
  }

  /* ---------- מוצרים למכירה (צד הלקוח) ---------- */
  function activeProducts(st) {
    return (st.products || []).filter((p) => p.active !== false)
      .slice().sort((a, z) => (z.createdAt || 0) - (a.createdAt || 0));
  }
  function clientProducts(st) {
    const products = activeProducts(st);
    if (!products.length) return emptyState("🛍️", "אין מוצרים כרגע", "בעל העסק עדיין לא הוסיף מוצרים");
    const waOk = !!waIntl(st.shop.phone || "");
    return `
      <div class="section-title">המוצרים שלנו</div>
      ${products.map((p) => `
        <div class="card prod-card">
          ${p.image ? `<div class="prod-card-img" data-act="product-zoom" data-id="${p.id}"><img src="${esc(p.image)}" alt="${esc(p.name)}"><span class="prod-zoom-hint">🔍</span></div>` : ""}
          <div class="prod-card-body">
            <div class="prod-card-top">
              <span class="prod-card-name">${esc(p.name)}</span>
              <span class="prod-card-price">${u.fmtPrice(p.price)}</span>
            </div>
            ${p.description ? `<p class="prod-card-desc">${esc(p.description)}</p>` : ""}
            ${waOk
              ? `<button class="btn btn-wa" data-act="product-interest" data-id="${p.id}">💬 מעניין אותי — פרטים לקנייה</button>`
              : `<p class="hint">לפרטים ולרכישה — פנו אל המספרה.</p>`}
          </div>
        </div>`).join("")}
    `;
  }

  /* מודאל כתיבת ביקורת חופשית (ללא צורך בתור) — גלוי לכל הלקוחות */
  function openNewReview() {
    const st = Store.get();
    const svcOptions = ['<option value="">כללי</option>']
      .concat((st.services || []).map((s) => `<option value="${esc(s.name)}">${esc(s.name)}</option>`)).join("");
    const preName = (identity.name || ((identity.firstName || "") + " " + (identity.lastName || "")).trim());
    openModal(`
      <div class="m-title">כתיבת ביקורת ⭐</div>
      <div class="m-sub">הביקורת תופיע לכל הלקוחות</div>
      <div class="field"><label>השם שלך</label>
        <input class="input" id="nrv-name" placeholder="השם שלך" value="${esc(preName)}"></div>
      <div class="field"><label>על איזה שירות? (לא חובה)</label>
        <select class="input" id="nrv-svc">${svcOptions}</select></div>
      <div class="stars" id="nrv-stars">
        ${[1, 2, 3, 4, 5].map((n) => `<button class="star on" data-star="${n}">★</button>`).join("")}
      </div>
      <div class="field" style="margin-top:16px"><label>הביקורת שלך (לא חובה)</label>
        <textarea class="input" id="nrv-text" rows="3" placeholder="ספרו לנו איך היה…"></textarea></div>
      <button class="btn btn-primary" data-act="send-new-review">פרסום הביקורת</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>
    `);
    let rating = 5;
    const wrap = $("#nrv-stars");
    const paint = () => [...wrap.children].forEach((c, i) => c.classList.toggle("on", i < rating));
    wrap.addEventListener("click", (e) => {
      const s = e.target.closest("[data-star]"); if (!s) return;
      rating = Number(s.dataset.star); paint();
    });
    $("#modal").__rating = () => rating;
  }

  /* ---------- תצוגת תמונה מוגדלת עם זום (צביטה / הקשה כפולה / כפתורים) ---------- */
  function openImageZoom(src, caption) {
    if (!src) return;
    openModal(`
      <div class="lightbox">
        <div class="lb-stage" id="lbStage">
          <img src="${src}" alt="${esc(caption || "")}" id="lbImg" draggable="false">
        </div>
        ${caption ? `<div class="lb-cap">${esc(caption)}</div>` : ""}
        <div class="lb-zoom">
          <button class="lb-zbtn" data-zoom="out" aria-label="הקטנה">−</button>
          <button class="lb-zbtn" data-zoom="reset">איפוס</button>
          <button class="lb-zbtn" data-zoom="in" aria-label="הגדלה">＋</button>
        </div>
        <div class="lb-hint">צביטה / הקשה כפולה כדי להגדיל</div>
      </div>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:10px">סגירה</button>
    `);
    setupLightboxZoom();
  }
  function openPhoto(id) {
    const p = Store.getGallery().find((x) => x.id === id);
    if (!p) return;
    openImageZoom(p.dataUrl, p.caption || "");
  }

  function setupLightboxZoom() {
    const stage = document.getElementById("lbStage");
    const img = document.getElementById("lbImg");
    if (!stage || !img) return;
    let scale = 1, tx = 0, ty = 0, startDist = 0, startScale = 1, lastTap = 0;
    const MIN = 1, MAX = 4;
    const pts = new Map();

    const apply = () => { img.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`; };
    function clampPan() {
      const r = stage.getBoundingClientRect();
      const maxX = Math.max(0, (img.clientWidth * scale - r.width) / 2);
      const maxY = Math.max(0, (img.clientHeight * scale - r.height) / 2);
      tx = Math.max(-maxX, Math.min(maxX, tx));
      ty = Math.max(-maxY, Math.min(maxY, ty));
    }
    function setScale(s) {
      scale = Math.max(MIN, Math.min(MAX, s));
      if (scale <= 1.001) { scale = 1; tx = 0; ty = 0; }
      clampPan(); apply();
      stage.classList.toggle("zoomed", scale > 1);
    }

    stage.addEventListener("pointerdown", (e) => {
      try { stage.setPointerCapture(e.pointerId); } catch (er) {}
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pts.size === 2) {
        const a = [...pts.values()];
        startDist = Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y);
        startScale = scale;
      } else if (pts.size === 1) {
        const now = Date.now();
        if (now - lastTap < 300) { setScale(scale > 1 ? 1 : 2.5); lastTap = 0; }
        else lastTap = now;
      }
    });
    stage.addEventListener("pointermove", (e) => {
      if (!pts.has(e.pointerId)) return;
      const prev = pts.get(e.pointerId);
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pts.size === 2 && startDist > 0) {
        const a = [...pts.values()];
        const d = Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y);
        setScale(startScale * (d / startDist));
      } else if (pts.size === 1 && scale > 1) {
        tx += e.clientX - prev.x; ty += e.clientY - prev.y; clampPan(); apply();
      }
    });
    const up = (e) => { pts.delete(e.pointerId); if (pts.size < 2) startDist = 0; };
    stage.addEventListener("pointerup", up);
    stage.addEventListener("pointercancel", up);

    document.querySelectorAll("[data-zoom]").forEach((b) => b.addEventListener("click", () => {
      const k = b.dataset.zoom;
      if (k === "in") setScale(scale + 0.6);
      else if (k === "out") setScale(scale - 0.6);
      else setScale(1);
    }));
  }

  function notifBanner() {
    if (!Notify.supported()) return "";
    if (Notify.permission() === "granted") return "";
    const blocked = Notify.permission() === "denied";
    return `
    <div class="banner sky">
      <span class="bn-ico">🔔</span>
      <div class="bn-body">
        <div class="bn-title">${blocked ? "ההתראות חסומות" : "אל תפספסו את התור"}</div>
        <div class="bn-sub">${blocked
          ? "כדי לקבל תזכורות — אפשרו התראות בהגדרות הדפדפן"
          : "תזכורת לפני התור, עדכון על ביטול, והודעה כשמתפנה תור מוקדם"}</div>
      </div>
      <button class="btn btn-primary btn-sm" data-act="${blocked ? "notif-help" : "enable-notif"}" style="width:auto">${blocked ? "איך?" : "אפשר"}</button>
    </div>`;
  }

  /* ---------- הזמנה לאישור התראות — בכל כניסה, עד שהלקוח מאשר ---------- */
  let notifPromptShown = false;   // פעם אחת לכל פתיחה של האפליקציה

  function notifBenefits() {
    return `
      <ul class="nb-list">
        <li><span>⏰</span><div><b>תזכורת לפני התור</b>לא תשכחו ולא תפספסו את התספורת</div></li>
        <li><span>🎉</span><div><b>התפנה תור מוקדם?</b>נעדכן אתכם ראשונים כשמתפנה מקום</div></li>
        <li><span>🔄</span><div><b>שינוי או ביטול</b>תדעו מיד אם משהו בתור שלכם משתנה</div></li>
        <li><span>💈</span><div><b>מבצעים והטבות</b>עדכונים מהמספרה — רק כשבאמת יש משהו</div></li>
      </ul>`;
  }

  // מוצג בכניסה ללקוח כשההתראות עדיין לא אושרו
  function promptNotif(force) {
    if (!Notify.supported()) return;
    if (Notify.permission() === "granted") return;
    if (!force && notifPromptShown) return;
    if (!force && view.route !== "client") return;
    if ($("#modalBack") && $("#modalBack").classList.contains("open")) return;   // אל תדרוס מודאל פתוח
    notifPromptShown = true;
    if (Notify.permission() === "denied") { notifHelp(); return; }
    openModal(`
      <div class="m-title">🔔 שלא תפספסו את התור</div>
      <div class="m-sub">אישור התראות לוקח שנייה — וזה מה שתקבלו:</div>
      ${notifBenefits()}
      <button class="btn btn-primary" data-act="enable-notif" style="margin-top:6px">אישור התראות</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">אולי אחר כך</button>
    `);
  }

  // ההתראות נחסמו בדפדפן — אי אפשר לבקש שוב, אז מסבירים איך לפתוח ידנית
  function notifHelp() {
    const ios = /iPad|iPhone|iPod/.test(navigator.userAgent);
    openModal(`
      <div class="m-title">🔕 ההתראות חסומות</div>
      <div class="m-sub">חסמתם התראות בעבר, ולכן הדפדפן לא ישאל שוב. כך פותחים:</div>
      ${notifBenefits()}
      <div class="pw-card" style="margin-top:4px">
        <div class="pw-card-t">${ios ? "באייפון" : "בדפדפן"}</div>
        <div class="pw-card-b">${ios
          ? "הגדרות → Safari → אתרים → התראות → אפשרו לאתר הזה. אם הוספתם לבית — הגדרות → התראות → BarberTor."
          : "לחצו על סמל המנעול 🔒 בשורת הכתובת → התראות (Notifications) → אפשר (Allow), ואז רעננו את העמוד."}</div>
      </div>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:12px">סגירה</button>
    `);
  }

  /* באנר למנהל — כל ספר יתבקש להפעיל התראות כדי לקבל הודעה על כל תור חדש */
  function ownerNotifBanner() {
    if (!Notify.supported()) return "";
    if (Notify.permission() === "granted") return "";
    return `
    <div class="banner sky">
      <span class="bn-ico">🔔</span>
      <div class="bn-body">
        <div class="bn-title">הפעילו התראות על תורים חדשים</div>
        <div class="bn-sub">קבלו הודעה לטלפון בכל פעם שלקוח קובע תור — גם כשהאפליקציה סגורה</div>
      </div>
      <button class="btn btn-primary btn-sm" data-act="enable-notif" style="width:auto">הפעל</button>
    </div>`;
  }

  function spamBanner() {
    const st = Store.get();
    const now = Date.now();
    if (spamDismissed && now - spamDismissed < 3600000) return "";
    const spamBookings = st.bookings.filter(function (b) {
      return b.spam && b.status !== "cancelled" && u.dateTime(b.date, b.start).getTime() > now &&
        b.createdAt > spamDismissed;
    });
    if (!spamBookings.length) return "";
    var users = {};
    spamBookings.forEach(function (b) {
      var key = bkPhone(b) || b.userId || bkName(b);
      if (!users[key]) users[key] = { name: bkName(b), count: 0 };
      users[key].count++;
    });
    var names = Object.keys(users).map(function (k) { return users[k]; });
    var lines = names.map(function (n) {
      return esc(n.name || "לקוח") + " — " + n.count + " תורים חשודים";
    }).join("<br>");
    return `
    <div class="banner warn spam-banner">
      <span class="bn-ico">🛡️</span>
      <div class="bn-body">
        <div class="bn-title">זוהתה פעילות חריגה</div>
        <div class="bn-sub">${lines}<br>כדאי לבדוק שזה לא ספאם. אפשר לחסום לקוח מלשונית ״תורים״.</div>
      </div>
      <button class="btn btn-sm" data-act="dismiss-spam" style="width:auto">הבנתי</button>
    </div>`;
  }

  /* ---------- מנוי (מודל עסקי) ---------- */
  // מצב המנוי של המספרה הפעילה: off / grandfathered / trial / active / expired
  function subStatus() {
    const cfg = UG_CONFIG.subscription || {};
    if (!cfg.enabled) return { state: "off" };
    const st = Store.get();
    if (!st || !st.shop) return { state: "off" };
    const now = Date.now();
    const sub = (Store.getSub && Store.getSub()) || null;
    const paidUntil = (sub && Number(sub.paidUntil)) || 0;
    if (paidUntil > now) return { state: "active", until: paidUntil, daysLeft: Math.ceil((paidUntil - now) / 86400000) };
    // מספרה ותיקה שסומנה בשרת כפטורה (grandfathered) — לא ננעלת לעולם.
    if (sub && sub.grandfathered) return { state: "grandfathered" };
    /* תחילת הניסיון נלקחת מהשרת (subs/<id>/trialStart) — צומת שהבעלים אינו יכול
       לכתוב אליו (רק המנהל/הקרון), ולכן אי אפשר "למתוח" את הניסיון ע״י עריכת
       createdAt של המספרה. עד שהקרון חותם את הערך (בדקות הראשונות של מספרה חדשה)
       נופלים-לאחור ל-createdAt — הקרון מקבע אותו מיד וממילא חוסם עתידֿ-תיארוך. */
    const serverStart = (sub && Number(sub.trialStart)) || 0;
    const created = serverStart || Number(st.shop.createdAt) || 0;
    if (!created) return { state: "grandfathered" };   // מספרה ותיקה מאוד — עד שהקרון יסמן
    const trialEnd = created + (Number(cfg.trialDays) || 30) * 86400000;
    if (now < trialEnd) return { state: "trial", until: trialEnd, daysLeft: Math.ceil((trialEnd - now) / 86400000) };
    return { state: "expired" };
  }

  function subPlans() {
    const cfg = UG_CONFIG.subscription || {};
    return Array.isArray(cfg.plans) ? cfg.plans : [];
  }
  // תיאור קצר של המסלול הזול ביותר לחודש — לשימוש בבאנרים
  function subPriceText() {
    const plans = subPlans();
    if (!plans.length) return "";
    const p = plans[0];
    return p.price + " ₪ " + (p.per || "");
  }
  /* קישור וואטסאפ להזמנת מסלול מסוים — ההודעה כבר מוכנה, כולל שם המסלול,
     המחיר וזיהוי המספרה, כדי שנדע בדיוק מי פונה ומה הוא רוצה. */
  function planWaHref(p) {
    const cfg = UG_CONFIG.subscription || {};
    if (!cfg.waPhone) return "";
    const st = Store.get();
    const name = (st && st.shop && st.shop.name) || "";
    const msg = "שלום! אני רוצה להפעיל מנוי ל-BarberTor.\n" +
      "המסלול: " + (p.name || "") + " — " + p.price + " ₪ " + (p.per || "") + "\n" +
      "המספרה: " + name + " (" + SHOP + ")";
    return "https://wa.me/" + cfg.waPhone + "?text=" + encodeURIComponent(msg);
  }

  // כרטיסי בחירת מסלול — payUrl מוביל לסליקה; אחרת וואטסאפ עם הודעה מוכנה למסלול
  function planCards() {
    const plans = subPlans();
    if (!plans.length) return "";
    return `<div class="pw-plans">` + plans.map((p) => {
      const wa = planWaHref(p);
      const cta = p.payUrl
        ? `<a class="btn btn-primary btn-sm" href="${esc(p.payUrl)}" target="_blank" rel="noopener">בחירה ותשלום</a>`
        : wa
        ? `<a class="btn btn-primary btn-sm" href="${esc(wa)}" target="_blank" rel="noopener" data-act="plan-pick" data-plan="${esc(p.id || "")}">בחירה</a>`
        : `<button class="btn btn-primary btn-sm" data-act="show-upgrade">בחירה</button>`;
      return `
      <div class="pw-plan${p.badge ? " best" : ""}">
        ${p.badge ? `<span class="pw-plan-badge">${esc(p.badge)}</span>` : ""}
        <div class="pw-plan-name">${esc(p.name || "")}</div>
        <div class="pw-plan-price">${esc(String(p.price))} <span>₪</span></div>
        <div class="pw-plan-per">${esc(p.per || "")}${p.note ? ` · ${esc(p.note)}` : ""}</div>
        ${cta}
      </div>`;
    }).join("") + `</div>`;
  }

  // כפתור פנייה בוואטסאפ להפעלת מנוי — כולל שם/כתובת המספרה כדי שנדע מי פונה
  function subWaButton(label) {
    const cfg = UG_CONFIG.subscription || {};
    if (!cfg.waPhone) return "";
    const st = Store.get();
    const name = (st && st.shop && st.shop.name) || "";
    const msg = `שלום! אני רוצה להפעיל מנוי ל-BarberTor.\nהמספרה: ${name} (${SHOP})`;
    const href = "https://wa.me/" + cfg.waPhone + "?text=" + encodeURIComponent(msg);
    return `<a class="btn btn-wa" href="${esc(href)}" target="_blank" rel="noopener" style="margin-top:10px;text-decoration:none">💬 ${esc(label || "פנייה בוואטסאפ")}</a>`;
  }

  /* טיימר חי (יורד כל שנייה) — מוכן כבר בעת הרינדור כדי שלא יהבהב "…". */
  function countdownHtml(until) {
    const left = Math.max(0, Number(until) - Date.now());
    const total = Math.floor(left / 1000);
    const days = Math.floor(total / 86400);
    const hms = [Math.floor((total % 86400) / 3600), Math.floor((total % 3600) / 60), total % 60]
      .map((n) => String(n).padStart(2, "0")).join(":");
    const dTxt = days > 0 ? days + (days === 1 ? " יום ו-" : " ימים ו-") : "";
    return `<span class="cd" data-countdown="${until}"><span class="cd-d">${dTxt}</span><bdi class="cd-t" dir="ltr">${hms}</bdi></span>`;
  }
  let trialTicker = null;
  function tickTrial() {
    const els = document.querySelectorAll("[data-countdown]");
    if (!els.length) { stopTrialTicker(); return; }   // אין טיימר על המסך — לחסוך CPU
    const now = Date.now();
    let expired = false;
    els.forEach((el) => {
      const until = Number(el.dataset.countdown) || 0;
      const left = until - now;
      if (left <= 0) { expired = true; return; }
      const total = Math.floor(left / 1000);
      const days = Math.floor(total / 86400);
      const hms = [Math.floor((total % 86400) / 3600), Math.floor((total % 3600) / 60), total % 60]
        .map((n) => String(n).padStart(2, "0")).join(":");
      const dEl = el.querySelector(".cd-d"), tEl = el.querySelector(".cd-t");
      if (dEl) dEl.textContent = days > 0 ? days + (days === 1 ? " יום ו-" : " ימים ו-") : "";
      if (tEl) tEl.textContent = hms;
    });
    if (expired) render();   // הגיע לאפס — הרינדור מחדש ינעל למסך התשלום
  }
  function startTrialTicker() {
    if (trialTicker) return;
    if (view.route !== "owner") return;                                 // ללקוחות אין טיימר
    if (!document.querySelector("[data-countdown]")) return;             // אין טיימר במסך → לא צריך
    trialTicker = setInterval(tickTrial, 1000);
  }
  function stopTrialTicker() {
    if (!trialTicker) return;
    clearInterval(trialTicker); trialTicker = null;
  }

  // באנר עדין במסך הניהול — ספירת ימי ניסיון / התראה על מנוי שמסתיים
  function subBanner() {
    const s = subStatus();
    if (s.state === "trial") {
      const soon = s.daysLeft <= 7;
      return `
      <div class="banner ${soon ? "warn" : "sky"}">
        <span class="bn-ico">${soon ? "⏳" : "🎁"}</span>
        <div class="bn-body">
          <div class="bn-title">תקופת ניסיון חינם — נותרו ${countdownHtml(s.until)}</div>
          <div class="bn-sub">בסיום הניסיון יידרש מנוי (${esc(subPriceText())}) כדי להמשיך לנהל</div>
        </div>
        <button class="btn btn-primary btn-sm" data-act="show-upgrade" style="width:auto">פרטים</button>
      </div>`;
    }
    if (s.state === "active" && s.daysLeft <= 5) {
      return `
      <div class="banner warn">
        <span class="bn-ico">⏳</span>
        <div class="bn-body">
          <div class="bn-title">המנוי מסתיים בעוד ${s.daysLeft} ימים</div>
          <div class="bn-sub">חדשו את המנוי כדי להמשיך ללא הפרעה</div>
        </div>
        <button class="btn btn-primary btn-sm" data-act="show-upgrade" style="width:auto">חידוש</button>
      </div>`;
    }
    return "";
  }

  /* חזרה מעמוד התשלום של ספק הסליקה (‎?paid=1‎) — מסמנים "ממתין לאישור" */
  function checkPaymentReturn() {
    let sp = null;
    try { sp = new URLSearchParams(location.search); } catch (e) { return; }
    if (!sp || sp.get("paid") !== "1") return;
    const plan = sp.get("plan") || "";
    try { history.replaceState(null, "", location.pathname + location.hash); } catch (e) {}
    if (Store.markPaymentPending) Store.markPaymentPending(plan);
    toast("התשלום התקבל — המנוי יופעל בקרוב ✓", "good", "💳");
  }

  // מסך "המנוי הסתיים" — מחליף את הניהול כשהניסיון נגמר ולא שולם
  function paywallBody() {
    const cfg = UG_CONFIG.subscription || {};
    const st = Store.get();
    const name = (st && st.shop && st.shop.name) || "";
    const sub = (Store.getSub && Store.getSub()) || null;
    // שילם וממתין לאישור — לא מציגים שוב מסלולים
    if (sub && sub.pending) {
      return `
      <div class="paywall">
        <div class="pw-ico">⏳</div>
        <h2>התשלום התקבל</h2>
        <p>תודה! המנוי של ${esc(name)} יופעל תוך זמן קצר, ואז הניהול ייפתח אוטומטית.</p>
        <div class="pw-card" style="margin-top:18px">
          <div class="pw-card-t">לא נפתח תוך כמה דקות?</div>
          <div class="pw-card-b">${esc(cfg.payInfo || "")}</div>
          ${subWaButton("בדיקת סטטוס בוואטסאפ")}
        </div>
      </div>`;
    }
    return `
      <div class="paywall">
        <div class="pw-ico">🔒</div>
        <h2>תקופת הניסיון הסתיימה</h2>
        <p>${esc(name)} — כדי להמשיך לנהל תורים, לקוחות והגדרות יש לבחור מסלול.</p>
        ${planCards()}
        <div class="pw-card">
          <div class="pw-card-t">להפעלת המנוי</div>
          <div class="pw-card-b">${esc(cfg.payInfo || "")}</div>
          ${subWaButton("פנייה בוואטסאפ")}
        </div>
        <p class="hint">הלקוחות שלך עדיין יכולים לקבוע תור בינתיים. ההפעלה מיידית לאחר התשלום.</p>
      </div>`;
  }

  function handleUpgrade() {
    const cfg = UG_CONFIG.subscription || {};
    openModal(`
      <div class="m-title">💳 בחירת מסלול</div>
      <div class="m-sub">ביטול בכל עת · ללא התחייבות</div>
      ${planCards()}
      <div class="pw-card" style="margin-top:4px">
        <div class="pw-card-b">${esc(cfg.payInfo || "")}</div>
        ${subWaButton("פנייה בוואטסאפ")}
      </div>
      <p class="hint" style="margin-top:12px">לאחר התשלום המספרה שלך תופעל והגישה לניהול תחזור — בדרך כלל תוך זמן קצר.</p>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:12px">סגירה</button>
    `);
  }

  /* ---------- פאנל-על: ניהול מנויים (אתה בלבד) ---------- */
  // השוואת קוד-על: מול hash מוצפן (עדיף) או מול טקסט גלוי (תאימות לאחור)
  async function sha256Hex(s) {
    try {
      const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
      return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
    } catch (e) { return ""; }
  }
  /* hash מלוחלח של סיסמת הניהול — מחליף את השמירה הגלויה. הלכלוח (הכתובת) מונע
     טבלאות-מוכנות (rainbow) וקישור בין מספרות. אותו קלט → אותו hash, כך שאפשר
     להשוות בהתחברות בלי לשמור את הסיסמה עצמה. */
  async function ownerHash(handle, pass) {
    return sha256Hex("btpass1|" + String(handle || "") + "|" + String(pass == null ? "" : pass));
  }
  async function adminCodeMatches(v, sc) {
    if (!v) return false;
    if (sc.adminPasscodeHash) {
      const h = await sha256Hex(v);
      return !!h && h === String(sc.adminPasscodeHash).toLowerCase();
    }
    return !!sc.adminPasscode && v === String(sc.adminPasscode);
  }

  /* קודי הכניסה של המספרה הראשית (config) — נשמרים כ-hash. משווים ע״י הצפנת הקלט.
     תאימות לאחור: אם עדיין מוגדרים קודים בטקסט גלוי (config ישן), גם הם עובדים. */
  async function ownerConfigCodeMatches(v) {
    if (!v) return false;
    const plain = [UG_CONFIG.ownerPasscode].concat(UG_CONFIG.ownerPasscodesExtra || []).filter(Boolean).map(String);
    if (plain.includes(v)) return true;
    const hashes = (UG_CONFIG.ownerPasscodeHashes || []).map((h) => String(h).toLowerCase());
    if (!hashes.length) return false;
    const h = await sha256Hex(v);
    return !!h && hashes.includes(h);
  }

  // המייל שרשאי להפעיל מנויים (מוגדר ב-config, נאכף בחוקי האבטחה)
  function adminEmail() { return String(((UG_CONFIG.subscription || {}).adminEmail) || "").toLowerCase(); }
  // האם מחוברים כרגע עם חשבון האדמין (כדי שהפעלת מנוי תעבור את חוקי האבטחה)
  function isAdminAuthed() {
    const em = (UG.Auth && UG.Auth.currentEmail && UG.Auth.currentEmail()) || "";
    return !!adminEmail() && em.toLowerCase() === adminEmail();
  }

  let adminShops = [];
  async function openAdminPanel() {
    openModal(`
      <div class="m-title">🛠️ ניהול מנויים</div>
      <div class="m-sub">הפעל או חדש מנוי למספרות ששילמו</div>
      <div id="adm-auth" style="margin-top:10px"></div>
      <div id="adm-list" style="max-height:56vh;overflow-y:auto;margin-top:12px">טוען…</div>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:12px">סגירה</button>
    `);
    renderAdminAuth();
    try {
      adminShops = await Store.adminListShops();
      renderAdminList();
    } catch (e) {
      const el = $("#adm-list"); if (el) el.textContent = "שגיאה בטעינת המספרות";
    }
  }

  // שורת מצב ההתחברות של האדמין בראש הפאנל
  function renderAdminAuth() {
    const el = $("#adm-auth"); if (!el) return;
    if (!adminEmail() || !authAvail) { el.innerHTML = ""; return; }
    if (isAdminAuthed()) {
      el.innerHTML = `<div class="conn-line"><span class="conn-dot"></span> מחובר כאדמין: ${esc(UG.Auth.currentEmail())}</div>`;
    } else {
      el.innerHTML = `
        <div class="info-note" style="margin:0 0 10px">
          <b>🔒 נדרשת התחברות</b>
          <p>כדי להפעיל מנויים התחברו עם חשבון האדמין (${esc(adminEmail())}). ההפעלה נכתבת למסד רק מחשבון זה.</p>
        </div>
        <button class="btn btn-google" data-act="adm-google">
          <span class="g-ico">${googleIcoSvg()}</span>התחברות עם Google</button>`;
    }
  }

  async function adminGoogleSignIn() {
    try {
      const user = await UG.Auth.signInWithGoogle();
      if (user) {
        if (isAdminAuthed()) { renderAdminAuth(); toast("מחובר כאדמין ✓", "good", "🔒"); }
        else { toast("החשבון הזה אינו חשבון האדמין", "", "🔒"); await UG.Auth.signOut(); renderAdminAuth(); }
      }
      // אם בוצעה הפניה (redirect) — נחזור לפאנל אחרי טעינה מחדש
    } catch (e) { toast(UG.Auth.humanError ? UG.Auth.humanError(e) : "ההתחברות נכשלה", "", "🔒"); }
  }

  function admShopStatus(s) {
    const cfg = UG_CONFIG.subscription || {};
    const now = Date.now();
    if (s.pending) return { label: "💰 שילם — ממתין להפעלה", cls: "pend" };
    if (s.paidUntil && s.paidUntil > now) {
      return { label: "מנוי פעיל · עד " + u.longDate(u.dateKey(new Date(s.paidUntil))), cls: "ok" };
    }
    if (!s.createdAt) return { label: "מספרה ותיקה (ללא הגבלה)", cls: "ok" };
    const trialEnd = s.createdAt + (Number(cfg.trialDays) || 30) * 86400000;
    if (now < trialEnd) return { label: "ניסיון · " + Math.ceil((trialEnd - now) / 86400000) + " ימים נותרו", cls: "trial" };
    return { label: "הסתיים — ממתין לתשלום", cls: "exp" };
  }

  /* סטטיסטיקה כוללת — מחושבת מהאינדקס שכבר נטען, בלי קריאות נוספות */
  function adminStatsHtml() {
    const cfg = UG_CONFIG.subscription || {};
    const now = Date.now();
    const trialMs = (Number(cfg.trialDays) || 30) * 86400000;
    let active = 0, trial = 0, expired = 0, pending = 0, newMonth = 0, newWeek = 0, revenue = 0;
    adminShops.forEach((s) => {
      if (s.pending) pending++;
      if (s.paidUntil && s.paidUntil > now) { active++; revenue += 35; }
      else if (!s.createdAt) active++;                      // מספרה ותיקה ללא הגבלה
      else if (now < s.createdAt + trialMs) trial++;
      else expired++;
      if (s.createdAt && now - s.createdAt < 30 * 86400000) newMonth++;
      if (s.createdAt && now - s.createdAt < 7 * 86400000) newWeek++;
    });
    const total = adminShops.length;
    const conv = (active + expired) ? Math.round(active / (active + expired) * 100) : 0;
    const cell = (num, lbl, cls) =>
      `<div class="adm-stat ${cls || ""}"><div class="as-num">${num}</div><div class="as-lbl">${lbl}</div></div>`;
    return `
      <div class="adm-stats">
        ${cell(total, "סה״כ מספרות")}
        ${cell(active, "מנוי פעיל", "ok")}
        ${cell(trial, "בניסיון", "trial")}
        ${cell(expired, "פג תוקף", "exp")}
        ${cell(pending, "ממתין לאישור", "pend")}
        ${cell(newWeek, "נרשמו השבוע")}
        ${cell(newMonth, "נרשמו החודש")}
        ${cell(conv + "%", "המרה לתשלום")}
        ${cell("₪" + revenue, "הכנסה חודשית*", "ok")}
      </div>
      <p class="hint" style="margin:-6px 0 12px;font-size:11px">* הערכה גסה לפי ₪35 למנוי פעיל — לא מבחין בין חודשי לשנתי.</p>`;
  }

  function renderAdminList() {
    const el = $("#adm-list"); if (!el) return;
    if (!adminShops.length) { el.innerHTML = `<p class="hint">אין עדיין מספרות</p>`; return; }
    el.innerHTML = adminStatsHtml() + adminShops.map((s) => {
      const st = admShopStatus(s);
      return `
      <div class="adm-shop${s.pending ? " pend" : ""}">
        <div class="adm-name">${esc(s.name)} <span style="color:var(--muted);font-weight:400">· ${esc(s.id)}</span></div>
        <div class="adm-meta">${s.phone ? esc(s.phone) + " · " : ""}${s.paidUntil ? "שולם עד " + esc(u.longDate(u.dateKey(new Date(s.paidUntil)))) : "טרם שולם"}</div>
        <span class="adm-badge ${st.cls}">${esc(st.label)}</span>
        <div class="adm-btns">
          <button class="btn btn-sm" data-act="adm-extend" data-sid="${esc(s.id)}" data-m="1">+ חודש</button>
          <button class="btn btn-sm" data-act="adm-extend" data-sid="${esc(s.id)}" data-m="12">+ שנה</button>
          <button class="btn btn-sm btn-danger" data-act="adm-extend" data-sid="${esc(s.id)}" data-m="0">איפוס</button>
        </div>
      </div>`;
    }).join("");
  }

  async function admExtend(sid, months) {
    const s = adminShops.find((x) => x.id === sid);
    if (!s) return;
    // הפעלת מנוי נכתבת למסד רק מחשבון האדמין — אם לא מחוברים, מבקשים התחברות.
    if (adminEmail() && authAvail && !isAdminAuthed()) {
      toast("התחברו עם חשבון האדמין כדי להפעיל מנוי", "", "🔒");
      renderAdminAuth();
      return;
    }
    const now = Date.now();
    let until = 0;
    if (months > 0) {
      const base = (s.paidUntil && s.paidUntil > now) ? s.paidUntil : now;
      const d = new Date(base);
      d.setMonth(d.getMonth() + months);
      until = d.getTime();
    }
    let ok = false;
    try { ok = await Store.adminSetPaid(sid, until); }
    catch (e) {
      const denied = /permission[_ ]denied/i.test(String((e && (e.code || e.message)) || e));
      toast(denied ? "אין הרשאה — התחברו עם חשבון האדמין" : "השמירה נכשלה", "", "🔒");
      renderAdminAuth();
      return;
    }
    if (!ok) { toast("הפעולה זמינה רק במצב ענן", "", "⚠️"); return; }
    s.paidUntil = until;   // עדכון מקומי לתצוגה מיידית
    s.pending = null;
    renderAdminList();
    toast(months === 0 ? "המנוי אופס" : (months === 12 ? "הופעל לשנה ✓" : (months === 1 ? "הופעל לחודש ✓" : `הופעל ל-${months} חודשים ✓`)), "good", "💳");
  }

  function arrivalBanner(st) {
    const now = Date.now();
    const upcoming = st.bookings
      .filter((b) => b.userId === identity.userId && b.status === "booked")
      .map((b) => ({ b, ts: u.dateTime(b.date, b.start).getTime() }))
      .filter((x) => x.ts > now && x.ts < now + 48 * 3600 * 1000)
      .sort((a, z) => a.ts - z.ts)[0];
    if (!upcoming) return "";
    const b = upcoming.b;
    return `
    <div class="banner good">
      <span class="bn-ico">📍</span>
      <div class="bn-body">
        <div class="bn-title">יש לך תור ${esc(u.relativeDay(b.date))} בשעה ${esc(b.start)}</div>
        <div class="bn-sub">${esc(b.serviceName)} · אשרו הגעה כדי לשמור את התור</div>
      </div>
      <button class="btn btn-primary btn-sm" data-act="confirm-arrival" data-id="${b.id}" style="width:auto">אשר הגעה</button>
    </div>`;
  }

  function clientBook(st, services) {
    if (!services.length) {
      return notifBanner() + emptyState("💈", "אין עדיין שירותים", "בעל העסק טרם הגדיר שירותים לקביעה");
    }
    // בחירת שירות אוטומטית — פחות הקשות (חשוב במיוחד כשיש שירות אחד)
    if (!view.selService || !services.some((s) => s.id === view.selService)) {
      view.selService = services[0].id;
    }
    // בורר שירות
    const svcCards = services.map((s) => `
      <button class="svc-card ${view.selService === s.id ? "selected" : ""}" data-svc="${s.id}">
        <div class="svc-ico">${esc(s.icon != null ? s.icon : "✂️")}</div>
        <div class="svc-body">
          <div class="svc-name">${esc(s.name)}</div>
          <div class="svc-sub">${u.fmtDuration(s.durationMin)}</div>
        </div>
        <div class="svc-price">${u.fmtPrice(s.price)}</div>
      </button>`).join("");

    const service = services.find((s) => s.id === view.selService);

    // בורר ימים (14 יום)
    const closed = new Set(st.closedDates || []);
    // יום פתוח = יש בו שעות פנויות (רגילות או שנפתחו ידנית) ואינו יום חופשה
    const hasHours = (k) => st.schedule[u.parseKey(k).getDay()].active || openedFor(st, k).length > 0;
    const isOpen = (k) => hasHours(k) && !closed.has(k);
    const days = nextDays(14);
    if (!view.selDate || !days.includes(view.selDate)) {
      view.selDate = days.find(isOpen) || days[0];
    }
    const dayChips = days.map((k) => {
      const d = u.parseKey(k);
      const vac = closed.has(k);
      const off = !hasHours(k) || vac;
      return `
      <button class="day-chip ${view.selDate === k ? "selected" : ""} ${off ? "off" : ""}"
              data-day="${k}" ${off ? "disabled" : ""}>
        <div class="dc-dow">${vac ? "חופשה" : off ? "סגור" : u.DOW_SHORT[d.getDay()]}</div>
        <div class="dc-num">${d.getDate()}</div>
        <div class="dc-mon">${u.MON[d.getMonth()]}</div>
      </button>`;
    }).join("");

    // שעות — רשת אחידה לפי משך השירות שנבחר; מסתירים שעברו/חסומות/שלא מתאימות
    // למשך התור (חופפות לתור אחר או חורגות משעת הסגירה), מסמנים תפוסות.
    let slotsHtml;
    const dur = (service && service.durationMin) || (st.shop.slotStep || 45);
    const allSlots = gridSlots(view.selDate, dur).filter((s) => !s.past && !s.blocked && !s.hidden && (s.booking || s.fits));
    // אם המשבצת שנבחרה כבר לא מתאימה לשירות הנוכחי (החלפת שירות לארוך יותר) — לנקות
    if (view.selSlot && !allSlots.some((s) => s.start === view.selSlot && !s.booking)) view.selSlot = null;
    const hasFree = allSlots.some((s) => !s.booking);
    if (closed.has(view.selDate)) {
      slotsHtml = emptyState("🌴", "המספרה בחופשה ביום זה", "בחרו יום אחר מהיומן");
    } else if (!hasHours(view.selDate)) {
      slotsHtml = emptyState("🚫", "סגור ביום זה", "בחרו יום אחר מהיומן");
    } else if (!allSlots.length || !hasFree) {
      slotsHtml = emptyState("⌛", "אין תורים פנויים", "כל התורים ליום זה תפוסים או שהיום הסתיים");
    } else {
      slotsHtml = `<div class="slots-grid">` + allSlots.map((s) => {
        if (s.booking) {
          const inList = (st.waitlist || []).some((w) =>
            w.userId === identity.userId && w.date === view.selDate && w.start === s.start);
          return `<button class="slot taken ${inList ? "inlist" : ""}" data-wait="${view.selDate}|${s.start}">${s.start}<span class="slot-tag">${inList ? "ברשימה ✓" : "תפוס · המתנה"}</span></button>`;
        }
        return `<button class="slot ${view.selSlot === s.start ? "selected" : ""}" data-slot="${s.start}">${s.start}</button>`;
      }).join("") + `</div>`;
    }

    const ctaLabel = view.selSlot
      ? `קביעת תור · ${esc(view.selSlot)} ${esc(u.relativeDay(view.selDate))}`
      : "בחרו שעה לתור";

    return `
      ${condensedClient() ? `<button class="btn btn-ghost btn-sm home-back" data-tab="home">‹ חזרה לעמוד הבית</button>` : (shopCover(st) ? `<div class="client-cover"><img src="${esc(shopCover(st))}" alt=""></div>` : "")}
      ${rescheduleBanner(st)}
      ${alertBanner(st)}
      ${notifBanner()}
      ${arrivalBanner(st)}
      ${reviewBanner(st)}
      <div class="section-title">בחירת שירות</div>
      <div class="svc-select">${svcCards}</div>

      <div class="section-title">בחירת יום</div>
      <div class="days-scroll">${dayChips}</div>

      <div class="section-title">${esc(u.longDate(view.selDate))} · שעות פנויות</div>
      ${slotsHtml}

      <div style="height:14px"></div>
      <button class="btn btn-primary" data-act="open-confirm" ${view.selSlot ? "" : "disabled"}>${ctaLabel}</button>
      ${condensedClient() ? "" : `
      ${aboutCard(st)}
      ${cShow(st, "showHours") ? hoursCard(st) : ""}
      ${installCard()}
      ${mapsCard(st)}
      ${cShow(st, "showShare") ? shareCard() : ""}
      <p class="hint" style="text-align:center;margin-top:22px">
        מנהלים מספרה? <a href="#new" data-act="open-signup" style="color:var(--sky)">פתחו מערכת תורים משלכם ›</a>
      </p>
      <p class="hint" style="text-align:center;margin-top:8px">
        <a href="privacy.html" target="_blank" rel="noopener" style="color:var(--muted)">מדיניות פרטיות</a>
        · <a href="terms.html" target="_blank" rel="noopener" style="color:var(--muted)">תנאי שימוש</a>
        · <span data-act="delete-my-data" style="color:var(--muted);text-decoration:underline;cursor:pointer">מחיקת הנתונים שלי</span>
      </p>`}
    `;
  }

  /* כרטיס "התקן אפליקציה" בולט ללקוח — מוסתר אם כבר מותקן */
  function installCard() {
    if (appInstalled()) return "";
    return `
      <div class="section-title">📲 קבעו תור בקליק — התקינו כאפליקציה</div>
      <div class="card">
        <div style="display:flex;align-items:center;gap:13px">
          <div style="width:44px;height:44px;border-radius:12px;flex:none;display:grid;place-items:center;background:var(--surface-3);font-size:21px">📲</div>
          <div style="flex:1;min-width:0">
            <div style="font-weight:700;font-size:15px">אייקון על מסך הבית שלכם</div>
            <div class="hint" style="margin-top:1px">גישה מהירה + תזכורת לפני התור</div>
          </div>
        </div>
        <button class="btn btn-primary btn-sm" data-act="install-app" style="width:100%;margin-top:13px">📲 התקנת האפליקציה</button>
        ${isIOS() ? `<div class="hint" style="text-align:center;margin-top:8px">באייפון: לחצו על <b>שיתוף</b> ⬆️ ואז <b>״הוסף למסך הבית״</b></div>` : ""}
      </div>`;
  }

  /* כרטיס התקנה להגדרות הספר — נעלם לגמרי אחרי שהאפליקציה מותקנת (מסך מלא),
     כדי שהכפתור לא יופיע לאחר ההורדה. */
  function installSettingsCard() {
    if (appInstalled()) return "";
    return `
      <div class="section-title">📲 התקנה על מסך הבית</div>
      <div class="card">
        <p class="hint" style="margin-top:0;margin-bottom:12px">התקינו את BarberTor כאפליקציה — אייקון על מסך הבית, פתיחה מהירה וקבלת התראות גם כשהיא סגורה.</p>
        <button class="btn btn-primary btn-sm" data-act="install-app" style="width:100%">📲 התקנת האפליקציה</button>
        ${isIOS() ? `<div class="hint" style="text-align:center;margin-top:8px">באייפון: לחצו על <b>שיתוף</b> ⬆️ ואז <b>״הוסף למסך הבית״</b></div>` : ""}
      </div>`;
  }

  function shareCard() {
    return `
      <div class="section-title">📣 אהבתם? שתפו</div>
      <div class="card">
        <div style="display:flex;align-items:center;gap:13px">
          <div style="width:44px;height:44px;border-radius:12px;flex:none;display:grid;place-items:center;background:var(--surface-3);font-size:21px">💬</div>
          <div style="flex:1;min-width:0">
            <div style="font-weight:700;font-size:15px">שתפו את המספרה עם חברים</div>
            <div class="hint" style="margin-top:1px">כמה שיותר תספורות טובות בעולם 😄</div>
          </div>
        </div>
        <button class="btn btn-primary btn-sm" data-act="share-app" style="width:100%;margin-top:13px">🔗 שיתוף</button>
      </div>`;
  }

  /* ---------- באנר "שינוי מועד" ---------- */
  function rescheduleBanner(st) {
    if (!view.rescheduleId) return "";
    const b = st.bookings.find((x) => x.id === view.rescheduleId);
    if (!b) { view.rescheduleId = null; return ""; }
    return `
    <div class="banner sky">
      <span class="bn-ico">🔄</span>
      <div class="bn-body">
        <div class="bn-title">שינוי מועד לתור</div>
        <div class="bn-sub">${esc(b.serviceName)} · כרגע ${esc(u.relativeDay(b.date))} ${esc(b.start)} — בחרו מועד חדש</div>
      </div>
      <div class="bn-actions">
        <button class="btn btn-ghost btn-sm" data-act="cancel-reschedule">ביטול</button>
      </div>
    </div>`;
  }

  /* ---------- באנר "התפנה תור" (רשימת המתנה) ---------- */
  function alertBanner(st) {
    const now = Date.now();
    const mine = (st.alerts || [])
      .filter((a) => a.userId === identity.userId && u.dateTime(a.date, a.start).getTime() > now)
      .sort((a, z) => u.dateTime(a.date, a.start) - u.dateTime(z.date, z.start))[0];
    if (!mine) return "";
    return `
    <div class="banner sky pulse">
      <span class="bn-ico">🎉</span>
      <div class="bn-body">
        <div class="bn-title">התפנה תור ${esc(u.relativeDay(mine.date))} בשעה ${esc(mine.start)}!</div>
        <div class="bn-sub">מהרו להזמין לפני שמישהו אחר יתפוס</div>
      </div>
      <div class="bn-actions">
        <button class="btn btn-primary btn-sm" data-act="alert-book" data-id="${mine.id}" data-date="${mine.date}" data-start="${mine.start}">הזמן עכשיו</button>
        <button class="btn btn-ghost btn-sm" data-act="alert-dismiss" data-id="${mine.id}">לא עכשיו</button>
      </div>
    </div>`;
  }

  /* ---------- באנר בקשת דירוג אחרי תספורת ---------- */
  function reviewsMuted() { return localStorage.getItem("ug_reviews_off__" + SHOP) === "1"; }
  function reviewableBooking(st) {
    const skip = new Set(JSON.parse(localStorage.getItem("ug_review_skip") || "[]"));
    const reviewed = new Set((st.reviews || []).filter((r) => r.userId === identity.userId).map((r) => r.bookingId));
    const now = Date.now();
    const o = st.bookings
      .filter((x) => x.userId === identity.userId && x.status !== "cancelled" && x.status !== "noshow")
      .map((x) => ({ x, end: u.dateTime(x.date, x.end).getTime() }))
      .filter((o) => o.end < now && now - o.end < 14 * 86400000 && !reviewed.has(o.x.id) && !skip.has(o.x.id))
      .sort((a, z) => z.end - a.end)[0];
    return o ? o.x : null;
  }
  function reviewBanner(st) {
    if (reviewsMuted()) return "";
    const b = reviewableBooking(st);
    if (!b) return "";
    return `
    <div class="banner good">
      <span class="bn-ico">⭐</span>
      <div class="bn-body">
        <div class="bn-title">איך הייתה התספורת?</div>
        <div class="bn-sub">${esc(b.serviceName)} · <span class="lnk" data-act="review-skip" data-id="${b.id}">אחר כך</span> · <span class="lnk" data-act="review-never">אל תשאל שוב</span></div>
      </div>
      <div class="bn-actions">
        <button class="btn btn-primary btn-sm" data-act="open-review" data-id="${b.id}">דרג</button>
      </div>
    </div>`;
  }

  /* רשתות חברתיות — טבלת מטא-נתונים אחת לכל הפלטפורמות (אינסטגרם/טיקטוק/פייסבוק/יוטיוב).
     שומרים רק את "שם המשתמש" (handle) — מקבלים גם קישור מלא, גם @user וגם שם נקי,
     ומחלצים את המזהה. הצגה ופתיחה בונות מזה URL לפי הפלטפורמה. */
  const SOCIAL_PLATFORMS = [
    { key: "instagram", label: "אינסטגרם", en: "Instagram", emoji: "📷", placeholder: "dani.barber",
      previewPrefix: "instagram.com/", urlPrefix: "https://instagram.com/",
      domainPat: /^https?:\/\/(www\.)?instagram\.com\//i, atInUrl: false, maxLen: 30,
      svg: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M17 2H7C4 2 2 4 2 7v10c0 3 2 5 5 5h10c3 0 5-2 5-5V7c0-3-2-5-5-5zM12 7a5 5 0 1 1 0 10 5 5 0 0 1 0-10zm0 2a3 3 0 1 0 0 6 3 3 0 0 0 0-6zm5-3a1 1 0 1 1 0 2 1 1 0 0 1 0-2z"/></svg>' },
    { key: "tiktok", label: "טיקטוק", en: "TikTok", emoji: "🎵", placeholder: "dani.barber",
      previewPrefix: "tiktok.com/@", urlPrefix: "https://www.tiktok.com/@",
      domainPat: /^https?:\/\/(www\.)?tiktok\.com\//i, atInUrl: true, maxLen: 24,
      svg: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2C6 2 2 6 2 12s4 10 10 10 10-4 10-10S18 2 12 2zm2.5 5.2c.2 1.1.9 2 2 2.4v1.8c-.8 0-1.5-.2-2.1-.6v3.9a3.2 3.2 0 1 1-3.2-3.2c.2 0 .3 0 .5.1v1.8a1.4 1.4 0 1 0 1 1.3V7.2h1.8z"/></svg>' },
    { key: "facebook", label: "פייסבוק", en: "Facebook", emoji: "📘", placeholder: "dani.barber",
      previewPrefix: "facebook.com/", urlPrefix: "https://www.facebook.com/",
      domainPat: /^https?:\/\/(www\.)?(facebook\.com|fb\.com|m\.facebook\.com)\//i, atInUrl: false, maxLen: 50,
      svg: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M20 2H4a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h8v-9H9v-3h3V7.5c0-2.4 1.5-3.5 3.5-3.5.9 0 1.7.1 2 .1V6h-1.3c-1 0-1.2.5-1.2 1.2V10h3l-.4 3H15v9h5a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2z"/></svg>' },
    { key: "youtube", label: "יוטיוב", en: "YouTube", emoji: "▶️", placeholder: "dani.barber",
      previewPrefix: "youtube.com/@", urlPrefix: "https://www.youtube.com/@",
      domainPat: /^https?:\/\/(www\.)?(youtube\.com|youtu\.be)\//i, atInUrl: true, maxLen: 30,
      svg: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M22 6.5c-.2-1-1-1.8-2-2C18 4 12 4 12 4s-6 0-8 .5c-1 .2-1.8 1-2 2C1.5 8.5 1.5 12 1.5 12s0 3.5.5 5.5c.2 1 1 1.8 2 2 2 .5 8 .5 8 .5s6 0 8-.5c1-.2 1.8-1 2-2 .5-2 .5-5.5.5-5.5s0-3.5-.5-5.5zM10 15.5V8.5l6 3.5-6 3.5z"/></svg>' },
  ];
  function socialMeta(key) { return SOCIAL_PLATFORMS.find((p) => p.key === key) || null; }
  function socialHandle(v, key) {
    const meta = socialMeta(key); if (!meta) return "";
    let s = String(v || "").trim();
    if (!s) return "";
    s = s.replace(meta.domainPat, "").replace(/^@/, "");
    // ביוטיוב: משיכת השם מקישורים ישנים (/c/name, /user/name, /channel/UC...)
    if (key === "youtube") s = s.replace(/^(c|user|channel)\//i, "");
    s = s.split(/[/?#]/)[0];
    // פייסבוק מרשה גם מקף וגם מספרים (profile.php?id=... כבר נחתך למעלה)
    const allowedPat = key === "facebook" ? /[^A-Za-z0-9._-]/g : /[^A-Za-z0-9._]/g;
    s = s.replace(allowedPat, "").replace(/^\.+|\.+$/g, "");
    return s.slice(0, meta.maxLen);
  }
  function socialUrl(handle, key) {
    const meta = socialMeta(key);
    if (!meta || !handle) return "";
    return meta.urlPrefix + handle;
  }
  // תאימות אחורה — קוד קיים משתמש ב-igHandle
  function igHandle(v) { return socialHandle(v, "instagram"); }

  /* ---------- תמיכה — דיווח תקלה בוואטסאפ (המספר עצמו לא מוצג) ---------- */
  function supportCard() {
    const sup = UG_CONFIG.support || {};
    if (!sup.waPhone) return "";
    const hours = Number(sup.slaHours) || 30;
    return `
      <div class="section-title">🛟 תמיכה</div>
      <div class="card">
        <p class="hint" style="margin:0 0 12px">נתקלתם בתקלה או שמשהו לא עובד כמו שצריך? שלחו לנו הודעה — <b>מתחייבים לטפל בה תוך עד ${hours} שעות</b>.</p>
        <button class="btn btn-wa" data-act="support-wa">
          <svg class="wa-ico" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M.057 24l1.687-6.163a11.867 11.867 0 01-1.587-5.945C.16 5.335 5.495 0 12.05 0a11.82 11.82 0 018.413 3.488 11.824 11.824 0 013.48 8.414c-.003 6.557-5.338 11.892-11.893 11.892a11.9 11.9 0 01-5.688-1.448L.057 24zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884-.001 2.225.651 3.891 1.746 5.634l-.999 3.648 3.742-.981zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.767.967-.94 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372s-1.04 1.016-1.04 2.479 1.065 2.876 1.213 3.074c.149.198 2.095 3.2 5.076 4.487.709.306 1.263.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.695.248-1.29.173-1.414z"/></svg>
          שליחת הודעה בוואטסאפ
        </button>
      </div>`;
  }

  function openSupportWa() {
    const sup = UG_CONFIG.support || {};
    if (!sup.waPhone) return;
    const st = Store.get();
    const shop = (st && st.shop && st.shop.name) || "";
    const msg = `שלום, יש לי תקלה ב-BarberTor.\nהמספרה: ${shop} (${SHOP})\nגרסה: ${APP_VERSION}\n\nהתקלה:\n`;
    openExternal("https://wa.me/" + sup.waPhone + "?text=" + encodeURIComponent(msg));
  }

  /* ---------- אישור מדיניות פרטיות ---------- */
  const PRIVACY_KEY = "ug_privacy_ok_v1";
  function privacyAccepted() {
    try { return localStorage.getItem(PRIVACY_KEY) === "1"; } catch (e) { return true; }
  }
  function acceptPrivacy() {
    try { localStorage.setItem(PRIVACY_KEY, "1"); } catch (e) {}
    closeModal();
    toast("תודה! אפשר להתחיל 🙂", "good", "✓");
    render();
    setTimeout(() => promptNotif(), 700);   // עכשיו אפשר להציע התראות
  }
  // חלון חסימה שמוצג פעם אחת עד לאישור
  function promptPrivacy() {
    if (privacyAccepted()) return;
    if ($("#modalBack") && $("#modalBack").classList.contains("open")) return;
    openModal(`
      <div class="m-title">🔒 פרטיות ותנאי שימוש</div>
      <div class="m-sub">רגע לפני שמתחילים</div>
      <p class="hint" style="margin:12px 0 6px;line-height:1.7">
        כדי לקבוע תור אנחנו שומרים את השם, הטלפון והתורים שלכם — ומשתמשים בהם רק
        לניהול התורים ולשליחת תזכורות. לא מוכרים ולא מעבירים את הפרטים לאף אחד.
      </p>
      <p class="hint" style="margin:0 0 14px">
        <a href="privacy.html" target="_blank" rel="noopener" style="color:var(--sky)">מדיניות הפרטיות ›</a>
        &nbsp;·&nbsp;
        <a href="terms.html" target="_blank" rel="noopener" style="color:var(--sky)">תנאי השימוש ›</a>
      </p>
      <button class="btn btn-primary" data-act="accept-privacy">אני מאשר/ת ומסכים/ה</button>
    `);
  }

  /* ---------- כרטיס "קצת עלינו" ---------- */
  function aboutCard(st) {
    const about = (st.shop.about || "").trim();
    const socs = SOCIAL_PLATFORMS
      .map((p) => ({ p: p, h: socialHandle(st.shop[p.key] || "", p.key) }))
      .filter((x) => x.h);
    if (!about && !socs.length) return "";
    return `
      <div class="section-title">✨ קצת עלינו</div>
      <div class="card">
        ${about ? `<p style="margin:0;line-height:1.65;font-size:14.5px;white-space:pre-line">${esc(about)}</p>` : ""}
        ${socs.length ? `<div class="socials"${about ? ` style="margin-top:14px"` : ""}>
          ${socs.map((x) => `<a class="soc-wrap" href="${esc(socialUrl(x.h, x.p.key))}" target="_blank" rel="noopener" aria-label="${esc(x.p.label)}">
            <span class="soc soc-${x.p.key}">${x.p.svg}</span>
            <span class="soc-name">${esc(x.p.en)}</span>
          </a>`).join("")}
        </div>` : ""}
      </div>`;
  }

  /* ---------- כרטיס שעות פעילות ---------- */
  function hoursCard(st) {
    const todayDow = new Date().getDay();
    const rows = [];
    for (let i = 0; i < 7; i++) {
      const d = st.schedule[i];
      rows.push(`
        <div class="hours-row ${i === todayDow ? "today" : ""}">
          <span class="hr-day">${u.DOW[i]}${i === todayDow ? " · היום" : ""}</span>
          <span class="hr-time" dir="ltr">${d.active ? `${esc(d.open)}–${esc(d.close)}` : "סגור"}</span>
        </div>`);
    }
    return `
      <div class="section-title">🕒 שעות פעילות</div>
      <div class="card hours-card">${rows.join("")}</div>`;
  }

  /* ---------- כרטיס "איך מגיעים" ---------- */
  function mapsCard(st) {
    const addr = (st.shop.address || "").trim();
    if (!addr) return "";
    const q = encodeURIComponent(addr);
    return `
      <div class="section-title">📍 איך מגיעים?</div>
      <div class="card">
        <div style="display:flex;align-items:center;gap:13px">
          <div style="width:44px;height:44px;border-radius:12px;flex:none;display:grid;place-items:center;background:var(--surface-3);font-size:21px">🗺️</div>
          <div style="flex:1;min-width:0">
            <div style="font-weight:700;font-size:15px">${esc(addr)}</div>
            <div class="hint" style="margin-top:1px">לחצו לפתיחת ניווט</div>
          </div>
        </div>
        <div class="btn-row btn-row-wrap" style="margin-top:13px">
          <a class="btn btn-sm nav-btn" target="_blank" rel="noopener" href="https://waze.com/ul?q=${q}&navigate=yes">🚗 Waze</a>
          <a class="btn btn-sm nav-btn" target="_blank" rel="noopener" href="https://www.google.com/maps/search/?api=1&query=${q}">🗺️ Google Maps</a>
          <a class="btn btn-sm nav-btn" target="_blank" rel="noopener" href="https://moovit.com/?to=${q}">🚌 Moovit</a>
        </div>
      </div>`;
  }

  function clientMine(st) {
    const now = Date.now();
    const mine = st.bookings
      .filter((b) => b.userId === identity.userId && b.status !== "cancelled")
      .map((b) => ({ b, ts: u.dateTime(b.date, b.start).getTime() }))
      .sort((a, z) => a.ts - z.ts);
    const upcoming = mine.filter((x) => x.ts > now - 60 * 60000);
    const cleared = Number(localStorage.getItem("ug_hist_cleared__" + SHOP) || 0);
    const past = mine.filter((x) => x.ts <= now - 60 * 60000 && x.ts >= cleared).reverse();
    const myWaits = (st.waitlist || [])
      .filter((w) => w.userId === identity.userId)
      .map((w) => ({ w, ts: u.dateTime(w.date, w.start).getTime() }))
      .sort((a, z) => a.ts - z.ts);

    if (!mine.length && !myWaits.length) {
      return alertBanner(st) + reviewBanner(st) +
        emptyState("🎟️", "אין לך תורים", condensedClient() ? "לחצו ״בית״ ואז ״הזמנת תור״ כדי לקבוע את התור הראשון" : "עברו ל״קביעת תור״ כדי לקבוע את התור הראשון");
    }
    const card = (x, isPast) => {
      const b = x.b;
      const st2 = b.status === "confirmed"
        ? `<span class="status-tag status-confirmed">✓ אושר</span>`
        : `<span class="status-tag status-booked">ממתין</span>`;
      const actions = isPast
        ? `<div class="btn-row" style="margin-top:12px">
             <button class="btn btn-sm" data-act="book-again" data-service="${esc(b.serviceId)}" data-staff="${esc(b.staff || "")}">🔁 קבע שוב</button>
           </div>`
        : `<div class="btn-row btn-row-wrap" style="margin-top:12px">
          ${b.status !== "confirmed" ? `<button class="btn btn-sm" data-act="confirm-arrival" data-id="${b.id}">אשר הגעה</button>` : ""}
          <button class="btn btn-sm" data-act="reschedule" data-id="${b.id}">🔄 שינוי מועד</button>
          <button class="btn btn-sm" data-act="add-cal" data-id="${b.id}">📅 ליומן</button>
          <button class="btn btn-sm btn-danger" data-act="cancel-booking" data-id="${b.id}">ביטול</button>
        </div>`;
      return `
      <div class="card" style="padding:15px 16px;${isPast ? "opacity:.6" : ""}">
        <div class="booking" style="padding:0;border:none;background:none">
          <div class="bk-time">
            <div class="bt-h">${esc(b.start)}</div>
            <div class="bt-d">${esc(u.relativeDay(b.date))}</div>
          </div>
          <div class="bk-body">
            <div class="bk-title">${esc(b.serviceName)}</div>
            <div class="bk-sub">${esc(u.longDate(b.date))} · <b>${u.fmtPrice(b.price)}</b></div>
          </div>
          ${st2}
        </div>
        ${actions}
      </div>`;
    };
    let html = alertBanner(st) + reviewBanner(st);
    // "קבע שוב כמו פעם קודמת" — קיצור מהיר על בסיס התור האחרון (לקוחות חוזרים)
    const lastBk = past.length ? past[0].b : null;
    if (lastBk) {
      html += `
      <div class="card again-card">
        <div class="again-ico">🔁</div>
        <div class="again-body">
          <b>קבע שוב כמו פעם קודמת</b>
          <div class="hint">${esc(lastBk.serviceName)}${lastBk.staff ? " · עם " + esc(lastBk.staff) : ""}</div>
        </div>
        <button class="btn btn-primary btn-sm" data-act="book-again" data-service="${esc(lastBk.serviceId)}" data-staff="${esc(lastBk.staff || "")}">קבע שוב</button>
      </div>`;
    }
    if (upcoming.length) {
      html += `<div class="section-title">תורים קרובים</div>` + upcoming.map((x) => card(x, false)).join("");
    }
    if (myWaits.length) {
      html += `<div class="section-title">רשימת המתנה 🔔</div>` + myWaits.map((x) => `
      <div class="card" style="padding:13px 15px">
        <div class="booking" style="padding:0;border:none;background:none">
          <div class="bk-time">
            <div class="bt-h">${esc(x.w.start)}</div>
            <div class="bt-d">${esc(u.relativeDay(x.w.date))}</div>
          </div>
          <div class="bk-body">
            <div class="bk-title">ממתין שיתפנה</div>
            <div class="bk-sub">${esc(u.longDate(x.w.date))} · נודיע לך ברגע שהתור יתפנה</div>
          </div>
          <button class="btn btn-sm btn-danger" data-act="leave-wait" data-id="${x.w.id}">הסר</button>
        </div>
      </div>`).join("");
    }
    if (past.length) {
      html += `<div class="section-title" style="display:flex;justify-content:space-between;align-items:center">
        <span>היסטוריה</span>
        <span class="lnk" data-act="clear-history" style="color:var(--bad);font-size:12px">🗑️ מחיקת היסטוריה</span>
      </div>` + past.map((x) => card(x, true)).join("");
    }
    return html;
  }

  function confirmCancelBooking(id) {
    const b = Store.get().bookings.find((x) => x.id === id);
    if (!b) return;
    openModal(`
      <div class="m-title">ביטול תור</div>
      <div class="m-sub">${esc(b.serviceName)} · ${esc(u.longDate(b.date))} בשעה ${esc(b.start)}</div>
      <p style="font-size:14px;color:var(--muted);margin:6px 0 20px">האם לבטל את התור? לא ניתן לשחזר, אך ניתן לקבוע תור חדש.</p>
      <button class="btn btn-danger" data-act="do-cancel-booking" data-id="${b.id}">כן, בטלו את התור</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">חזרה</button>
    `);
  }

  function confirmClearHistory() {
    openModal(`
      <div class="m-title">מחיקת היסטוריה</div>
      <div class="m-sub">היסטוריית התורים שלך תוסתר לצמיתות מהמכשיר הזה</div>
      <p style="font-size:14px;color:var(--muted);margin:6px 0 20px">התורים העתידיים שלך יישארו. הפעולה משפיעה רק על התצוגה שלך.</p>
      <button class="btn btn-danger" data-act="do-clear-history">מחיקה</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>
    `);
  }

  /* ---------- מודאל אישור הזמנה ---------- */
  function openConfirm() {
    const st = Store.get();
    const service = st.services.find((s) => s.id === view.selService);
    if (!service || !view.selSlot) return;
    const isResched = !!view.rescheduleId;
    openModal(`
      <div class="m-title">${isResched ? "אישור שינוי מועד" : "אישור קביעת תור"}</div>
      <div class="m-sub">בדקו את הפרטים לפני האישור</div>
      <div class="summary-row"><span class="sr-k">שירות</span><span class="sr-v">${esc(service.name)}</span></div>
      <div class="summary-row"><span class="sr-k">תאריך</span><span class="sr-v">${esc(u.longDate(view.selDate))}</span></div>
      <div class="summary-row"><span class="sr-k">שעה</span><span class="sr-v">${esc(view.selSlot)}</span></div>
      <div class="summary-row"><span class="sr-k">משך</span><span class="sr-v">${u.fmtDuration(service.durationMin)}</span></div>
      <div class="summary-row"><span class="sr-k">מחיר</span><span class="sr-v big">${u.fmtPrice(service.price)}</span></div>
      <div style="height:18px"></div>
      ${(st.shop.staff && st.shop.staff.length) ? `
      <div class="field"><label>ספר מועדף <span class="opt-star">*</span></label>
        <select class="input" id="cf-staff">
          <option value="">אין העדפה</option>
          ${st.shop.staff.map((n) => `<option value="${esc(n)}" ${view.selStaff === n ? "selected" : ""}>${esc(n)}</option>`).join("")}
        </select>
        <div class="hint" style="margin-top:5px">* בקשה בלבד — ייתכן שהתור יתקיים עם ספר אחר.</div>
      </div>` : ""}
      <div class="field-row">
        <div class="field"><label>שם פרטי <span class="req">*</span></label>
          <input class="input" id="cf-first" placeholder="שם פרטי" value="${esc(identity.firstName || "")}"></div>
        <div class="field"><label>שם משפחה <span class="req">*</span></label>
          <input class="input" id="cf-last" placeholder="שם משפחה" value="${esc(identity.lastName || "")}"></div>
      </div>
      <div class="field"><label>טלפון נייד <span class="req">*</span></label>
        <input class="input" id="cf-phone" type="tel" inputmode="tel" placeholder="050-0000000" value="${esc(identity.phone)}"></div>
      ${(UG.Email && UG.Email.configured()) ? `
      <div class="field"><label>אימייל <span class="opt">(לא חובה — לקבלת אישור למייל)</span></label>
        <input class="input" id="cf-email" type="email" inputmode="email" autocomplete="email" placeholder="name@email.com" value="${esc(identity.email || "")}"></div>` : ""}
      <button class="btn btn-primary" data-act="do-book">${isResched ? "אישור המועד החדש" : "אישור וקביעת התור"}</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>
    `);
  }

  /* קריאת שדות איש קשר מהמודאל + ולידציה (שם פרטי, משפחה, טלפון תקין) */
  function readContact() {
    const first = ($("#cf-first") && $("#cf-first").value.trim()) || "";
    const last = ($("#cf-last") && $("#cf-last").value.trim()) || "";
    const phoneRaw = ($("#cf-phone") && $("#cf-phone").value.trim()) || "";
    if (!first) { toast("נא להזין שם פרטי", "", "✋"); return null; }
    if (!last) { toast("נא להזין שם משפחה", "", "✋"); return null; }
    if (!u.isValidPhone(phoneRaw)) { toast("מספר טלפון לא תקין", "", "📵"); return null; }
    const phone = u.fmtPhone(phoneRaw);
    const emailEl = $("#cf-email");
    const email = emailEl ? emailEl.value.trim() : (identity.email || "");
    // מייל אינו חובה — נבדק רק אם הוזן, כדי שנשלח אישור לכתובת תקינה
    if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { toast("כתובת אימייל לא תקינה", "", "📧"); return null; }
    const name = first + " " + last;
    identity.firstName = first; identity.lastName = last; identity.name = name; identity.phone = phone;
    identity.email = email;
    saveIdentity();
    return { first, last, phone, name, email };
  }

  /* קישור "הוסף ליומן Google" — נבנה מהתאריך והשעה של התור (אזור זמן ישראל) */
  function gcalUrl(bk, shop) {
    const d = (bk.date || "").replace(/-/g, "");         // 20260730
    const s = (bk.start || "").replace(":", "") + "00";  // 140000
    const e = (bk.end || "").replace(":", "") + "00";    // 143000
    if (d.length !== 8 || s.length !== 6 || e.length !== 6) return "";
    const detParts = [];
    if (bk.serviceName) detParts.push("שירות: " + bk.serviceName);
    if (shop.phone) detParts.push("טלפון: " + shop.phone);
    const p = new URLSearchParams({
      action: "TEMPLATE",
      text: (bk.serviceName || "תור") + " · " + (shop.name || "מספרה"),
      dates: d + "T" + s + "/" + d + "T" + e,
      details: detParts.join("\n"),
      location: shop.address || "",
      ctz: "Asia/Jerusalem",
    });
    return "https://calendar.google.com/calendar/render?" + p.toString();
  }

  /* שליחת מייל אישור ללקוח (אם EmailJS מוגדר והוזן אימייל) — לא חוסם את הזרימה */
  function sendBookingEmail(bk) {
    if (!bk || !bk.email || !(UG.Email && UG.Email.configured())) return;
    const shop = (Store.get() && Store.get().shop) || {};
    UG.Email.sendBooking({
      to_email: bk.email,
      to_name: bk.userName || "",
      service: bk.serviceName || "",
      date: u.longDate(bk.date),
      time: bk.start,
      duration: u.fmtDuration(bk.durationMin),
      price: u.fmtPrice(bk.price),
      shop_name: shop.name || "",
      shop_address: shop.address || "",
      shop_phone: shop.phone || "",
      calendar_url: gcalUrl(bk, shop),
    }).then((res) => {
      if (res && res.sent) toast("אישור נשלח למייל 📧", "sky", "📧");
      else toast("מייל נכשל [tpl=" + ((UG_CONFIG.emailjs || {}).templateId || "?") + "]: " + (res && res.error || "unknown"), "", "⚠️");
    }).catch((e) => toast("שגיאת מייל: " + (e && e.message || e), "", "⚠️"));
  }

  async function doBook() {
    const contact = readContact();
    if (!contact) return;
    const bookedDate = view.selDate, bookedStart = view.selSlot;
    const reschedId = view.rescheduleId;
    const staffEl = $("#cf-staff");
    const staff = staffEl ? staffEl.value : (view.selStaff || "");
    view.selStaff = staff;
    const btn = $("[data-act='do-book']"); if (btn) { btn.disabled = true; btn.textContent = reschedId ? "מעדכן…" : "קובע תור…"; }
    // רשת ביטחון: כל כשל בלתי צפוי (רשת/הרשאה) לא ישאיר את הכפתור תקוע על
    // "קובע תור…" — נסגור, נודיע, ונאפשר ניסיון חוזר.
    let res;
    try {
      res = await Store.createBooking({
        serviceId: view.selService, date: bookedDate, start: bookedStart,
        userId: identity.userId, userName: contact.name, phone: contact.phone, email: contact.email,
        staff: staff,
        excludeBookingId: reschedId || undefined,   // אל תתנגש עם התור המקורי בעת שינוי מועד
      });
    } catch (e) {
      res = { ok: false, reason: "לא הצלחנו לקבוע את התור — נסו שוב." };
    }
    if (btn) { btn.disabled = false; btn.textContent = reschedId ? "אישור המועד החדש" : "אישור וקביעת התור"; }
    if (!res || !res.ok) {
      closeModal();
      toast((res && res.reason) || "לא ניתן לקבוע את התור", "", "⚠️");
      view.selSlot = null;
      render();   // התור המקורי נשמר — אפשר לבחור מועד אחר
      return;
    }
    // שינוי מועד: המועד החדש נקבע בהצלחה — כעת מבטלים את התור הישן
    if (reschedId) { if (clientCancelSeen) clientCancelSeen.add(reschedId); await Store.setBookingStatus(reschedId, "cancelled", "client"); view.rescheduleId = null; }
    closeModal();
    view.selSlot = null;
    view.clientTab = "mine";
    toast(reschedId ? "המועד עודכן ✓" : "התור נקבע בהצלחה!", "good", reschedId ? "🔄" : "🎉");
    // מייל אישור ללקוח. במספרה מאובטחת השם/המייל אינם בתור הציבורי, לכן משלימים
    // אותם מהפרטים המקומיים שהלקוח מילא זה עתה.
    sendBookingEmail(Object.assign({}, res.booking, { userName: contact.name, email: contact.email }));
    // אם ההזמנה הגיעה מהתראת "התפנה תור" — נקה את ההתראה
    const stale = (Store.get().alerts || [])
      .filter((a) => a.userId === identity.userId && a.date === bookedDate && a.start === bookedStart)
      .map((a) => a.id);
    if (stale.length) await Store.consumeAlert(stale);
    // תזמון תזכורת + הצעה לאשר התראות
    if (Notify.permission() === "granted") {
      ensureFcm();
      Notify.scheduleReminders(Store.get().bookings, identity.userId, Store.get().shop);
    } else if (Notify.supported() && Notify.permission() === "default") {
      const r = await Notify.requestPermission();
      if (r === "granted") {
        toast("התראות הופעלו — נזכיר לך לפני התור", "sky", "🔔");
        ensureFcm();
        Notify.scheduleReminders(Store.get().bookings, identity.userId, Store.get().shop);
      }
    }
    render();
  }

  /* ---------- העלאת תמונה לגלריה (דחיסה בצד הלקוח) ---------- */
  function compressImage(file, maxDim, quality) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        URL.revokeObjectURL(url);
        let w = img.naturalWidth, h = img.naturalHeight;
        const scale = Math.min(1, maxDim / Math.max(w, h));
        w = Math.round(w * scale); h = Math.round(h * scale);
        const canvas = document.createElement("canvas");
        canvas.width = w; canvas.height = h;
        canvas.getContext("2d").drawImage(img, 0, 0, w, h);
        let q = quality, out = canvas.toDataURL("image/jpeg", q);
        while (out.length > 900000 && q > 0.4) { q -= 0.1; out = canvas.toDataURL("image/jpeg", q); }
        resolve(out);
      };
      img.onerror = reject;
      img.src = url;
    });
  }

  async function handleUpload(file) {
    if (!file || !file.type || file.type.indexOf("image/") !== 0) { toast("נא לבחור קובץ תמונה", "", "🖼️"); return; }
    toast("מעלה תמונה…", "sky", "⏳");
    try {
      const dataUrl = await compressImage(file, 1100, 0.72);
      await Store.addPhoto(dataUrl, "");
      toast("התמונה נוספה לגלריה ✓", "good", "🖼️");
      render();
    } catch (e) {
      toast("לא הצלחנו להעלות את התמונה", "", "⚠️");
    }
  }

  /* ---------- לוגו המספרה (דחיסה + שמירת שקיפות) ---------- */
  function compressLogo(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        URL.revokeObjectURL(url);
        let w = img.naturalWidth, h = img.naturalHeight;
        const scale = Math.min(1, 320 / Math.max(w, h));
        w = Math.max(1, Math.round(w * scale)); h = Math.max(1, Math.round(h * scale));
        const canvas = document.createElement("canvas");
        canvas.width = w; canvas.height = h;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, w, h);
        let out = canvas.toDataURL("image/png");   // PNG — שומר שקיפות של לוגו
        if (out.length > 200000) {                 // גדול מדי — רקע לבן + JPEG
          ctx.globalCompositeOperation = "destination-over";
          ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, w, h);
          let q = 0.85; out = canvas.toDataURL("image/jpeg", q);
          while (out.length > 200000 && q > 0.4) { q -= 0.1; out = canvas.toDataURL("image/jpeg", q); }
        }
        resolve(out);
      };
      img.onerror = reject;
      img.src = url;
    });
  }

  /* ---------- חיתוך תמונה לפני שמירה (לוגו/קאבר) ----------
     מודאל עם גרירה (עכבר/מגע) וזום (סליידר/צביטה), חיתוך ליחס קבוע ואז דחיסה —
     בלי ספריות חיצוניות, רק Canvas ואירועי מצביע רגילים. */
  function touchDist(t) { const dx = t[0].clientX - t[1].clientX, dy = t[0].clientY - t[1].clientY; return Math.hypot(dx, dy); }
  function openCropper(file, opts) {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onerror = () => { URL.revokeObjectURL(url); toast("לא הצלחנו לטעון את התמונה", "", "⚠️"); };
    img.onload = () => {
      const ratio = opts.ratio || 1;
      openModal(`
        <div class="m-title">✂️ ${esc(opts.title || "חיתוך תמונה")}</div>
        <div class="m-sub">גררו כדי למקם, והחליקו כדי להתקרב</div>
        <div class="crop-frame" id="crop-frame" style="--ar:${ratio}"><img id="crop-img" src="${url}" alt="" draggable="false"></div>
        <input type="range" id="crop-zoom" min="0" max="100" value="0" style="width:100%;margin-top:16px">
        <div class="btn-row" style="margin-top:14px">
          <button class="btn btn-primary" data-act2="crop-confirm">שמירה</button>
          <button class="btn btn-ghost" data-act2="crop-cancel">ביטול</button>
        </div>
      `);
      const frame = $("#crop-frame"), imgEl = $("#crop-img"), zoomEl = $("#crop-zoom");
      const iw = img.naturalWidth, ih = img.naturalHeight;
      const rect = frame.getBoundingClientRect();
      const frameW = rect.width, frameH = rect.height;
      const baseScale = Math.max(frameW / iw, frameH / ih);
      const maxZoom = 3;
      let zoom = 1, tx = (frameW - iw * baseScale) / 2, ty = (frameH - ih * baseScale) / 2;
      const apply = () => {
        const scale = baseScale * zoom;
        const sw = iw * scale, sh = ih * scale;
        tx = Math.min(0, Math.max(frameW - sw, tx));
        ty = Math.min(0, Math.max(frameH - sh, ty));
        imgEl.style.width = sw + "px"; imgEl.style.height = sh + "px";
        imgEl.style.transform = `translate(${tx}px, ${ty}px)`;
      };
      apply();
      let dragging = false, lastX = 0, lastY = 0, pinchDist0 = 0, pinchZoom0 = 1;
      const onDown = (e) => {
        if (e.touches && e.touches.length === 2) { pinchDist0 = touchDist(e.touches); pinchZoom0 = zoom; return; }
        dragging = true;
        const p = e.touches ? e.touches[0] : e;
        lastX = p.clientX; lastY = p.clientY;
      };
      const onMove = (e) => {
        if (e.touches && e.touches.length === 2) {
          e.preventDefault();
          const d = touchDist(e.touches);
          zoom = Math.min(maxZoom, Math.max(1, pinchZoom0 * (d / (pinchDist0 || d))));
          zoomEl.value = String(Math.round((zoom - 1) / (maxZoom - 1) * 100));
          apply(); return;
        }
        if (!dragging) return;
        e.preventDefault();
        const p = e.touches ? e.touches[0] : e;
        tx += p.clientX - lastX; ty += p.clientY - lastY;
        lastX = p.clientX; lastY = p.clientY;
        apply();
      };
      const onUp = () => { dragging = false; };
      frame.addEventListener("mousedown", onDown);
      frame.addEventListener("touchstart", onDown, { passive: true });
      document.addEventListener("mousemove", onMove);
      frame.addEventListener("touchmove", onMove, { passive: false });
      document.addEventListener("mouseup", onUp);
      frame.addEventListener("touchend", onUp);
      zoomEl.addEventListener("input", () => {
        zoom = 1 + (Number(zoomEl.value) / 100) * (maxZoom - 1);
        apply();
      });
      const cleanup = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        URL.revokeObjectURL(url);
      };
      $("[data-act2='crop-cancel']").addEventListener("click", () => { cleanup(); closeModal(); });
      $("[data-act2='crop-confirm']").addEventListener("click", () => {
        const scale = baseScale * zoom;
        const sx = -tx / scale, sy = -ty / scale, sw = frameW / scale, sh = frameH / scale;
        const outW = opts.outW || 640, outH = Math.round(outW / ratio);
        const canvas = document.createElement("canvas");
        canvas.width = outW; canvas.height = outH;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, sx, sy, sw, sh, 0, 0, outW, outH);
        let out;
        if (opts.transparent) {
          out = canvas.toDataURL("image/png");
          if (out.length > (opts.maxBytes || 200000)) {   // גדול מדי — רקע לבן + JPEG (כמו compressLogo)
            ctx.globalCompositeOperation = "destination-over";
            ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, outW, outH);
            let q = 0.85; out = canvas.toDataURL("image/jpeg", q);
            while (out.length > (opts.maxBytes || 200000) && q > 0.4) { q -= 0.1; out = canvas.toDataURL("image/jpeg", q); }
          }
        } else {
          let q = opts.quality || 0.75; out = canvas.toDataURL("image/jpeg", q);
          while (out.length > (opts.maxBytes || 900000) && q > 0.4) { q -= 0.1; out = canvas.toDataURL("image/jpeg", q); }
        }
        cleanup(); closeModal();
        opts.onDone(out);
      });
    };
    img.src = url;
  }

  async function handleLogoUpload(file) {
    if (!file || !file.type || file.type.indexOf("image/") !== 0) { toast("נא לבחור קובץ תמונה", "", "🖼️"); return; }
    openCropper(file, {
      ratio: 1, title: "חיתוך לוגו", outW: 480, transparent: true, maxBytes: 200000,
      onDone: async (dataUrl) => {
        toast("מעלה לוגו…", "sky", "⏳");
        try {
          await Store.setShopMedia("logo", dataUrl);
          toast("הלוגו עודכן ✓ — כך יראו אותו הלקוחות", "good", "🎨");
          render();
        } catch (e) { toast("לא הצלחנו לשמור את הלוגו", "", "⚠️"); }
      },
    });
  }

  async function handleCoverUpload(file) {
    if (!file || !file.type || file.type.indexOf("image/") !== 0) { toast("נא לבחור קובץ תמונה", "", "🖼️"); return; }
    openCropper(file, {
      ratio: 16 / 7, title: "חיתוך תמונת נושא", outW: 1280, quality: 0.75, maxBytes: 900000,
      onDone: async (dataUrl) => {
        toast("מעלה תמונת נושא…", "sky", "⏳");
        try {
          await Store.setShopMedia("cover", dataUrl);
          toast("תמונת הנושא עודכנה ✓", "good", "🌄");
          render();
        } catch (e) { toast("לא הצלחנו לשמור את התמונה", "", "⚠️"); }
      },
    });
  }

  /* ---------- קוד QR לקישור הלקוחות (מקומי, ללא רשת) ---------- */
  // qrcode.js נטען עצל (רק כשצריך) כדי לחסוך ~57KB בטעינה הראשונה של כל לקוח.
  let qrLoaded = typeof qrcode !== "undefined";
  function ensureQrCode() {
    if (qrLoaded) return Promise.resolve();
    if (!UG.loadQrCode) return Promise.reject();
    return UG.loadQrCode().then(() => { qrLoaded = true; });
  }
  function qrDataUrl(text, cell, margin) {
    try {
      if (typeof qrcode === "undefined") return "";
      const qr = qrcode(0, "M");   // 0 = בחירת גרסה אוטומטית · M = תיקון שגיאות בינוני
      qr.addData(text || "");
      qr.make();
      return qr.createDataURL(cell || 6, margin == null ? 4 : margin);
    } catch (e) { return ""; }
  }
  async function downloadQr() {
    await ensureQrCode();
    const url = qrDataUrl(clientLink(), 16, 4);
    if (!url) { toast("לא ניתן ליצור קוד QR", "", "⚠️"); return; }
    try {
      const a = document.createElement("a");
      a.href = url; a.download = "barbertor-" + SHOP + "-qr.gif";
      document.body.appendChild(a); a.click(); a.remove();
      toast("קוד ה-QR הורד — אפשר להדפיס ולתלות 📷", "good", "⬇️");
    } catch (e) { toast("ההורדה נכשלה", "", "⚠️"); }
  }
  // כרטיס QR משותף (מוצג בדף הפרסום ובהגדרות).
  // qrcode.js נטען עצל — בפעם הראשונה שהעמוד מוצג יוצג פלייסהולדר, ולאחר שהספרייה נטענה נרנדר מחדש.
  function qrShareCard() {
    const url = qrDataUrl(clientLink(), 6, 4);
    if (!url) {
      ensureQrCode().then(() => { if (!isEditingRoot()) render(); }).catch(() => {});
      return `
      <div class="section-title">📷 קוד QR למספרה</div>
      <div class="card qr-card">
        <div class="qr-img" style="display:grid;place-items:center;background:var(--surface-3);color:var(--muted);font-size:26px">⏳</div>
        <div class="qr-body">
          <div class="hint" style="margin:0">טוען את קוד ה-QR…</div>
        </div>
      </div>`;
    }
    return `
      <div class="section-title">📷 קוד QR למספרה</div>
      <div class="card qr-card">
        <img class="qr-img" src="${url}" alt="קוד QR של המספרה" width="180" height="180">
        <div class="qr-body">
          <div class="hint" style="margin:0 0 10px">הדפיסו ותלו בחנות, או הוסיפו לביו באינסטגרם — הלקוחות סורקים וקובעים תור.</div>
          <button class="btn btn-primary btn-sm" data-act="qr-download">⬇️ הורדת הקוד</button>
        </div>
      </div>`;
  }

  /* ---------- מודאל רשימת המתנה ---------- */
  function openWaitlist(dateKey, start) {
    const st = Store.get();
    const mine = (st.waitlist || []).find((w) =>
      w.userId === identity.userId && w.date === dateKey && w.start === start);
    if (mine) {
      openModal(`
        <div class="m-title">אתם ברשימת ההמתנה 🔔</div>
        <div class="m-sub">לשעה ${esc(start)}, ${esc(u.longDate(dateKey))}</div>
        <p style="font-size:14px;color:var(--muted);margin-bottom:18px">אם התור יתפנה — תקבלו הודעה מיד ותוכלו להזמין אותו לפני כולם.</p>
        <button class="btn btn-danger" data-act="leave-wait" data-id="${mine.id}">יציאה מרשימת ההמתנה</button>
        <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">סגור</button>
      `);
      return;
    }
    openModal(`
      <div class="m-title">השעה תפוסה — רשימת המתנה</div>
      <div class="m-sub">אם התור יתפנה, נודיע לכם מיד ותוכלו לתפוס אותו</div>
      <div class="summary-row"><span class="sr-k">תאריך</span><span class="sr-v">${esc(u.longDate(dateKey))}</span></div>
      <div class="summary-row"><span class="sr-k">שעה</span><span class="sr-v">${esc(start)}</span></div>
      <div style="height:16px"></div>
      <div class="field-row">
        <div class="field"><label>שם פרטי <span class="req">*</span></label>
          <input class="input" id="cf-first" placeholder="שם פרטי" value="${esc(identity.firstName || "")}"></div>
        <div class="field"><label>שם משפחה <span class="req">*</span></label>
          <input class="input" id="cf-last" placeholder="שם משפחה" value="${esc(identity.lastName || "")}"></div>
      </div>
      <div class="field"><label>טלפון נייד <span class="req">*</span></label>
        <input class="input" id="cf-phone" type="tel" inputmode="tel" placeholder="050-0000000" value="${esc(identity.phone)}"></div>
      <button class="btn btn-primary" data-act="join-wait" data-key="${dateKey}|${start}">🔔 הצטרפות לרשימת ההמתנה</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>
      <p class="hint">חשוב: כדי לקבל את ההודעה כשיתפנה תור — אשרו קבלת התראות.</p>
    `);
  }

  async function doJoinWait(key) {
    const contact = readContact();
    if (!contact) return;
    const [dateKey, start] = key.split("|");
    // אם בינתיים השעה התפנתה — הצע להזמין ישר
    const freeNow = gridSlots(dateKey).some((s) => s.start === start && !s.booking && !s.blocked && !s.past && !s.hidden);
    if (freeNow) {
      closeModal();
      view.selDate = dateKey; view.selSlot = start; view.clientTab = "book";
      toast("השעה התפנתה הרגע — אפשר להזמין!", "good", "🎉");
      render(); openConfirm();
      return;
    }
    try {
      await Store.joinWaitlist({
        date: dateKey, start,
        userId: identity.userId, userName: contact.name, phone: contact.phone,
      });
    } catch (e) {
      toast("לא הצלחנו להצטרף לרשימת ההמתנה — נסו שוב.", "", "⚠️");
      return;   // החלון נשאר פתוח כדי לנסות שוב
    }
    closeModal();
    toast("נכנסת לרשימת ההמתנה — נודיע אם יתפנה 🔔", "sky", "✅");
    // ודא הרשאת התראות כדי שההודעה באמת תגיע (גם כשהאפליקציה סגורה)
    if (Notify.supported() && Notify.permission() === "default") {
      const r = await Notify.requestPermission();
      if (r === "granted") toast("התראות הופעלו ✓", "good", "🔔");
    }
    ensureFcm();
    render();
  }

  /* ---------- מודאל דירוג וביקורת ---------- */
  function openReview(bookingId) {
    const st = Store.get();
    const b = st.bookings.find((x) => x.id === bookingId);
    if (!b) return;
    openModal(`
      <div class="m-title">דירוג התספורת ⭐</div>
      <div class="m-sub">${esc(b.serviceName)} · ${esc(u.longDate(b.date))}</div>
      <div class="stars" id="rv-stars">
        ${[1, 2, 3, 4, 5].map((n) => `<button class="star on" data-star="${n}">★</button>`).join("")}
      </div>
      <div class="field" style="margin-top:16px"><label>ביקורת (לא חובה)</label>
        <textarea class="input" id="rv-text" rows="3" placeholder="ספרו לנו איך היה…"></textarea></div>
      <button class="btn btn-primary" data-act="send-review" data-id="${b.id}">שליחת הדירוג</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>
    `);
    let rating = 5;
    const wrap = $("#rv-stars");
    const paint = () => [...wrap.children].forEach((c, i) => c.classList.toggle("on", i < rating));
    wrap.addEventListener("click", (e) => {
      const s = e.target.closest("[data-star]"); if (!s) return;
      rating = Number(s.dataset.star); paint();
    });
    $("#modal").__rating = () => rating;
  }

  /* ---------- התראת מערכת חד-פעמית על "התפנה תור" ---------- */
  function notifyAlerts(st) {
    let seen;
    try { seen = new Set(JSON.parse(localStorage.getItem("ug_alerts_seen") || "[]")); }
    catch (e) { seen = new Set(); }
    let changed = false;
    (st.alerts || [])
      .filter((a) => a.userId === identity.userId && u.dateTime(a.date, a.start).getTime() > Date.now())
      .forEach((a) => {
        if (seen.has(a.id)) return;
        seen.add(a.id); changed = true;
        Notify.show(
          "🎉 התפנה תור!",
          `${u.relativeDay(a.date)} בשעה ${a.start} — היכנסו מהר להזמין לפני שייתפס`,
          { tag: "freed-" + a.id }
        );
        if (view.route === "client") toast(`התפנה תור ${u.relativeDay(a.date)} בשעה ${a.start}!`, "sky", "🎉");
      });
    if (changed) {
      try { localStorage.setItem("ug_alerts_seen", JSON.stringify([...seen].slice(-100))); } catch (e) {}
    }
  }

  /* =======================================================================
     צד בעל העסק
     =======================================================================*/
  function renderOwner() {
    const st = Store.get();
    // מודל חדש: חובה לאבטח את המספרה בחשבון לפני שאפשר לנהל
    if (newAuthShop() && authAvail && !(st.shop && st.shop.ownerUid)) {
      return `
      <div class="screen active">
        ${topbar("אבטחת המספרה", {})}
        <div class="content"><div class="auth-gate">
          <div class="ag-emoji">🔒</div>
          <h2>אבטחו את המספרה שלכם</h2>
          <p>כדי לנהל את המספרה יש להתחבר עם חשבון אישי — כך רק אתם תוכלו להיכנס לניהול. זה חד-פעמי.</p>
          <button class="btn btn-primary" data-act="secure-shop">🔒 אבטחה עם Google או אימייל</button>
          <button class="btn btn-ghost" data-act="owner-logout" style="margin-top:8px">יציאה</button>
        </div></div>
      </div>`;
    }
    const todayKey = u.dateKey(new Date());
    const now = Date.now();
    const todayCount = st.bookings.filter((b) => b.status !== "cancelled" && b.date === todayKey).length;
    // מנוי הסתיים — נועלים את הניהול ומציגים מסך הפעלה
    const locked = subStatus().state === "expired";
    // לשוניות שעברו לתוך "הגדרות" במבנה המסודר (try): נגישות מתפריט הניהול,
    // ומקבלות כפתור "חזרה להגדרות" בראש.
    const SETTINGS_CHILDREN = ["hours", "services", "products", "clients", "report", "publish"];
    let body;
    if (locked) body = paywallBody();
    else if (view.ownerTab === "cal") body = ownerCal(st);
    else if (view.ownerTab === "hours") body = ownerHours(st);
    else if (view.ownerTab === "services") body = ownerServices(st);
    else if (view.ownerTab === "products") body = ownerProducts(st);
    else if (view.ownerTab === "bookings") body = ownerBookings(st);
    else if (view.ownerTab === "clients") body = ownerClients(st);
    else if (view.ownerTab === "report") body = ownerReport(st);
    else if (view.ownerTab === "publish") body = ownerPublish(st);
    else body = ownerSettings(st);
    if (!locked && tidyOwner() && SETTINGS_CHILDREN.includes(view.ownerTab)) {
      body = `<button class="btn btn-ghost btn-sm home-back" data-otab="settings">‹ חזרה להגדרות</button>` + body;
    }

    const upcomingCount = st.bookings.filter((b) =>
      b.status !== "cancelled" && u.dateTime(b.date, b.start).getTime() > now).length;

    const banners = locked ? "" :
      subBanner() + spamBanner() + (view.ownerTab !== "settings" ? ownerNotifBanner() : "");

    const settingsPageLabel = {
      business: "פרטי העסק", booking: "תורים ותזכורות", brand: "מיתוג ועיצוב",
      client: "עמוד הלקוח", alerts: "התראות ואבטחה", tools: "כלים ותחזוקה", account: "חשבון",
    };
    const tabLabel = locked ? "המנוי הסתיים"
      : (tidyOwner() && view.ownerTab === "settings" && view.settingsPage && !view.settingsItem && settingsPageLabel[view.settingsPage])
      || ({
      cal: "יומן", hours: "שעות", services: "שירותים", products: "מוצרים", bookings: "תורים",
      clients: "לקוחות", report: "דוח", publish: "פרסום", settings: "הגדרות",
    }[view.ownerTab] || "ניהול העסק");

    // הגדרת כל לשונית: מפתח, אייקון, תווית. הסדר בהמשך נקבע לפי tidyOwner().
    const TAB_DEF = {
      cal: ["🗓️", "יומן"], hours: ["🕐", "שעות"], services: ["✂️", "שירותים"],
      products: ["🛍️", "מוצרים"], bookings: ["🎟️", "תורים"], clients: ["👥", "לקוחות"],
      report: ["📊", "דוח"], publish: ["📣", "פרסום"], settings: ["⚙️", "הגדרות"],
    };
    // מסודר (try): רק עבודה יומית בסרגל — יומן, תורים, הגדרות. כל השאר בתוך ההגדרות.
    const tabOrder = tidyOwner()
      ? ["cal", "bookings", "settings"]
      : ["cal", "hours", "services", "products", "bookings", "clients", "report", "publish", "settings"];
    const tabBtn = (key) => {
      const [ico, label] = TAB_DEF[key];
      const icoHtml = key === "bookings"
        ? `<span class="tb-ico" style="position:relative">${ico}${upcomingCount ? `<span class="badge-count" style="inset-inline-start:auto;inset-inline-end:-10px;top:-6px">${upcomingCount}</span>` : ""}</span>`
        : `<span class="tb-ico">${ico}</span>`;
      // לשונית "הגדרות" פעילה גם כשגולשים בעמוד-בן שלה (שעות/שירותים/דוח וכו')
      const active = view.ownerTab === key ||
        (key === "settings" && tidyOwner() && SETTINGS_CHILDREN.includes(view.ownerTab));
      return `<button data-otab="${key}" class="${active ? "active" : ""}">${icoHtml}${label}</button>`;
    };

    return `
    <div class="screen active">
      ${topbar(tabLabel, {})}
      <div class="content" id="oscroll">${banners}${body}</div>
      ${locked ? "" : (tidyOwner()
        ? `<div class="tabbar" id="otabbar">${tabOrder.map(tabBtn).join("")}</div>`
        : `
      <div class="otabbar-wrap">
        <button class="tab-arrow tab-arrow-start" data-tabscroll="start" aria-label="עוד לשונית">›</button>
        <div class="tabbar scroll" id="otabbar">
          ${tabOrder.map(tabBtn).join("")}
        </div>
        <button class="tab-arrow tab-arrow-end" data-tabscroll="end" aria-label="עוד לשונית">‹</button>
      </div>`)}
    </div>`;
  }

  // כל שעות היממה (00:00–24:00) — כדי לאפשר שעות פעילות גם בלילה ומוקדם בבוקר
  function timeOptions(selected) {
    let html = "";
    for (let m = 0; m <= 24 * 60; m += 15) {
      const v = u.toHHMM(m % (24 * 60) === 0 && m !== 0 ? 24 * 60 - 0 : m);
      const label = m === 24 * 60 ? "24:00" : v;
      const val = m === 24 * 60 ? "23:59" : v;
      html += `<option value="${val}" ${val === selected ? "selected" : ""}>${label}</option>`;
    }
    return html;
  }

  // תצוגת יומן יומית — כל שעות היום (06:00–24:00) עם מתג לכל שעה.
  // הספר יכול לפתוח כל שעה שירצה, גם מחוץ לשעות הפעילות הקבועות.
  function ownerCal(st) {
    const days = nextDays(14);
    if (!view.oDate || !days.includes(view.oDate)) {
      view.oDate = days.find((k) => st.schedule[u.parseKey(k).getDay()].active) || days[0];
    }
    const dayChips = days.map((k) => {
      const d = u.parseKey(k);
      const off = !st.schedule[d.getDay()].active;
      const hasExtra = openedFor(st, k).length > 0;
      return `
      <button class="day-chip ${view.oDate === k ? "selected" : ""} ${off && !hasExtra ? "off" : ""}"
              data-oday="${k}">
        <div class="dc-dow">${off ? "סגור" : u.DOW_SHORT[d.getDay()]}${hasExtra ? " ⏰" : ""}</div>
        <div class="dc-num">${d.getDate()}</div>
        <div class="dc-mon">${u.MON[d.getMonth()]}</div>
      </button>`;
    }).join("");

    const slots = ownerDayGrid(view.oDate).filter((s) => !s.past);
    const body = `<div class="card" style="padding:6px 14px">` + slots.map((s) => {
      if (s.booking) {
        return `
        <div class="slot-line booked${s.booking.spam ? " spam-slot" : ""}">
          <span class="sl-time">${s.start}</span>
          <div class="sl-mid">
            <span class="sl-name">${esc(bkName(s.booking) || "לקוח")}${s.booking.spam ? " 🛡️" : ""}</span>
            <span class="sl-sub">${esc(s.booking.serviceName)}</span>
          </div>
          <span class="status-tag status-booked">תפוס</span>
        </div>`;
      }
      const state = s.signupClosed
        ? "נסגר להרשמה"
        : s.available
        ? (s.inHours ? "פנוי" : "פתוח")
        : (s.inHours ? "לא פנוי" : "מחוץ לשעות");
      return `
      <div class="slot-line ${s.available ? "" : "off"} ${s.inHours ? "" : "extra"}">
        <span class="sl-time">${s.start}</span>
        <div class="sl-mid"><span class="sl-state ${s.signupClosed ? "closed" : s.available ? "free" : "blocked"}">${state}${(s.available && !s.inHours && !s.signupClosed) ? " ⏰" : ""}</span></div>
        <label class="switch">
          <input type="checkbox" data-slot-open="${view.oDate}|${s.start}" data-inhours="${s.inHours ? "1" : "0"}" ${s.available ? "checked" : ""}>
          <span class="track"></span><span class="thumb"></span>
        </label>
      </div>`;
    }).join("") + `</div>`;

    return `
      <div class="section-title">בחירת יום</div>
      <div class="days-scroll">${dayChips}</div>
      <div class="section-title">${esc(u.longDate(view.oDate))} · סימון זמינות</div>
      ${body}
      <p class="hint">כל שעות היממה (00:00–24:00) מוצגות כאן — אפשר לפתוח תורים גם בלילה ומוקדם בבוקר. הדליקו מתג כדי לפתוח שעה ללקוחות — <b>גם מחוץ לשעות הפעילות הרגילות</b> (מסומן ⏰). כבו מתג כדי לסמן שעה כלא-פנויה. שעה שכבר נקבעה מסומנת ״תפוס״.</p>
    `;
  }

  // לשונית ״שעות״ — ימי הפעילות ושעות העבודה השבועיות
  function ownerHours(st) {
    const rows = [];
    for (let i = 0; i < 7; i++) {
      const d = st.schedule[i];
      rows.push(`
      <div class="day-row ${d.active ? "" : "off"}" data-day="${i}">
        <div class="dname">${u.DOW[i]}</div>
        <div class="dtimes">
          ${d.active ? `
            <select class="time-sel" data-time="open" data-day="${i}">${timeOptions(d.open)}</select>
            <span class="sep">עד</span>
            <select class="time-sel" data-time="close" data-day="${i}">${timeOptions(d.close)}</select>
          ` : `<span class="closed-tag">סגור</span>`}
        </div>
        <label class="switch">
          <input type="checkbox" data-active="${i}" ${d.active ? "checked" : ""}>
          <span class="track"></span><span class="thumb"></span>
        </label>
      </div>`);
    }
    const todayKey = u.dateKey(new Date());
    const upcomingClosed = (st.closedDates || []).filter((k) => k >= todayKey).sort();
    const closedList = upcomingClosed.length ? `
      <div class="hint" style="margin:14px 0 8px;font-weight:700">תאריכים חסומים:</div>
      <div class="vac-list">${upcomingClosed.map((k) => `
        <span class="vac-chip">${esc(u.longDate(k))}<button data-act="del-vacation" data-key="${k}" aria-label="הסר">✕</button></span>`).join("")}</div>` : "";
    const vacationsCard = `
      <div class="section-title">🌴 חופשות וסגירת תאריכים</div>
      <div class="card">
        <p class="hint" style="margin-top:0;margin-bottom:11px">חסמו יום בודד או טווח (חופשה) — הלקוחות לא יוכלו להזמין בתאריכים אלה.</p>
        <div class="field-row">
          <div class="field"><label>מתאריך</label><input class="input" id="vac-from" type="date" value="${todayKey}"></div>
          <div class="field"><label>עד תאריך</label><input class="input" id="vac-to" type="date" value="${todayKey}"></div>
        </div>
        <button class="btn btn-primary btn-sm" data-act="add-vacation" style="width:100%">חסימת התאריכים</button>
        ${closedList}
      </div>`;

    // מבנה מסודר: שורה לכל יום → לחיצה פותחת את עריכת אותו יום
    if (tidyOwner()) {
      if (view.subPage === "vac") return subBack("חזרה לשעות") + vacationsCard;
      const dayIdx = /^day([0-6])$/.exec(view.subPage || "");
      if (dayIdx) {
        const i = Number(dayIdx[1]); const d = st.schedule[i];
        return subBack("חזרה לשעות") + `
          <div class="section-title">🕐 יום ${esc(u.DOW[i])}</div>
          <div class="card">
            <label class="ab-custom" style="margin-top:0">
              <input type="checkbox" data-active="${i}" ${d.active ? "checked" : ""}>
              <span>פתוח ביום ${esc(u.DOW[i])}</span>
            </label>
            ${d.active ? `
            <div class="field-row" style="margin-top:14px">
              <div class="field"><label>שעת פתיחה</label>
                <select class="input" data-time="open" data-day="${i}">${timeOptions(d.open)}</select></div>
              <div class="field"><label>שעת סגירה</label>
                <select class="input" data-time="close" data-day="${i}">${timeOptions(d.close)}</select></div>
            </div>` : `<p class="hint" style="margin:14px 0 0">היום סגור. הדליקו את המתג כדי לקבוע שעות עבודה.</p>`}
            <p class="hint" style="margin:14px 0 0">כל שינוי נשמר מיד ומתעדכן אצל הלקוחות בזמן אמת.</p>
          </div>`;
      }
      view.subPage = null;
      const dayColors = ["#0ea5e9", "#22c55e", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6", "#64748b"];
      return `
        <div class="section-title">🕐 ימי הפעילות</div>
        <div class="card set-list">
          ${st.schedule.map((d, i) => setRow({
            nav: `data-act="sub-page" data-sub="day${i}"`, ico: "📅", color: dayColors[i],
            label: "יום " + u.DOW[i], val: d.active ? d.open + "–" + d.close : "סגור",
            ltr: !!d.active,   // טווח שעות נכתב משמאל לימין, אחרת העברית הופכת אותו
          })).join("")}
        </div>
        <div class="card set-list">
          ${setRow({ nav: `data-act="sub-page" data-sub="vac"`, ico: "🌴", color: "#f97316",
            label: "חופשות וסגירת תאריכים", sub: "חסימת ימים שבהם לא עובדים",
            val: upcomingClosed.length ? upcomingClosed.length + " תאריכים" : "אין" })}
        </div>
        <p class="hint">שעות העבודה קובעות אילו שעות מוצגות ללקוחות ובלשונית ״יומן״.</p>`;
    }

    return `
      <div class="section-title">ימי הפעילות ושעות העבודה</div>
      <div class="card">${rows.join("")}</div>
      <p class="hint">כל שינוי נשמר מיד ומתעדכן אצל הלקוחות בזמן אמת. שעות העבודה קובעות אילו שעות מוצגות בלשונית ״יומן״.</p>
      ${vacationsCard}
    `;
  }

  function ownerServices(st) {
    const items = st.services.map((s) => `
      <div class="card">
        <div class="service-item">
          <div class="svc-ico" style="width:44px;height:44px;border-radius:12px;display:grid;place-items:center;background:var(--surface-3);font-size:20px">${esc(s.icon != null ? s.icon : "✂️")}</div>
          <div class="si-main">
            <div class="si-name">${esc(s.name)}</div>
            <div class="si-meta"><span class="chip-price">${u.fmtPrice(s.price)}</span><span class="pill">⏱ ${u.fmtDuration(s.durationMin)}</span></div>
          </div>
          <button class="icon-btn" data-act="edit-svc" data-id="${s.id}">✏️</button>
        </div>
      </div>`).join("");
    if (tidyOwner()) {
      return `
        <div class="section-title">✂️ השירותים שאתה מציע</div>
        ${st.services.length ? `<div class="card set-list">${st.services.map((s) => setRow({
          nav: `data-act="edit-svc" data-id="${s.id}"`, ico: esc(s.icon != null ? s.icon : "✂️"), color: "#0ea5e9",
          label: s.name, sub: u.fmtDuration(s.durationMin), val: u.fmtPrice(s.price),
        })).join("")}</div>` : emptyState("✂️", "אין שירותים", "הוסיפו את השירות הראשון")}
        <div style="height:14px"></div>
        <button class="btn btn-primary" data-act="add-svc">＋ הוספת שירות</button>
        <p class="hint">שם השירות, המחיר והמשך מתעדכנים אצל כל הלקוחות מיד.</p>`;
    }
    return `
      <div class="section-title">השירותים שאתה מציע</div>
      ${items || emptyState("✂️", "אין שירותים", "הוסיפו את השירות הראשון")}
      <div style="height:14px"></div>
      <button class="btn btn-primary" data-act="add-svc">＋ הוספת שירות</button>
      <p class="hint">שם השירות, המחיר והמשך מתעדכנים אצל כל הלקוחות מיד.</p>
    `;
  }

  /* ---------- מוצרים למכירה (צד הבעלים) ---------- */
  function ownerProducts(st) {
    const waOk = !!waIntl(st.shop.phone || "");
    const products = (st.products || []).slice().sort((a, z) => (z.createdAt || 0) - (a.createdAt || 0));
    const items = products.map((p) => `
      <div class="card">
        <div class="prod-row">
          <div class="prod-thumb">${p.image ? `<img src="${esc(p.image)}" alt="">` : "🛍️"}</div>
          <div class="prod-main">
            <div class="prod-name">${esc(p.name)}</div>
            <div class="prod-price">${u.fmtPrice(p.price)}</div>
            ${p.description ? `<div class="prod-desc-mini">${esc(p.description)}</div>` : ""}
          </div>
          <button class="icon-btn" data-act="edit-product" data-id="${p.id}">✏️</button>
        </div>
      </div>`).join("");
    const noPhoneNote = !waOk ? `<div class="card notice-card">📱 כדי להוסיף מוצרים צריך קודם להזין מספר טלפון נייד ב<b>הגדרות ← טלפון</b> — דרכו הלקוחות פונים בוואטסאפ.</div>` : "";
    if (tidyOwner()) {
      return `
        ${noPhoneNote}
        <div class="section-title">🛍️ המוצרים שאתה מוכר</div>
        ${products.length ? `<div class="card set-list">${products.map((p) => setRow({
          nav: `data-act="edit-product" data-id="${p.id}"`, ico: "🛍️", img: p.image ? esc(p.image) : "",
          color: "#22c55e", label: p.name, sub: p.description || "", val: u.fmtPrice(p.price),
        })).join("")}</div>` : emptyState("🛍️", "אין עדיין מוצרים", "הוסיפו מוצר ראשון — קרם, שעווה, שמפו…")}
        <div style="height:14px"></div>
        <button class="btn btn-primary" data-act="add-product" ${waOk ? "" : 'disabled style="opacity:.5"'}>＋ הוספת מוצר</button>
        <p class="hint">הלקוחות רואים את המוצרים בעמוד ״מוצרים״, ויכולים לפנות אליך ישירות בוואטסאפ להזמנה.</p>`;
    }
    return `
      ${noPhoneNote}
      <div class="section-title">המוצרים שאתה מוכר</div>
      ${items || emptyState("🛍️", "אין עדיין מוצרים", "הוסיפו מוצר ראשון — קרם, שעווה, שמפו…")}
      <div style="height:14px"></div>
      <button class="btn btn-primary" data-act="add-product" ${waOk ? "" : 'disabled style="opacity:.5"'}>＋ הוספת מוצר</button>
      <p class="hint">הלקוחות רואים את המוצרים בעמוד ״מוצרים״, ויכולים לפנות אליך ישירות בוואטסאפ להזמנה.</p>
    `;
  }

  function productModal(existing) {
    const p = existing || { name: "", price: "", description: "", image: "" };
    openModal(`
      <div class="m-title">${existing ? "עריכת מוצר" : "מוצר חדש"}</div>
      <div class="m-sub">הפרטים יופיעו אצל הלקוחות בעמוד ״מוצרים״</div>
      <div class="prod-img-pick">
        <div class="prod-img-prev${p.image ? " has-img" : ""}" id="pm-prev">${p.image ? `<img src="${esc(p.image)}" alt="">` : "🛍️"}</div>
        <div class="btn-row" style="margin-top:10px;justify-content:center">
          <button type="button" class="btn btn-sm" data-act="product-pic">${p.image ? "החלפת תמונה" : "העלאת תמונה"}</button>
          <button type="button" class="btn btn-danger btn-sm" id="pm-clear" data-act="product-pic-clear" style="${p.image ? "" : "display:none"}">הסרה</button>
        </div>
        <input type="file" accept="image/*" data-productfile style="display:none">
      </div>
      <div class="field" style="margin-top:12px"><label>שם המוצר</label>
        <input class="input" id="pm-name" placeholder="לדוגמה: שעוות עיצוב לשיער" value="${esc(p.name)}"></div>
      <div class="field"><label>מחיר (₪)</label>
        <input class="input" id="pm-price" type="number" inputmode="numeric" min="0" placeholder="50" value="${esc(p.price)}"></div>
      <div class="field"><label>תיאור <span class="opt">(לא חובה)</span></label>
        <textarea class="input" id="pm-desc" rows="3" placeholder="ספרו על המוצר — למה הוא טוב, למי הוא מתאים…" style="resize:vertical;line-height:1.6">${esc(p.description)}</textarea></div>
      <button class="btn btn-primary" data-act="save-product" data-id="${existing ? existing.id : ""}">שמירה</button>
      ${existing ? `<button class="btn btn-danger" data-act="del-product" data-id="${existing.id}" style="margin-top:8px">מחיקת מוצר</button>` : `<button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>`}
    `);
    $("#modal").__prodImg = p.image || "";
  }

  async function saveProduct(id) {
    const name = ($("#pm-name") && $("#pm-name").value.trim()) || "";
    const price = Number($("#pm-price") && $("#pm-price").value);
    const description = ($("#pm-desc") && $("#pm-desc").value.trim()) || "";
    const image = ($("#modal") && $("#modal").__prodImg) || "";
    if (!name) { toast("נא להזין שם מוצר", "", "✋"); return; }
    if (!(price >= 0)) { toast("בדקו את המחיר", "", "✋"); return; }
    await Store.upsertProduct({ id: id || undefined, name, price, description, image });
    closeModal(); toast("המוצר נשמר ✓", "good", "🛍️"); render();
  }

  function svcModal(existing) {
    const s = existing || { name: "", price: "", durationMin: 30, icon: "✂️" };
    // v = הערך שנשמר, g = מה שמוצג בכפתור. "ללא" שומר מחרוזת ריקה (בלי אייקון).
    const iconOpts = [
      { v: "", g: "ללא", txt: true },
      { v: "✂️", g: "✂️" }, { v: "🧔", g: "🧔" }, { v: "💈", g: "💈" }, { v: "🪒", g: "🪒" },
      { v: "👦", g: "👦" }, { v: "💇‍♂️", g: "💇‍♂️" }, { v: "💇‍♀️", g: "💇‍♀️" }, { v: "✨", g: "✨" },
      { v: "🎨", g: "🎨" }, { v: "💨", g: "💨" }, { v: "🔥", g: "🔥" }, { v: "💆‍♀️", g: "💆‍♀️" },
      { v: "💅", g: "💅" }, { v: "🌀", g: "🌀" },
    ];
    const icBtnStyle = (txt, sel) => `width:46px;font-size:${txt ? "12px" : "20px"};${sel ? "border-color:var(--sky);box-shadow:0 0 0 2px var(--sky-glow)" : ""}`;
    openModal(`
      <div class="m-title">${existing ? "עריכת שירות" : "שירות חדש"}</div>
      <div class="m-sub">הפרטים יופיעו אצל הלקוחות</div>
      <div class="field"><label>סוג התספורת / השירות</label>
        <input class="input" id="sv-name" placeholder="לדוגמה: תספורת גבר, צביעת שיער, פן" value="${esc(s.name)}"></div>
      <div class="field-row">
        <div class="field"><label>מחיר (₪)</label>
          <input class="input" id="sv-price" type="number" inputmode="numeric" min="0" placeholder="60" value="${esc(s.price)}"></div>
        <div class="field"><label>משך (דקות)</label>
          <input class="input" id="sv-dur" type="number" inputmode="numeric" min="5" step="5" placeholder="30" value="${esc(s.durationMin)}"></div>
      </div>
      <div class="field"><label>אייקון <span class="opt">(לא חובה)</span></label>
        <div style="display:flex;gap:8px;flex-wrap:wrap" id="sv-icons">
          ${iconOpts.map((o) => `<button class="btn btn-sm" data-ic="${o.v}" style="${icBtnStyle(o.txt, o.v === (s.icon || ""))}">${o.g}</button>`).join("")}
        </div>
      </div>
      <button class="btn btn-primary" data-act="save-svc" data-id="${existing ? existing.id : ""}">שמירה</button>
      ${existing ? `<button class="btn btn-danger" data-act="del-svc" data-id="${existing.id}" style="margin-top:8px">מחיקת שירות</button>` : `<button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>`}
    `);
    // בחירת אייקון
    let chosen = s.icon || "";
    $("#sv-icons").addEventListener("click", (e) => {
      const b = e.target.closest("[data-ic]"); if (!b) return;
      chosen = b.dataset.ic;
      [...$("#sv-icons").children].forEach((c) => (c.style.cssText = icBtnStyle(c.dataset.ic === "", false)));
      b.style.cssText = icBtnStyle(b.dataset.ic === "", true);
    });
    $("#modal").__icon = () => chosen;
  }

  function ownerBookings(st) {
    const now = Date.now();
    const list = st.bookings
      .filter((b) => b.status !== "cancelled")
      .map((b) => ({ b, ts: u.dateTime(b.date, b.start).getTime() }))
      .sort((a, z) => a.ts - z.ts);
    const upcoming = list.filter((x) => x.ts > now - 30 * 60000);
    const past = list.filter((x) => x.ts <= now - 30 * 60000).reverse();

    const addBtn = `<button class="btn btn-primary" data-act="add-booking" style="margin-bottom:14px">＋ הוספת תור ידני</button>`;
    if (!list.length) return addBtn + emptyState("🎟️", "אין תורים עדיין", "כשלקוח יקבע תור הוא יופיע כאן — או הוסיפו תור ידני");

    const row = (x, isPast) => {
      const b = x.b;
      // בתצוגת ההיסטוריה מצב ההגעה מוצג ע״י כפתור «הגיע» עצמו (לבן→שחור), בלי תג כפול
      const stg = b.status === "noshow"
        ? `<span class="status-tag status-noshow">❌ לא הגיע</span>`
        : (b.status === "confirmed" && isPast)
        ? ``
        : b.status === "confirmed"
        ? `<span class="status-tag status-confirmed">✓ אישר הגעה</span>`
        : `<span class="status-tag status-booked">ממתין</span>`;
      const arrived = b.status === "confirmed";
      const action = !isPast
        ? `<button class="btn btn-sm btn-danger" data-act="owner-cancel" data-id="${b.id}">בטל</button>`
        : (b.status === "noshow"
            ? `<button class="btn btn-sm" data-act="owner-unnoshow" data-id="${b.id}">בטל סימון</button>`
            : `<div class="bk-acts">
                 <button class="arrive-btn ${arrived ? "done" : ""}" data-act="owner-confirm" data-id="${b.id}">${arrived ? "✓ הגיע" : "סמן הגעה"}</button>
                 ${arrived ? "" : `<button class="btn btn-sm" data-act="owner-noshow" data-id="${b.id}">לא הגיע</button>`}
               </div>`);
      return `
      <div class="booking" style="${isPast ? "opacity:.6" : ""}">
        <div class="bk-time">
          <div class="bt-h">${esc(b.start)}</div>
          <div class="bt-d">${esc(u.relativeDay(b.date))}</div>
        </div>
        <div class="bk-body">
          <div class="bk-title">${esc(bkName(b) || "לקוח")}</div>
          <div class="bk-sub">${esc(b.serviceName)} · ${bkPhone(b) ? `<a href="tel:${esc(bkPhone(b))}">${esc(bkPhone(b))}</a>` : "ללא טלפון"}</div>
          <div class="bk-sub">${esc(u.longDate(b.date))}${b.staff ? ` · <span class="staff-req">🧑‍🔧 ביקש: ${esc(b.staff)}</span>` : ""}</div>
          ${b.priorNoShow ? `<div class="noshow-warn">⚠️ הלקוח לא הגיע בעבר${b.priorNoShow > 1 ? ` (${b.priorNoShow} פעמים)` : ""}</div>` : ""}
          ${b.spam ? `<div class="spam-warn">🛡️ ${b.spam.reason === "multi" ? "ללקוח " + b.spam.count + " תורים פעילים — כדאי לוודא שזה לגיטימי" : b.spam.reason === "burst" ? b.spam.count + " הזמנות ברצף קצר מאותו לקוח" : "הוזמנו " + b.spam.count + " תורים בזמן קצר"}</div>` : ""}
        </div>
        <div style="display:flex;flex-direction:column;gap:8px;align-items:flex-end">
          ${stg}
          ${action}
        </div>
      </div>`;
    };
    let html = addBtn;
    if (upcoming.length) html += `<div class="section-title">תורים קרובים (${upcoming.length})</div>` + upcoming.map((x) => row(x, false)).join("");
    if (past.length) html += `<div class="section-title">היסטוריה</div>` + past.map((x) => row(x, true)).join("");
    return html;
  }

  /* ---------- הוספת תור ידנית ע״י הבעלים ----------
     מציג בדיוק את בורר הימים והשעות שהלקוח רואה, כדי שקל יהיה לראות מה פנוי.
     בנוסף יש «שעה אחרת» כדי לקבוע גם מחוץ לשעות הפעילות. */
  let addBk = null;

  function ownerAddBooking() {
    const st = Store.get();
    if (!st.services.length) { toast("צריך להגדיר שירות אחד לפחות", "", "✂️"); return; }
    const now = new Date();
    addBk = {
      date: u.dateKey(now), start: "", custom: false,
      hour: String(now.getHours()).padStart(2, "0"), min: "00",
      svcId: st.services[0].id, staff: "", name: "", phone: "", email: "", contactId: "",
    };
    openModal(addBookingHtml());
    wireAddBooking();
    setTimeout(() => $("#ab-name") && $("#ab-name").focus(), 100);
  }

  // שמירת מה שהוקלד לפני ציור מחדש של המודאל
  function captureAddBooking() {
    if (!addBk) return;
    const g = (id) => { const e = $(id); return e ? e.value : undefined; };
    const map = { svcId: "#ab-svc", staff: "#ab-staff", name: "#ab-name", phone: "#ab-phone",
                  email: "#ab-email", contactId: "#ab-contact", hour: "#ab-hour", min: "#ab-min",
                  date: "#ab-date" };
    Object.keys(map).forEach((k) => { const v = g(map[k]); if (v !== undefined) addBk[k] = v; });
  }

  function refreshAddBooking() {
    captureAddBooking();
    // שומר את מיקום הגלילה של סרגל הימים — אחרת בחירת יום גוללת בחזרה להתחלה
    const prevDays = $("#modal .days-scroll");
    const daysScroll = prevDays ? prevDays.scrollLeft : null;
    $("#modal").innerHTML = `<div class="m-handle"></div>` + addBookingHtml();
    const newDays = $("#modal .days-scroll");
    if (newDays && daysScroll !== null) newDays.scrollLeft = daysScroll;
    wireAddBooking();
  }

  function addBookingHtml() {
    const st = Store.get();
    const a = addBk;
    const closed = new Set(st.closedDates || []);
    const days = nextDays(14);
    // ב«שעה אחרת» מותר גם תאריך רחוק מהיומן — אין להחזיר אותו לטווח הצ׳יפים
    if (!a.custom && !days.includes(a.date)) a.date = days[0];

    // בורר ימים — לבעלים כל הימים לחיצים, גם סגור/חופשה
    const dayChips = days.map((k) => {
      const d = u.parseKey(k);
      const vac = closed.has(k);
      const off = !st.schedule[d.getDay()].active || vac;
      return `
      <button class="day-chip ${a.date === k ? "selected" : ""} ${off ? "off" : ""}" data-abday="${k}">
        <div class="dc-dow">${vac ? "חופשה" : off ? "סגור" : u.DOW_SHORT[d.getDay()]}</div>
        <div class="dc-num">${d.getDate()}</div>
        <div class="dc-mon">${u.MON[d.getMonth()]}</div>
      </button>`;
    }).join("");

    // שעות — כמו אצל הלקוח, אבל התפוסות מוצגות חסומות ומשבצות שעברו עדיין נבחרות
    // (כדי לתעד לקוח מזדמן שכבר היה)
    const slots = gridSlots(a.date);
    let slotsHtml;
    if (!slots.length) {
      slotsHtml = `<p class="hint" style="margin:2px 0 0">אין שעות פעילות ביום זה — סמנו «שעה אחרת» כדי לקבוע בכל זאת.</p>`;
    } else {
      slotsHtml = `<div class="slots-grid">` + slots.map((s) => {
        if (s.booking) {
          return `<button class="slot taken" disabled>${s.start}<span class="slot-tag">${esc(bkName(s.booking) || "תפוס")}</span></button>`;
        }
        const tag = s.blocked ? `<span class="slot-tag show">חסום</span>` : (s.past ? `<span class="slot-tag show">עבר</span>` : "");
        return `<button class="slot ${(!a.custom && a.start === s.start) ? "selected" : ""} ${s.past ? "slot-past" : ""}" data-abslot="${s.start}">${s.start}${tag}</button>`;
      }).join("") + `</div>`;
    }

    const svcOptions = st.services.map((s) =>
      `<option value="${s.id}" ${a.svcId === s.id ? "selected" : ""}>${esc(s.name)} · ${u.fmtDuration(s.durationMin)}</option>`).join("");
    const hourOptions = Array.from({ length: 24 }, (_, h) => { const v = String(h).padStart(2, "0"); return `<option value="${v}" ${a.hour === v ? "selected" : ""}>${v}</option>`; }).join("");
    const minOptions = ["00", "15", "30", "45"].map((m) => `<option value="${m}" ${a.min === m ? "selected" : ""}>${m}</option>`).join("");
    const contacts = (Store.getContacts()).slice().sort((x, y) => (x.name || "").localeCompare(y.name || "", "he"));
    const staff = st.shop.staff || [];
    const chosen = a.custom ? (a.hour + ":" + a.min) : a.start;

    return `
      <div class="m-title">הוספת תור ידני</div>
      <div class="m-sub">${chosen
        ? `נבחר: <b>${esc(chosen)}</b> · ${esc(u.relativeDay(a.date))}`
        : "בחרו יום ושעה מהיומן"}</div>

      <div class="field"><label>שירות</label>
        <select class="input" id="ab-svc">${svcOptions}</select></div>
      ${staff.length ? `
      <div class="field"><label>ספר</label>
        <select class="input" id="ab-staff">
          <option value="">— ללא —</option>
          ${staff.map((n) => `<option value="${esc(n)}" ${a.staff === n ? "selected" : ""}>${esc(n)}</option>`).join("")}
        </select></div>` : ""}

      <label class="fld-lbl">יום</label>
      <div class="days-scroll">${dayChips}</div>
      <label class="fld-lbl" style="margin-top:12px">שעה</label>
      ${slotsHtml}

      <label class="ab-custom">
        <input type="checkbox" id="ab-custom" ${a.custom ? "checked" : ""}>
        <span>שעה אחרת — גם מחוץ לשעות הפעילות</span>
      </label>
      ${a.custom ? `
      <div class="field"><label>תאריך</label>
        <input class="input" id="ab-date" type="date" value="${esc(a.date)}"></div>
      <div style="display:flex;gap:8px;align-items:center;direction:ltr;justify-content:flex-start;margin-bottom:4px">
        <select class="input" id="ab-hour" style="flex:1">${hourOptions}</select>
        <span style="font-weight:800">:</span>
        <select class="input" id="ab-min" style="flex:1">${minOptions}</select>
      </div>` : ""}

      ${contacts.length ? `
      <div class="field" style="margin-top:12px"><label>בחירת לקוח קיים (לא חובה)</label>
        <select class="input" id="ab-contact">
          <option value="">— לקוח חדש —</option>
          ${contacts.map((c) => `<option value="${esc(c.id)}" data-name="${esc(c.name)}" data-phone="${esc(c.phone)}" ${a.contactId === c.id ? "selected" : ""}>${esc(c.name)}${c.phone ? " · " + esc(c.phone) : ""}</option>`).join("")}
        </select></div>` : ""}
      <div class="field"><label>שם הלקוח</label>
        <input class="input" id="ab-name" placeholder="שם הלקוח" value="${esc(a.name)}"></div>
      <div class="field"><label>טלפון</label>
        <input class="input" id="ab-phone" type="tel" inputmode="tel" placeholder="050-0000000" value="${esc(a.phone)}"></div>
      ${(UG.Email && UG.Email.configured()) ? `
      <div class="field"><label>אימייל לאישור (לא חובה)</label>
        <input class="input" id="ab-email" type="email" inputmode="email" placeholder="name@email.com" value="${esc(a.email)}"></div>` : ""}
      <button class="btn btn-primary" data-act="save-add-booking">קביעת התור</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>
    `;
  }

  function wireAddBooking() {
    const cs = $("#ab-contact");
    if (cs) cs.addEventListener("change", () => {
      const opt = cs.options[cs.selectedIndex];
      if (opt && opt.value) {
        if ($("#ab-name")) $("#ab-name").value = opt.dataset.name || "";
        if ($("#ab-phone")) $("#ab-phone").value = opt.dataset.phone || "";
      }
    });
    const cb = $("#ab-custom");
    if (cb) cb.addEventListener("change", () => {
      captureAddBooking();
      addBk.custom = cb.checked;
      refreshAddBooking();
    });
  }

  async function saveAddBooking() {
    captureAddBooking();
    const a = addBk;
    if (!a) return;
    const start = a.custom ? (a.hour + ":" + a.min) : a.start;
    const name = String(a.name || "").trim();
    const phoneRaw = String(a.phone || "").trim();
    if (!a.svcId) { toast("בחרו שירות", "", "✋"); return; }
    if (!start) { toast("בחרו שעה מהיומן", "", "🕐"); return; }
    if (!name) { toast("הזינו שם לקוח", "", "✋"); return; }
    // טלפון חובה — בלעדיו אי אפשר לשלוח ללקוח הודעות בוואטסאפ
    if (!u.isValidPhone(phoneRaw)) { toast("נא להזין מספר טלפון תקין", "", "📵"); return; }
    const phone = u.fmtPhone(phoneRaw);
    const email = String(a.email || "").trim();
    if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { toast("כתובת אימייל לא תקינה", "", "📧"); return; }
    const res = await Store.createBooking({
      serviceId: a.svcId, date: a.date, start,
      userId: "owner:" + u.normalizePhone(phone),
      userName: name, phone, email, staff: a.staff || "",
    });
    if (!res.ok) { toast(res.reason || "לא ניתן לקבוע את התור", "", "⚠️"); return; }
    // תור שכבר עבר (לקוח מזדמן שהיה עכשיו) — נספר מיד בהכנסות ובביקורים
    const past = u.dateTime(a.date, start).getTime() <= Date.now();
    if (past) await Store.setBookingStatus(res.booking.id, "confirmed", "owner");
    closeModal();
    toast(past ? "התור נוסף ונספר בהכנסות ✓" : "התור נוסף ✓", "good", "➕");
    // מייל אישור ללקוח — משלימים שם/מייל מקומית (במספרה מאובטחת הם אינם בתור הציבורי)
    sendBookingEmail(Object.assign({}, res.booking, { userName: name, email: email }));
    addBk = null;
    render();
  }

  /* ---------- רשימת לקוחות (CRM) ---------- */
  function clientKey(b) { const ph = bkPhone(b); return (ph && u.normalizePhone(ph)) || b.userId || bkName(b) || "לקוח"; }

  /* ---------- חסימת לקוח בעייתי ---------- */
  function blockedList() { return (Store.get() || {}).blockedClients || []; }
  function isClientBlocked(c) {
    const p = c.phone ? u.normalizePhone(c.phone) : "";
    return blockedList().some((b) => (p && b.phone === p) || (b.userId && b.userId === c.key));
  }
  function blockKeyOf(c) {
    const p = c.phone ? u.normalizePhone(c.phone) : "";
    const hit = blockedList().find((b) => (p && b.phone === p) || (b.userId && b.userId === c.key));
    return hit ? hit.id : "";
  }
  // מאתר את פרטי הלקוח לפי המפתח שלו ברשימה (טלפון מנורמל או userId)
  function findClientByKey(key) {
    const st = Store.get();
    const b = (st.bookings || []).filter((x) => x.status !== "cancelled")
      .reverse().find((x) => clientKey(x) === key);
    if (b) return { name: bkName(b) || "לקוח", phone: bkPhone(b) || "", userId: b.userId || "" };
    const c = (Store.getContacts()).find((x) => ((x.phone && u.normalizePhone(x.phone)) || x.name) === key);
    return c ? { name: c.name || "לקוח", phone: c.phone || "", userId: "" } : null;
  }

  function openBlockClient(key) {
    const c = findClientByKey(key);
    if (!c) { toast("לא נמצא לקוח", "", "✋"); return; }
    blockTarget = c;
    openModal(`
      <div class="m-title">🚫 חסימת לקוח</div>
      <div class="m-sub">${esc(c.name)}${c.phone ? " · " + esc(u.fmtPhone(c.phone)) : ""}</div>
      <p class="hint" style="margin:12px 0 4px">לקוח חסום <b>לא יוכל לקבוע תור אונליין</b>. הוא יראה הודעה שתפנה אותו להתקשר למספרה.</p>
      <p class="hint" style="margin:0 0 12px">התורים הקיימים שלו נשארים, ותוכלו עדיין להוסיף לו תור ידנית. אפשר לבטל את החסימה בכל רגע.</p>
      <button class="btn btn-danger" data-act="do-block-client">חסימת הלקוח</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>
    `);
  }
  let blockTarget = null;
  async function doBlockClient() {
    if (!blockTarget) return;
    await Store.blockClient(blockTarget);
    const nm = blockTarget.name;
    blockTarget = null;
    closeModal(); toast(`${nm} נחסם — לא יוכל לקבוע אונליין`, "", "🚫"); render();
  }

  /* צבירת רשימת הלקוחות — משמשת גם את הרשימה וגם את חלון הפרטים */
  function clientsAgg(st) {
    const map = new Map();
    // אנשי הקשר שיובאו — מופיעים גם אם עדיין לא הזמינו תור
    (Store.getContacts()).forEach((c) => {
      const key = (c.phone && u.normalizePhone(c.phone)) || c.name || c.id;
      if (!map.has(key)) map.set(key, {
        key, name: c.name || "לקוח", phone: c.phone || "", visits: 0, spent: 0,
        lastTs: 0, lastDate: null, contactId: c.id, imported: true,
      });
    });
    st.bookings.filter((b) => b.status !== "cancelled").forEach((b) => {
      const key = clientKey(b);
      const nm = bkName(b), ph = bkPhone(b);
      let c = map.get(key);
      if (!c) { c = { key, name: nm || "לקוח", phone: ph || "", visits: 0, spent: 0, lastTs: 0, lastDate: b.date }; map.set(key, c); }
      if (nm) c.name = nm;
      if (ph) c.phone = ph;
      if (b.status === "confirmed") { c.visits++; c.spent += Number(b.price || 0); }
      if (b.status === "noshow") c.noShows = (c.noShows || 0) + 1;
      const ts = u.dateTime(b.date, b.start).getTime();
      if (ts > c.lastTs) { c.lastTs = ts; c.lastDate = b.date; c.imported = false; }
    });
    return [...map.values()].sort((a, z) => z.lastTs - a.lastTs);
  }

  function ownerClients(st) {
    const clients = clientsAgg(st);
    const importBtn = `<button class="btn btn-primary" data-act="import-clients" style="margin-bottom:14px">📥 ייבוא רשימת לקוחות</button>`;
    if (!clients.length) return importBtn + emptyState("👥", "אין עדיין לקוחות", "ייבאו את רשימת הלקוחות שלכם, או שהם יופיעו כאן אחרי שיזמינו תור");
    const actionsRow = `
      <div class="btn-row" style="margin-bottom:14px">
        <button class="btn btn-primary" data-act="broadcast">📢 הודעה לכולם</button>
        <button class="btn" data-act="import-clients">📥 ייבוא רשימה</button>
      </div>`;
    const totalSpent = clients.reduce((s, c) => s + c.spent, 0);
    const totalVisits = clients.reduce((s, c) => s + c.visits, 0);
    const chips = `
      <div class="stat-chips">
        <div class="stat-chip"><div class="sc-num">${clients.length}</div><div class="sc-lbl">לקוחות</div></div>
        <div class="stat-chip"><div class="sc-num">${totalVisits}</div><div class="sc-lbl">ביקורים</div></div>
        <div class="stat-chip"><div class="sc-num">${u.fmtPrice(totalSpent)}</div><div class="sc-lbl">סה״כ</div></div>
      </div>`;
    const importedNote = clients.some((c) => c.imported) ? `
      <div class="info-note">
        <b>ℹ️ מה זה "לקוח מיובא"?</b>
        <p>לקוח שהוספת מרשימת הלקוחות שלך אך עדיין לא הזמין תור. ברגע שיזמין תור (או שתקבע לו תור ידני) — הוא יהפוך ללקוח מלא עם היסטוריית ביקורים והכנסות, והתג ייעלם.</p>
      </div>` : "";

    /* מסודר: שורה אחידה לכל לקוח — לחיצה פותחת את כרטיס הלקוח עם הפעולות */
    if (tidyOwner()) {
      return `
        ${actionsRow}
        ${chips}
        <div class="section-title">כל הלקוחות</div>
        <div class="card set-list">
          ${clients.map((c) => {
            const blocked = isClientBlocked(c);
            const bits = [];
            if (c.visits) bits.push(c.visits + " ביקורים");
            if (c.lastDate) bits.push("אחרון " + u.relativeDay(c.lastDate));
            if (c.noShows) bits.push("❌ " + c.noShows + " לא הגיע");
            return setRow({
              ico: (String(c.name).trim()[0]) || "?",
              color: blocked ? "var(--bad)" : "var(--sky)",
              label: (blocked ? "🚫 " : "") + c.name,
              sub: bits.length ? bits.join(" · ") : "עדיין לא הזמין תור",
              val: c.spent ? u.fmtPrice(c.spent) : "",
              nav: `data-act="client-detail" data-key="${esc(c.key)}"`,
            });
          }).join("")}
        </div>
        ${importedNote}
      `;
    }

    return `
      ${actionsRow}
      ${chips}
      <div class="section-title">כל הלקוחות</div>
      ${clients.map((c) => `
        <div class="card${isClientBlocked(c) ? " cli-blocked" : ""}" style="padding:13px 15px">
          <div style="display:flex;align-items:center;gap:12px">
            <div class="cli-ava">${esc((String(c.name).trim()[0]) || "?")}</div>
            <div style="flex:1;min-width:0">
              <div class="bk-title">${esc(c.name)}${c.imported ? ` <span class="cli-badge">מיובא</span>` : ""}${isClientBlocked(c) ? ` <span class="cli-badge blocked">🚫 חסום</span>` : ""}</div>
              <div class="bk-sub">${c.phone ? `<a href="tel:${esc(c.phone)}">${esc(c.phone)}</a>` : "ללא טלפון"}</div>
              <div class="bk-sub">${c.imported ? "עדיין לא הזמין תור" : `${c.visits} ביקורים · <b>${u.fmtPrice(c.spent)}</b> · אחרון ${esc(u.relativeDay(c.lastDate))}`}${c.noShows ? ` · <span class="noshow-cnt">❌ ${c.noShows} לא הגיע</span>` : ""}</div>
              ${(!isClientBlocked(c) && c.noShows >= 2) ? `<div class="bk-sub" style="color:var(--bad)">⚠️ לא הגיע ${c.noShows} פעמים — כדאי לשקול חסימה</div>` : ""}
            </div>
            ${isClientBlocked(c)
              ? `<button class="btn btn-sm" data-act="unblock-client" data-key="${esc(blockKeyOf(c))}">בטל חסימה</button>`
              : c.imported
              ? `<button class="btn btn-sm" data-act="del-contact" data-id="${esc(c.contactId)}">הסר</button>`
              : `<div class="bk-acts">
                   <button class="btn btn-sm" data-act="client-detail" data-key="${esc(c.key)}">פרטים</button>
                   <button class="btn btn-sm btn-danger" data-act="block-client" data-key="${esc(c.key)}">חסום</button>
                 </div>`}
          </div>
        </div>`).join("")}
      ${importedNote}
    `;
  }

  /* מודאל ייבוא לקוחות — הדבקת רשימה (שם + טלפון בכל שורה) */
  function openImportClients() {
    openModal(`
      <div class="m-title">📥 ייבוא רשימת לקוחות</div>
      <div class="m-sub">הדביקו שורה לכל לקוח — שם וטלפון</div>
      <p class="hint" style="margin:8px 0 6px">כל שורה: <b>שם, טלפון</b> · לדוגמה:</p>
      <div class="import-eg">דני כהן, 050-1234567<br>מאיה לוי 0529876543<br>יוסי</div>
      <textarea class="input" id="imp-text" rows="7" placeholder="דני כהן, 050-1234567
מאיה לוי, 052-9876543" style="resize:vertical;line-height:1.6"></textarea>
      <button class="btn btn-primary" data-act="do-import-clients" style="margin-top:12px">ייבוא</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>
    `);
    setTimeout(() => $("#imp-text") && $("#imp-text").focus(), 100);
  }

  // ניתוח טקסט חופשי לרשימת {name, phone}. הטלפון = רצף הספרות בשורה; השם = השאר.
  function parseContactsText(text) {
    return (text || "").split(/\r?\n/).map((line) => {
      const raw = line.trim();
      if (!raw) return null;
      const phoneMatch = raw.match(/[0-9][0-9\-\s()+]{6,}/);
      let phone = "", name = raw;
      if (phoneMatch) {
        phone = phoneMatch[0].replace(/[^\d+]/g, "");
        name = raw.replace(phoneMatch[0], "").replace(/[,;|\t]+/g, " ").trim();
      } else {
        name = raw.replace(/[,;|\t]+/g, " ").trim();
      }
      if (!name && !phone) return null;
      return { name: name || "לקוח", phone };
    }).filter(Boolean);
  }

  async function doImportClients() {
    const text = ($("#imp-text") && $("#imp-text").value) || "";
    const list = parseContactsText(text);
    if (!list.length) { toast("לא נמצאו לקוחות בטקסט", "", "✋"); return; }
    const noPhone = list.filter((c) => !u.isValidPhone(c.phone || "")).length;
    const added = await Store.addContacts(list);
    closeModal();
    toast(added ? `יובאו ${added} לקוחות ✓` : "כל הלקוחות כבר קיימים", added ? "good" : "sky", "📥");
    // בלי טלפון אי אפשר לשלוח וואטסאפ — כדאי שהספר יידע
    if (noPhone) setTimeout(() => toast(`${noPhone} לקוחות ללא טלפון תקין — לא יקבלו הודעות וואטסאפ`, "", "📵"), 2600);
    render();
  }

  /* מודאל הודעה קבוצתית — נשלחת כהתראת פוש לכל הלקוחות שהפעילו התראות */
  // כמה לקוחות ייתכן שיקבלו את ההודעה (מי שהזמין דרך האפליקציה)
  function broadcastRecipients() {
    const st = Store.get();
    return new Set(
      (st.bookings || [])
        .filter((b) => b.userId && b.userId.indexOf("owner") !== 0)
        .map((b) => b.userId)
    ).size;
  }

  function openBroadcast() {
    const recipients = broadcastRecipients();
    openModal(`
      <div class="m-title">📢 הודעה לכל הלקוחות</div>
      <div class="m-sub">ההודעה תגיע כהתראה לטלפון של הלקוחות שהזמינו דרך האפליקציה והפעילו התראות</div>
      <textarea class="input" id="bc-text" rows="4" maxlength="180" placeholder="לדוגמה: מבצע החודש — תספורת + עיצוב זקן ב-60₪ בלבד! מוזמנים לקבוע תור 💈" style="resize:vertical;line-height:1.6;margin-top:6px"></textarea>
      <p class="hint" style="margin:8px 0 4px">עד 180 תווים · עד ${recipients} לקוחות שהזמינו דרך האפליקציה — יקבלו רק מי שאישר התראות</p>
      <button class="btn btn-primary" data-act="do-broadcast" style="margin-top:8px">שליחה לכל הלקוחות</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>
    `);
    setTimeout(() => $("#bc-text") && $("#bc-text").focus(), 100);
  }

  /* שליחת ההודעה הקבוצתית. sel — תיבת הטקסט (מודאל הלקוחות או הכרטיס בלשונית פרסום) */
  async function doBroadcast(sel, act) {
    const el = $(sel || "#bc-text");
    const text = ((el && el.value) || "").trim();
    if (!text) { toast("נא לכתוב הודעה", "", "✋"); return; }
    const btn = $("[data-act='" + (act || "do-broadcast") + "']");
    const label = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; btn.textContent = "שולח…"; }
    const entry = await Store.addBroadcast(text);
    if (sel === "#pb-text") {                       // כרטיס בעמוד — מנקים ומשחררים
      if (el && entry) el.value = "";
      if (btn) { btn.disabled = false; btn.textContent = label; }
    } else {
      closeModal();
    }
    toast(entry ? "ההודעה תישלח ללקוחות בקרוב 📢" : "לא ניתן לשלוח כרגע", entry ? "good" : "", "📢");
  }

  /* מילוי מהיר של תבנית הודעה בכרטיס שבלשונית פרסום */
  function fillBroadcast(text) {
    const el = $("#pb-text");
    if (!el) return;
    el.value = text || "";
    el.focus();
  }

  /* ---------- שליחה בוואטסאפ — מגיע לכל לקוח עם טלפון, גם בלי אפליקציה ---------- */
  // 0501234567 → 972501234567 (פורמט wa.me)
  function waIntl(phone) {
    const n = u.normalizePhone(phone || "");
    if (!/^0\d{8,9}$/.test(n)) return "";
    return "972" + n.slice(1);
  }

  // כל הלקוחות עם טלפון — מהתורים ומהרשימה שיובאה, ללא כפילויות
  function clientsWithPhone() {
    const st = Store.get();
    const map = new Map();
    (Store.getContacts()).forEach((c) => {
      const p = u.normalizePhone(c.phone || "");
      if (p && !map.has(p)) map.set(p, { name: c.name || "לקוח", phone: p });
    });
    (st.bookings || []).filter((b) => b.status !== "cancelled").forEach((b) => {
      const p = u.normalizePhone(bkPhone(b) || "");
      if (!p) return;
      const nm = bkName(b);
      const cur = map.get(p);
      if (cur) { if (nm) cur.name = nm; }
      else map.set(p, { name: nm || "לקוח", phone: p });
    });
    return [...map.values()]
      .filter((c) => waIntl(c.phone))
      .sort((a, z) => String(a.name).localeCompare(String(z.name), "he"));
  }

  let waSent = new Set();
  let waText = "";

  function openWaBlast() {
    const el = $("#pb-text");
    const text = ((el && el.value) || "").trim();
    if (!text) { toast("קודם כתבו את ההודעה", "", "✋"); return; }
    const list = clientsWithPhone();
    if (!list.length) { toast("אין לקוחות עם מספר טלפון", "", "📵"); return; }
    waSent = new Set();
    waText = text;
    openModal(`
      <div class="m-title">📲 שליחה בוואטסאפ</div>
      <div class="m-sub">מגיע לכל לקוח עם טלפון — גם למי שאין לו את האפליקציה</div>
      <div class="wa-msg">${esc(text)}</div>
      <div id="wa-list"></div>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:12px">סגירה</button>
    `);
    renderWaList();
  }

  function renderWaList() {
    const el = $("#wa-list"); if (!el) return;
    const list = clientsWithPhone();
    const done = list.filter((c) => waSent.has(c.phone)).length;
    el.innerHTML =
      `<div class="wa-prog">נשלחו ${done} מתוך ${list.length}</div>
       <div class="wa-list">` +
      list.map((c) => {
        const sent = waSent.has(c.phone);
        const href = "https://wa.me/" + waIntl(c.phone) + "?text=" + encodeURIComponent(waText);
        return `
        <div class="wa-row${sent ? " sent" : ""}">
          <div class="wa-who"><b>${esc(c.name)}</b><span>${esc(u.fmtPhone(c.phone))}</span></div>
          <a class="btn btn-wa btn-sm" href="${esc(href)}" target="_blank" rel="noopener"
             data-act="wa-sent" data-p="${esc(c.phone)}">${sent ? "✓ נשלח" : "שליחה"}</a>
        </div>`;
      }).join("") + `</div>`;
  }

  function clientDetail(key) {
    const st = Store.get();
    const c = clientsAgg(st).find((x) => x.key === key);
    if (!c) return;
    const bks = st.bookings.filter((b) => b.status !== "cancelled" && clientKey(b) === key)
      .sort((a, z) => u.dateTime(z.date, z.start) - u.dateTime(a.date, a.start));
    const blocked = isClientBlocked(c);
    // הפעולות שהיו קודם על כרטיס הלקוח ברשימה — עברו לכאן
    const acts = blocked
      ? `<button class="btn" data-act="unblock-client" data-key="${esc(blockKeyOf(c))}">🔓 ביטול החסימה</button>`
      : c.imported
      ? `<button class="btn btn-danger" data-act="del-contact" data-id="${esc(c.contactId || "")}">🗑️ הסרה מהרשימה</button>`
      : `<button class="btn btn-danger" data-act="block-client" data-key="${esc(c.key)}">🚫 חסימת הלקוח</button>`;
    openModal(`
      <div class="m-title">${esc(c.name)}${blocked ? " 🚫" : ""}</div>
      <div class="m-sub">${bks.length} תורים · ${u.fmtPrice(c.spent)} סה״כ${c.phone ? ` · <a href="tel:${esc(c.phone)}">${esc(u.fmtPhone(c.phone))}</a>` : ""}</div>
      ${blocked ? `<p class="hint" style="margin:10px 0 0;color:var(--bad)">הלקוח חסום — לא יכול לקבוע תור אונליין.</p>` : ""}
      ${(!blocked && c.noShows >= 2) ? `<p class="hint" style="margin:10px 0 0;color:var(--bad)">⚠️ לא הגיע ${c.noShows} פעמים — כדאי לשקול חסימה.</p>` : ""}
      <div style="max-height:44vh;overflow-y:auto;margin-top:8px">
        ${bks.length ? bks.map((b) => `
          <div class="summary-row">
            <span class="sr-k">${esc(u.longDate(b.date))} · ${esc(b.start)}</span>
            <span class="sr-v">${esc(b.serviceName)} · ${u.fmtPrice(b.price)} ${b.status === "confirmed" ? "✓" : ""}</span>
          </div>`).join("") : `<p class="hint" style="margin:6px 0 0">עדיין לא הזמין תור.</p>`}
      </div>
      <div style="margin-top:14px">${acts}</div>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">סגירה</button>
    `);
  }

  /* ---------- דוח חודשי (מנהל בלבד) ---------- */
  const HEB_MONTHS = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"];
  function ymNow() { const d = new Date(); return d.getFullYear() + "-" + u.pad(d.getMonth() + 1); }
  function ymShift(ym, delta) {
    const [y, m] = ym.split("-").map(Number);
    const d = new Date(y, m - 1 + delta, 1);
    return d.getFullYear() + "-" + u.pad(d.getMonth() + 1);
  }
  function ymLabel(ym) { const [y, m] = ym.split("-").map(Number); return HEB_MONTHS[m - 1] + " " + y; }

  function ownerReport(st) {
    if (!view.statMonth) view.statMonth = ymNow();
    const ym = view.statMonth;
    const isCur = ym === ymNow();
    const rows = st.bookings
      .filter((b) => b.status === "confirmed" && b.date.startsWith(ym))
      .map((b) => ({ b, ts: u.dateTime(b.date, b.start).getTime() }))
      .sort((a, z) => a.ts - z.ts);
    const total = rows.reduce((s, x) => s + Number(x.b.price || 0), 0);

    const table = rows.length ? `
      <div class="card" style="padding:4px 0;overflow-x:auto">
        <table class="stat-table">
          <thead><tr><th>תאריך</th><th>לקוח</th><th>שירות</th><th>שולם</th><th></th></tr></thead>
          <tbody>
            ${rows.map((x) => `
            <tr>
              <td class="st-date">${Number(x.b.date.slice(8, 10))}.${Number(x.b.date.slice(5, 7))} · ${esc(x.b.start)}</td>
              <td class="st-name">${esc(bkName(x.b) || "לקוח")}</td>
              <td>${esc(x.b.serviceName)}</td>
              <td class="money">${u.fmtPrice(x.b.price)}</td>
              <td><button class="row-del" data-act="del-report" data-id="${x.b.id}" aria-label="מחיקה">✕</button></td>
            </tr>`).join("")}
          </tbody>
          <tfoot><tr>
            <td colspan="3">סה״כ ${rows.length} תספורות</td>
            <td class="money">${u.fmtPrice(total)}</td>
            <td></td>
          </tr></tfoot>
        </table>
      </div>` : emptyState("📊", "אין עדיין נתונים בחודש זה", "תספורת נכנסת לדוח ברגע שהלקוח מאשר הגעה");

    const reviews = (st.reviews || []).slice().sort((a, z) => (z.createdAt || 0) - (a.createdAt || 0));
    const avg = reviews.length ? (reviews.reduce((s, r) => s + Number(r.rating || 0), 0) / reviews.length).toFixed(1) : null;
    const revHtml = reviews.length
      ? reviews.slice(0, 30).map((r) => reviewCardHtml(r)).join("")
      : `<p class="hint" style="margin-top:4px">אין עדיין ביקורות — לקוחות מתבקשים לדרג אחרי כל תספורת.</p>`;

    const revTitle = `ביקורות לקוחות${avg ? ` · ממוצע ${avg} ★` : ""}`;
    const head = `
      <div class="month-nav">
        <button class="icon-btn" data-act="stat-prev" title="חודש קודם">‹</button>
        <div class="mn-label">${ymLabel(ym)}</div>
        <button class="icon-btn" data-act="stat-next" title="חודש הבא" ${isCur ? "disabled" : ""}>›</button>
      </div>
      <div class="stat-chips">
        <div class="stat-chip"><div class="sc-num">${rows.length}</div><div class="sc-lbl">תספורות שאושרו</div></div>
        <div class="stat-chip"><div class="sc-num">${u.fmtPrice(total)}</div><div class="sc-lbl">הכנסות החודש</div></div>
      </div>
      ${rows.length ? `<button class="btn btn-sm" data-act="export-report" style="margin-bottom:12px">📊 ייצוא לאקסל</button>` : ""}
      ${table}
      <p class="hint">הדוח מציג תורים שהלקוח אישר בהם הגעה. בתחילת כל חודש הטבלה מתחילה מאפס — אפשר לדפדף לחודשים קודמים עם החצים.</p>`;

    /* מסודר: הביקורות יוצאות לעמוד-משנה משלהן במקום כותרת בתוך הדוח */
    if (tidyOwner()) {
      if (view.subPage === "rev") {
        return subBack("חזרה לדוח") + `<div class="section-title">${revTitle}</div>` + revHtml;
      }
      view.subPage = null;
      return head + `
        <div class="card set-list" style="margin-top:14px">
          ${setRow({
            nav: `data-act="sub-page" data-sub="rev"`, ico: "⭐", color: "#f59e0b",
            label: "ביקורות לקוחות",
            sub: reviews.length ? `${reviews.length} ביקורות מלקוחות` : "אין עדיין ביקורות",
            val: avg ? avg + " ★" : "",
          })}
        </div>`;
    }

    return head + `
      <div class="section-title">${revTitle}</div>
      ${revHtml}
    `;
  }

  function ownerSecuritySection(st) {
    const shop = st.shop || {};
    if (!(UG.Auth && authAvail)) {
      return `
        <div class="section-title">🔒 אבטחה</div>
        <div class="card"><div class="conn-line"><span class="conn-dot local"></span>
          התחברות מאובטחת דורשת חיבור ענן (Firebase) פעיל.</div></div>`;
    }
    if (shop.ownerUid) {
      const email = (UG.Auth.currentEmail && UG.Auth.currentEmail()) || "";
      const isOwnerAuthed = !!(UG.Auth.currentUid && UG.Auth.currentUid() === shop.ownerUid);
      return `
        <div class="section-title">🔒 אבטחה</div>
        <div class="card">
          <div class="conn-line" style="margin-bottom:10px"><span class="conn-dot"></span>
            המספרה מאובטחת בחשבון אישי${email ? ` · ${esc(email)}` : ""}</div>
          <p class="hint" style="margin-top:0">הכניסה לניהול היא עם החשבון הזה בלבד.</p>
          <div class="btn-row btn-row-wrap" style="margin-top:10px;gap:8px">
            <button class="btn btn-sm" data-act="auth-signout">התנתקות מהחשבון</button>
            <button class="btn btn-sm btn-danger" data-act="release-security">שחרור ההגנה / העברה לספר אחר</button>
          </div>
          <p class="hint" style="margin-top:8px">שחרור ההגנה מחזיר את הכניסה לקוד, כדי שספר אחר יוכל לאבטח את המספרה עם החשבון שלו.${isOwnerAuthed ? "" : " לשחרור יש להתחבר קודם עם חשבון הבעלים הנוכחי."}</p>
        </div>`;
    }
    return `
      <div class="section-title">🔒 אבטחה</div>
      <div class="card">
        <p class="hint" style="margin-top:0">הגנו על המספרה עם חשבון אישי (Google או אימייל) במקום קוד משותף — רק אתם תוכלו להיכנס לניהול.</p>
        <button class="btn btn-primary" data-act="secure-shop" style="margin-top:10px">🔒 הגנה על המספרה שלי</button>
      </div>`;
  }

  // קישור החשבון (Google/מייל) למספרה — מוודא שלא מחובר כבר לבעלים אחר
  async function linkAccountToShop(errEl) {
    const uid = UG.Auth.currentUid();
    if (!uid) { if (errEl) errEl.textContent = "ההתחברות לא הושלמה — נסו שוב"; return false; }
    const shop = Store.get().shop;
    if (shop.ownerUid && shop.ownerUid !== uid) {
      if (errEl) errEl.textContent = "המספרה כבר מאובטחת בחשבון אחר";
      await UG.Auth.signOut(); return false;
    }
    if (!shop.ownerUid) await Store.saveShop({ ownerUid: uid });
    closeModal(); toast("המספרה מאובטחת בחשבון שלך 🔒", "good", "🔒"); render();
    return true;
  }

  /* שחרור ההגנה מהמספרה — מנקה את ownerUid כדי שאפשר יהיה להיכנס עם הקוד ולאבטח
     מחדש עם חשבון אחר (למשל העברת המספרה לספר). מותנה בכך שמחוברים כרגע עם חשבון
     הבעלים הנוכחי — אחרת חוקי האבטחה יחסמו את הכתיבה ואין למי לשחרר. */
  function releaseSecurity() {
    const shop = (Store.get() && Store.get().shop) || {};
    if (!shop.ownerUid) { toast("המספרה כבר אינה מאובטחת בחשבון", "", "🔓"); return; }
    const authedAsOwner = !!(UG.Auth && UG.Auth.currentUid && UG.Auth.currentUid() === shop.ownerUid);
    if (!authedAsOwner) {
      toast("כדי לשחרר את ההגנה יש להתחבר קודם עם חשבון הבעלים הנוכחי", "", "🔒");
      if (UG.Auth && authAvail) promptOwnerLogin(shop.ownerUid);
      return;
    }
    openModal(`
      ${authHeader()}
      <div class="m-sub" style="text-align:center;margin-bottom:8px">שחרור ההגנה מהמספרה</div>
      <p class="hint" style="margin-top:0">אחרי השחרור, הכניסה לניהול חוזרת לקוד הרגיל, והספר יוכל לאבטח את המספרה מחדש עם החשבון שלו. אפשר תמיד לאבטח שוב.</p>
      <button class="btn btn-danger" data-act2="do-release-security">כן, שחרר את ההגנה</button>
      <button class="btn btn-ghost btn-sm" data-act="close-modal" style="margin-top:8px;width:100%">ביטול</button>
    `);
    const btn = $("[data-act2='do-release-security']");
    if (btn) btn.addEventListener("click", async () => {
      btn.disabled = true; btn.textContent = "משחרר…";
      try {
        await Store.saveShop({ ownerUid: null });   // הסרת ownerUid → מספרה לא-מאובטחת
        closeModal();
        toast("ההגנה שוחררה 🔓 — עכשיו אפשר להיכנס עם הקוד ולאבטח מחדש", "good", "🔓");
        render();
      } catch (e) {
        btn.disabled = false; btn.textContent = "כן, שחרר את ההגנה";
        toast("השחרור נכשל — ודאו שאתם מחוברים עם חשבון הבעלים", "", "⚠️");
      }
    });
  }

  function openSecureShop() {
    openModal(`
      <div class="m-title">🔒 הגנה על המספרה</div>
      <div class="m-sub">התחברו כדי שרק אתם תוכלו לנהל את המספרה</div>
      <button class="btn btn-google" data-act2="secure-google">
        <span class="g-ico">${googleIcoSvg()}</span>המשך עם Google</button>
      <div class="auth-or"><span>או עם אימייל</span></div>
      <div class="field"><label>אימייל</label>
        <input class="input" id="au-email" type="email" inputmode="email" autocomplete="username" placeholder="you@example.com"></div>
      <div class="field"><label>סיסמה (לפחות 6 תווים)</label>
        <input class="input" id="au-pass" type="password" autocomplete="current-password"></div>
      <p class="hint" id="au-err" style="color:var(--bad);min-height:15px;margin-top:0"></p>
      <button class="btn btn-primary" data-act2="do-secure-login">התחברות</button>
      <button class="btn" data-act2="do-secure-signup" style="margin-top:8px">הרשמה (חשבון חדש)</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>
    `);
    const run = async (mode) => {
      const email = ($("#au-email") && $("#au-email").value.trim()) || "";
      const pass = ($("#au-pass") && $("#au-pass").value) || "";
      const errEl = $("#au-err");
      if (!email || !pass) { if (errEl) errEl.textContent = "נא למלא אימייל וסיסמה"; return; }
      if (errEl) errEl.textContent = "רגע…";
      try {
        if (mode === "signup") await UG.Auth.signUp(email, pass);
        else await UG.Auth.signIn(email, pass);
        await linkAccountToShop(errEl);
      } catch (e) { if (errEl) errEl.textContent = UG.Auth.humanError(e); }
    };
    const google = async () => {
      const errEl = $("#au-err");
      if (errEl) errEl.textContent = "מתחבר עם Google…";
      try {
        rememberGoogleIntent("secure");
        const user = await UG.Auth.signInWithGoogle();
        if (user) { clearGoogleIntent(); await linkAccountToShop(errEl); }   // popup הצליח; redirect ייטופל בטעינה
      } catch (e) { clearGoogleIntent(); if (errEl) errEl.textContent = UG.Auth.humanError(e); }
    };
    const lb = $("[data-act2='do-secure-login']"); if (lb) lb.addEventListener("click", () => run("login"));
    const sb = $("[data-act2='do-secure-signup']"); if (sb) sb.addEventListener("click", () => run("signup"));
    const gb = $("[data-act2='secure-google']"); if (gb) gb.addEventListener("click", google);
    setTimeout(() => $("#au-email") && $("#au-email").focus(), 100);
  }

  function googleIcoSvg() {
    return `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="#4285F4" d="M23.5 12.3c0-.9-.1-1.5-.2-2.2H12v4h6.5c-.1 1-.8 2.5-2.3 3.5v2.9h3.7c2.2-2 3.6-5 3.6-8.2z"/><path fill="#34A853" d="M12 24c3.1 0 5.7-1 7.6-2.8l-3.7-2.9c-1 .7-2.3 1.2-3.9 1.2-3 0-5.5-2-6.4-4.8H1.7v3C3.6 21.3 7.5 24 12 24z"/><path fill="#FBBC05" d="M5.6 14.7c-.2-.7-.4-1.4-.4-2.2s.1-1.5.4-2.2v-3H1.7C1 8.7.6 10.3.6 12s.4 3.3 1.1 4.7l3.9-2z"/><path fill="#EA4335" d="M12 4.8c1.7 0 2.9.7 3.5 1.3l2.7-2.6C16.5 1.9 14.4 1 12 1 7.5 1 3.6 3.7 1.7 7.3l3.9 3c.9-2.7 3.4-4.5 6.4-4.5z"/></svg>`;
  }

  /* זכירת כוונת ההתחברות עם Google לפני הפניה (redirect), כי הדף נטען מחדש */
  function rememberGoogleIntent(mode) {
    try { sessionStorage.setItem("ug_gauth", mode + ":" + SHOP); } catch (e) {}
  }
  function clearGoogleIntent() { try { sessionStorage.removeItem("ug_gauth"); } catch (e) {} }
  function readGoogleIntent() {
    try { const v = sessionStorage.getItem("ug_gauth") || ""; const [mode, sid] = v.split(":"); return sid === SHOP ? mode : ""; }
    catch (e) { return ""; }
  }

  function ownerGallerySection() {
    const photos = Store.getGallery();
    return `
      <div class="section-title">🖼️ גלריית תספורות (${photos.length})</div>
      <div class="card">
        <label class="btn btn-primary" style="cursor:pointer;margin:0">
          ＋ העלאת תמונה
          <input type="file" accept="image/*" data-gfile style="display:none">
        </label>
        <p class="hint">התמונות שתעלה מוצגות ללקוחות בלשונית ״גלריה״. מומלץ תמונות מאוזנות/מאונכות ברורות.</p>
        ${photos.length ? `<div class="gallery-grid" style="margin-top:14px">
          ${photos.map((p) => `
            <div class="gphoto">
              <img src="${esc(p.dataUrl)}" loading="lazy">
              <button class="gdel" data-delphoto="${p.id}" aria-label="מחיקה">✕</button>
            </div>`).join("")}
        </div>` : ""}
      </div>
    `;
  }

  /* ---------- עורך רשימת הספרים (בהגדרות) ---------- */
  let staffEdit = [];
  function staffEditorBody() {
    return (staffEdit.length ? staffEdit : [""]).map((n, i) => `
      <div class="wiz-staff-row" data-staff-row="${i}">
        <input class="input st-name" placeholder="שם הספר" value="${esc(n || "")}">
        <button type="button" class="sv-del" data-act="stf-del" data-i="${i}" aria-label="מחיקה">✕</button>
      </div>`).join("");
  }
  function captureStaffEdit() {
    const list = $("#stf-list"); if (!list) return;
    staffEdit = [...list.querySelectorAll(".st-name")].map((el) => el.value.trim());
  }
  function refreshStaffEditor() { const l = $("#stf-list"); if (l) l.innerHTML = staffEditorBody(); }
  function openStaffEditor() {
    const st = Store.get();
    staffEdit = (st.shop.staff || []).slice();
    if (!staffEdit.length) staffEdit = [""];
    openModal(`
      <div class="m-title">🧑‍🔧 ספרים במספרה</div>
      <div class="m-sub">הלקוח יוכל לבקש ספר מסוים בעת ההזמנה</div>
      <div id="stf-list" style="margin-top:12px">${staffEditorBody()}</div>
      <button type="button" class="btn btn-sm" data-act="stf-add" style="width:100%;margin-top:6px">＋ הוספת ספר</button>
      <button class="btn btn-primary" data-act="save-staff" style="margin-top:12px">שמירה</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>
    `);
    setTimeout(() => { const el = $("#stf-list .st-name"); if (el) el.focus(); }, 100);
  }
  async function saveStaff() {
    captureStaffEdit();
    const names = staffEdit.map((n) => (n || "").trim()).filter(Boolean);
    await Store.saveShop({ staff: names });
    closeModal();
    toast("רשימת הספרים נשמרה ✓", "good", "🧑‍🔧");
    render();
  }

  function confirmDeleteBooking(id) {
    const b = Store.get().bookings.find((x) => x.id === id);
    if (!b) return;
    openModal(`
      <div class="m-title">מחיקת רשומה מהדוח</div>
      <div class="m-sub">${esc(bkName(b) || "לקוח")} · ${esc(b.serviceName)} · ${esc(u.longDate(b.date))}</div>
      <p style="font-size:14px;color:var(--muted);margin:6px 0 20px">הרשומה תוסר מהדוח לצמיתות ולא ניתן יהיה לשחזר אותה.</p>
      <button class="btn btn-danger" data-act="do-del-report" data-id="${id}">מחיקה</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>
    `);
  }

  /* ---------- פרסום ללקוחות ---------- */
  function ownerPublish(st) {
    const link = clientLink();
    const owner = (st.shop.ownerName || "").trim().split(/\s+/)[0];
    const svcCount = (st.services || []).filter((s) => s.active !== false).length;

    // מבנה מסודר: רשימת שורות → כל אחת פותחת את הכלי עצמו
    if (tidyOwner()) {
      if (view.subPage === "link") return subBack("חזרה לפרסום") + pubLinkCard(link);
      if (view.subPage === "qr") return subBack("חזרה לפרסום") + qrShareCard();
      if (view.subPage === "bc") return subBack("חזרה לפרסום") + pubBroadcastCard();
      if (view.subPage === "how") return subBack("חזרה לפרסום") + pubHowCard();
      view.subPage = null;
      return `
        <div class="pub-hero">
          <div class="pub-ico${shopLogo(st) ? " has-img" : ""}">${shopLogo(st)
            ? `<img class="pub-logo" src="${esc(shopLogo(st))}" alt="">` : "📣"}</div>
          <h2>${owner ? esc(owner) + ", המספרה שלך מוכנה!" : "המספרה שלך מוכנה!"}</h2>
          <p>שלח/י את הקישור הזה ללקוחות — הם יזמינו תור לבד, ישירות מהטלפון.</p>
        </div>
        <div class="card set-list">
          ${setRow({ nav: `data-act="sub-page" data-sub="link"`, ico: "🔗", color: "#0ea5e9",
            label: "הקישור האישי שלך", sub: "העתקה, שיתוף ותצוגת לקוח" })}
          ${setRow({ nav: `data-act="sub-page" data-sub="qr"`, ico: "📷", color: "#8b5cf6",
            label: "קוד QR", sub: "להדפסה ולתלייה בחנות" })}
          ${setRow({ nav: `data-act="sub-page" data-sub="bc"`, ico: "📢", color: "#ef4444",
            label: "הודעה לכל הלקוחות", sub: "התראה באפליקציה או וואטסאפ" })}
          ${setRow({ nav: `data-act="sub-page" data-sub="how"`, ico: "💡", color: "#f59e0b",
            label: "איך זה עובד?", sub: "3 שלבים פשוטים" })}
        </div>
        ${svcCount ? "" : `
        <div class="banner good" style="margin-top:14px">
          <span class="bn-ico">✂️</span>
          <div class="bn-body">
            <div class="bn-title">כדאי להוסיף שירותים</div>
            <div class="bn-sub">הגדירו סוגי תספורת, מחירים ומשך — מ״הגדרות ← שירותים״.</div>
          </div>
        </div>`}`;
    }

    return `
      <div class="pub-hero">
        <div class="pub-ico${shopLogo(st) ? " has-img" : ""}">${shopLogo(st)
          ? `<img class="pub-logo" src="${esc(shopLogo(st))}" alt="">` : "📣"}</div>
        <h2>${owner ? esc(owner) + ", המספרה שלך מוכנה!" : "המספרה שלך מוכנה!"}</h2>
        <p>שלח/י את הקישור הזה ללקוחות — הם יזמינו תור לבד, ישירות מהטלפון.</p>
      </div>

      ${pubLinkCard(link)}

      ${qrShareCard()}

      ${pubBroadcastCard()}
      ${pubHowCard()}

      ${svcCount ? "" : `
      <div class="banner good" style="margin-top:14px">
        <span class="bn-ico">✂️</span>
        <div class="bn-body">
          <div class="bn-title">כדאי להוסיף שירותים</div>
          <div class="bn-sub">הגדירו סוגי תספורת, מחירים ומשך — בלשונית ״שירותים״.</div>
        </div>
      </div>`}
    `;
  }

  /* כרטיסי לשונית הפרסום — מופרדים כדי שישמשו גם כעמודי-משנה במבנה המסודר */
  function pubLinkCard(link) {
    return `
      <div class="section-title">🔗 הקישור האישי שלך</div>
      <div class="card">
        <div class="pub-link">${esc(link)}</div>
        <div class="btn-row btn-row-wrap" style="margin-top:13px">
          <button class="btn btn-primary btn-sm" data-act="copy-link">📋 העתקה</button>
          <button class="btn btn-sm" data-act="share-app">🔗 שיתוף</button>
          <button class="btn btn-sm" data-act="preview-client">👁️ תצוגת לקוח</button>
          <button class="btn btn-wa btn-sm" data-act="share-wa"><svg class="wa-ico" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M.057 24l1.687-6.163a11.867 11.867 0 01-1.587-5.945C.16 5.335 5.495 0 12.05 0a11.82 11.82 0 018.413 3.488 11.824 11.824 0 013.48 8.414c-.003 6.557-5.338 11.892-11.893 11.892a11.9 11.9 0 01-5.688-1.448L.057 24zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884-.001 2.225.651 3.891 1.746 5.634l-.999 3.648 3.742-.981zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.767.967-.94 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372s-1.04 1.016-1.04 2.479 1.065 2.876 1.213 3.074c.149.198 2.095 3.2 5.076 4.487.709.306 1.263.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.695.248-1.29.173-1.414z"/></svg>וואטסאפ</button>
        </div>
        <div class="info-note" style="margin-top:12px">
          <b>🔒 הקישור בטוח לשיתוף</b>
          <p>לקוח שפותח את הקישור רואה <b>רק את עמוד ההזמנה</b> — לעולם לא את הניהול. הניהול נפתח אך ורק במכשיר שלך, אחרי כניסה עם הסיסמה. לחצו ״👁️ תצוגת לקוח״ כדי לראות בדיוק מה הלקוח רואה.</p>
        </div>
      </div>`;
  }
  function pubBroadcastCard() {
    return `
      <div class="section-title">📢 הודעה לכל הלקוחות</div>
      <div class="card">
        <div class="hint" style="margin:0 0 11px">כתבו הודעה — היא תגיע כהתראה לטלפון של כל הלקוחות שהזמינו דרך האפליקציה, גם כשהיא סגורה.</div>
        <div class="bc-tpls">
          <button class="bc-tpl" data-act="bc-tpl" data-t="עברנו לאפליקציית תורים חדשה! מעכשיו אפשר לקבוע תור ישירות מהטלפון, בלי טלפונים והודעות 💈">📱 עברנו לאפליקציה</button>
          <button class="bc-tpl" data-act="bc-tpl" data-t="מבצע חדש החודש! מוזמנים לקבוע תור ולנצל 💈">🎉 מבצע חדש</button>
          <button class="bc-tpl" data-act="bc-tpl" data-t="השקנו שירות חדש במחיר השקה מנצח — מוזמנים לקבוע תור ולהתרשם ✂️">✨ שירות חדש</button>
        </div>
        <textarea class="input" id="pb-text" rows="3" maxlength="180"
          placeholder="לדוגמה: מבצע החודש — תספורת + עיצוב זקן ב-60₪ בלבד! מוזמנים לקבוע תור 💈"
          style="resize:vertical;line-height:1.6"></textarea>
        <div class="hint" style="margin:8px 0 12px">עד 180 תווים</div>
        <button class="btn btn-primary" data-act="do-broadcast-pub">📢 התראה באפליקציה</button>
        <div class="hint" style="margin:6px 0 14px">מיידי וחינם · עד ${broadcastRecipients()} לקוחות — רק מי שאישר התראות</div>
        <button class="btn btn-wa" data-act="wa-blast">📲 שליחה בוואטסאפ</button>
        <div class="hint" style="margin:6px 0 0">מגיע ל-${clientsWithPhone().length} לקוחות עם טלפון — גם בלי אפליקציה · נשלח מהוואטסאפ שלך, לקוח-לקוח</div>
      </div>`;
  }
  function pubHowCard() {
    return `
      <div class="section-title">איך זה עובד?</div>
      <div class="card">
        <div class="pub-step"><span class="ps-n">1</span><div><b>שולח/ת את הקישור</b><div class="hint">בוואטסאפ, אינסטגרם או סטטוס — לכל הלקוחות.</div></div></div>
        <div class="pub-step"><span class="ps-n">2</span><div><b>הלקוח פותח ומזמין</b><div class="hint">בלי הרשמה ובלי סיסמה. הוא גם יכול להתקין את זה כאפליקציה בטלפון.</div></div></div>
        <div class="pub-step"><span class="ps-n">3</span><div><b>אתה מקבל התראה</b><div class="hint">כל תור חדש מופיע מיד בלשונית ״תורים״ וביומן.</div></div></div>
      </div>

      <div class="card" style="margin-top:14px">
        <div style="display:flex;align-items:center;gap:12px">
          <div style="font-size:26px">🔄</div>
          <div style="flex:1;min-width:0">
            <b style="font-size:15px">כל שינוי מתעדכן אצלם מיד</b>
            <div class="hint" style="margin-top:2px">שירותים, מחירים, שעות ותמונות — מה שתשנה/י כאן, הלקוחות רואים בקישור בזמן אמת. אין צורך לשלוח קישור חדש.</div>
          </div>
        </div>
      </div>`;
  }

  function ownerSettings(st) {
    const logo = shopLogo(st) || "";
    const cover = shopCover(st) || "";
    const cLogo = `
      <div class="section-title">🖼️ לוגו המספרה</div>
      <div class="card">
        <div class="logo-set">
          <div class="logo-preview${logo ? " has-img" : ""}">${logo
            ? `<img src="${esc(logo)}" alt="לוגו">`
            : esc((st.shop.name || "מ")[0])}</div>
          <div class="logo-set-body">
            <div class="hint" style="margin:0 0 10px">העלו תמונת לוגו — היא תופיע בראש העמוד, אצלכם ואצל הלקוחות.</div>
            <div class="btn-row">
              <button class="btn btn-primary btn-sm" data-act="logo-pick">${logo ? "החלפת לוגו" : "העלאת לוגו"}</button>
              ${logo ? `<button class="btn btn-danger btn-sm" data-act="logo-remove">הסרה</button>` : ""}
            </div>
            <input type="file" accept="image/*" data-logofile style="display:none">
          </div>
        </div>
      </div>`;
    const cCover = `
      <div class="section-title">🌄 תמונת נושא (קאבר)</div>
      <div class="card">
        <div class="cover-preview${cover ? " has-img" : ""}">${cover ? `<img src="${esc(cover)}" alt="קאבר">` : "🌄 אין תמונת נושא"}</div>
        <div class="hint" style="margin:11px 0">תמונה רחבה שתופיע בראש עמוד ההזמנה של הלקוחות — נותנת מראה מקצועי.</div>
        <div class="btn-row">
          <button class="btn btn-primary btn-sm" data-act="cover-pick">${cover ? "החלפת תמונה" : "העלאת תמונה"}</button>
          ${cover ? `<button class="btn btn-danger btn-sm" data-act="cover-remove">הסרה</button>` : ""}
        </div>
        <input type="file" accept="image/*" data-coverfile style="display:none">
      </div>`;
    const cLink = `
      <div class="section-title">🔗 הקישור שלך ללקוחות</div>
      <div class="card">
        <div class="hint" style="margin-bottom:10px">שלחו את הקישור הזה ללקוחות — הוא פותח את המספרה שלכם:</div>
        <div style="word-break:break-all;font-weight:700;font-size:13.5px;color:var(--sky-2)">${esc(clientLink())}</div>
        <div class="btn-row" style="margin-top:12px">
          <button class="btn btn-sm" data-act="copy-link">📋 העתקה</button>
          <button class="btn btn-sm" data-act="share-app">🔗 שיתוף</button>
          <button class="btn btn-wa btn-sm" data-act="share-wa"><svg class="wa-ico" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M.057 24l1.687-6.163a11.867 11.867 0 01-1.587-5.945C.16 5.335 5.495 0 12.05 0a11.82 11.82 0 018.413 3.488 11.824 11.824 0 013.48 8.414c-.003 6.557-5.338 11.892-11.893 11.892a11.9 11.9 0 01-5.688-1.448L.057 24zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884-.001 2.225.651 3.891 1.746 5.634l-.999 3.648 3.742-.981zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.767.967-.94 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372s-1.04 1.016-1.04 2.479 1.065 2.876 1.213 3.074c.149.198 2.095 3.2 5.076 4.487.709.306 1.263.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.695.248-1.29.173-1.414z"/></svg>וואטסאפ</button>
        </div>
      </div>`;
    const cQr = qrShareCard();
    const cStyle = `
      <div class="section-title">🎨 סגנון העיצוב</div>
      <div class="card">
        <p class="hint" style="margin-top:0;margin-bottom:12px">כך ייראה האתר — אצלכם ואצל הלקוחות.</p>
        <div class="style-picker">${WIZ_STYLES.map((s) => `
          <button type="button" class="style-opt ${((st.shop.style || "sky") === s.id) ? "selected" : ""}" data-act="set-style" data-style="${s.id}">
            <span class="style-swatch" style="background:linear-gradient(145deg, ${s.c1}, ${s.c2})">${s.emoji}</span>
            <span class="so-body"><span class="so-name">${esc(s.name)}</span><span class="hint" style="display:block">${esc(s.desc)}</span></span>
            <span class="so-check">✓</span>
          </button>`).join("")}</div>
      </div>`;
    const cStaff = `
      <div class="section-title">🧑‍🔧 ספרים במספרה</div>
      <div class="card">
        <p class="hint" style="margin-top:0;margin-bottom:${(st.shop.staff || []).length ? "10px" : "12px"}">${(st.shop.staff || []).length
          ? "הלקוחות יכולים לבקש ספר מסוים בעת ההזמנה (בקשה בלבד — לא התחייבות)."
          : "אין ספרים מוגדרים. הוסיפו שמות כדי לאפשר ללקוח לבחור ספר מועדף."}</p>
        ${(st.shop.staff || []).length ? `<div class="staff-chips">${st.shop.staff.map((n) => `<span class="staff-chip">🧑 ${esc(n)}</span>`).join("")}</div>` : ""}
        <button class="btn btn-sm" data-act="edit-staff" style="margin-top:12px">${(st.shop.staff || []).length ? "עריכת רשימת הספרים" : "＋ הוספת ספרים"}</button>
      </div>`;
    const cSecurity = ownerSecuritySection(st);
    const cGallery = ownerGallerySection();
    const cClientShow = `
      <div class="section-title">👁️ מה מוצג בעמוד הלקוח</div>
      <div class="card">
        <p class="hint" style="margin-top:0;margin-bottom:12px">בחרו אילו מקטעים הלקוחות יראו. כיבוי מקטע מסתיר אותו מהלקוחות (לא מוחק כלום).</p>
        ${[
          ["showReviews", "⭐", "ביקורות"],
          ["showGallery", "🖼️", "גלריית תספורות"],
          ["showProducts", "🛍️", "מוצרים למכירה"],
          ["showHours", "🕒", "שעות פעילות"],
          ["showShare", "📣", "כפתור שיתוף המספרה"],
        ].map(([key, ico, label]) => `
          <label class="ab-custom cli-show-row">
            <input type="checkbox" id="set-${key}" ${cShow(st, key) ? "checked" : ""}>
            <span>${ico} ${esc(label)}</span>
          </label>`).join("")}
        <button class="btn btn-primary" data-act="save-settings" style="margin-top:14px">שמירה</button>
      </div>`;
    // בלוק הרשתות החברתיות — משותף לכרטיס השטוח ולכרטיס המסודר
    const socialBlock = `
        <div class="field"><label>רשתות חברתיות <span class="opt">(לא חובה)</span></label>
          <div class="hint" style="margin:0 0 10px">מלאו רק את מה שיש לכם. הלקוחות יראו אייקונים לחיצים בעמוד ההזמנה — לחיצה תפתח את העמוד שלכם ברשת.</div>
          ${SOCIAL_PLATFORMS.map((p) => `
            <div class="soc-set-row">
              <label class="soc-set-lbl">${p.emoji} ${esc(p.label)}</label>
              <input class="input" id="set-${p.key}" value="${esc(st.shop[p.key] || "")}" placeholder="${esc(p.placeholder)}" autocapitalize="off" autocomplete="off" spellcheck="false" inputmode="latin">
              <div class="ig-help">
                <span class="ig-prev" id="prev-set-${p.key}">${esc(p.previewPrefix)}<b>${esc(socialHandle(st.shop[p.key] || "", p.key) || "השם-שלך")}</b></span>
                <button type="button" class="btn btn-sm" data-act="soc-test" data-p="${p.key}" data-src="#set-${p.key}">פתחו לבדיקה ↗</button>
              </div>
            </div>`).join("")}
        </div>`;
    // הגדרות תזכורות ומרווחי תורים — משותפות (בכרטיס נפרד במבנה המסודר)
    const bookingFields = `
        <div class="field"><label>מרווח בין תורים</label>
          <select class="input" id="set-step">
            ${[30, 45, 60].map((n) => `<option value="${n}" ${st.shop.slotStep === n ? "selected" : ""}>${n} דקות</option>`).join("")}
          </select></div>
        <div class="field"><label>שליחת תזכורת ללקוח — כמה זמן לפני התור</label>
          <select class="input" id="set-remind">
            ${[30, 60, 90, 120].map((n) => `<option value="${n}" ${st.shop.reminderMinutes === n ? "selected" : ""}>${n} דקות לפני</option>`).join("")}
          </select>
        </div>
        <label class="ab-custom">
          <input type="checkbox" id="set-remind-day" ${st.shop.remindDayBefore !== false ? "checked" : ""}>
          <span>לשלוח גם תזכורת יום לפני התור</span>
        </label>
        <p class="hint" style="margin:4px 0 14px">תזכורת נוספת שנשלחת כיממה מראש — מקטינה ביטולים ואי-הגעות.</p>
        <div class="field"><label>סגירת ההרשמה לפני התור</label>
          <select class="input" id="set-hidefree">
            <option value="0" ${!Number(st.shop.hideFreeBeforeMin) ? "selected" : ""}>ללא — אפשר להזמין עד הרגע האחרון</option>
            ${[30, 60, 90, 120, 180, 240, 360, 720].map((n) => `<option value="${n}" ${Number(st.shop.hideFreeBeforeMin) === n ? "selected" : ""}>${n >= 60 ? (n % 60 ? (n / 60).toFixed(1) : n / 60) + " שעות לפני" : n + " דקות לפני"}</option>`).join("")}
          </select>
          <p class="hint" style="margin:6px 0 0">תור שנשאר פנוי כשנותר פחות מהזמן הזה ייעלם ממסך הלקוחות. למשל: אם תבחרו שעה, ותור של 14:00 עדיין פנוי ב-13:00 — הוא לא יוצג יותר. אצלכם ביומן הוא ממשיך להופיע, ותוכלו לרשום אליו לקוח מזדמן.</p>
        </div>`;
    // כרטיס פרטי העסק (זהות) — בלי הגדרות התורים
    const cBusinessInfo = `
      <div class="section-title">📇 פרטי העסק</div>
      <div class="card">
        <div class="field"><label>שם העסק</label>
          <input class="input" id="set-name" value="${esc(st.shop.name)}"></div>
        <div class="field"><label>תיאור קצר</label>
          <input class="input" id="set-tag" value="${esc(st.shop.tagline || "")}"></div>
        <div class="field"><label>קצת עלינו (יוצג ללקוחות בעמוד ההזמנה)</label>
          <textarea class="input" id="set-about" rows="3" placeholder="ספרו על המספרה — ותק, התמחות, אווירה…" style="resize:vertical;line-height:1.6">${esc(st.shop.about || "")}</textarea></div>
        ${socialBlock}
        <div class="field"><label>כתובת המספרה (לכפתור ״איך מגיעים״)</label>
          <input class="input" id="set-addr" value="${esc(st.shop.address || "")}" placeholder="רבי טרפון 12, ירושלים"></div>
        <div class="field"><label>טלפון</label>
          <input class="input" id="set-phone" type="tel" value="${esc(st.shop.phone || "")}"></div>
        <button class="btn btn-primary" data-act="save-settings">שמירת הגדרות</button>
      </div>`;
    // כרטיס תורים ותזכורות — במבנה המסודר בלבד
    const cBooking = `
      <div class="section-title">⏰ תורים ותזכורות</div>
      <div class="card">
        ${bookingFields}
        <button class="btn btn-primary" data-act="save-settings" style="margin-top:12px">שמירת הגדרות</button>
      </div>`;
    // כרטיס פרטי העסק המקורי (שטוח) — זהות + תורים יחד, לשאר המספרות
    const cBusiness = `
      <div class="section-title">📇 פרטי העסק</div>
      <div class="card">
        <div class="field"><label>שם העסק</label>
          <input class="input" id="set-name" value="${esc(st.shop.name)}"></div>
        <div class="field"><label>תיאור קצר</label>
          <input class="input" id="set-tag" value="${esc(st.shop.tagline || "")}"></div>
        <div class="field"><label>קצת עלינו (יוצג ללקוחות בעמוד ההזמנה)</label>
          <textarea class="input" id="set-about" rows="3" placeholder="ספרו על המספרה — ותק, התמחות, אווירה…" style="resize:vertical;line-height:1.6">${esc(st.shop.about || "")}</textarea></div>
        ${socialBlock}
        <div class="field"><label>כתובת המספרה (לכפתור ״איך מגיעים״)</label>
          <input class="input" id="set-addr" value="${esc(st.shop.address || "")}" placeholder="רבי טרפון 12, ירושלים"></div>
        <div class="field"><label>טלפון</label>
          <input class="input" id="set-phone" type="tel" value="${esc(st.shop.phone || "")}"></div>
        ${bookingFields}
        <button class="btn btn-primary" data-act="save-settings">שמירת הגדרות</button>
      </div>`;
    const cInstall = installSettingsCard();
    const cNotif = `
      <div class="section-title">🔔 התראות</div>
      <div class="card">
        <div class="conn-line" style="margin-bottom:12px">
          <span class="conn-dot ${Notify.permission() === "granted" ? "" : "local"}"></span>
          ${Notify.permission() === "granted" ? "התראות פעילות — תקבל הודעה על כל תור חדש" : "התראות כבויות"}
        </div>
        <button class="btn" data-act="enable-notif">${Notify.permission() === "granted" ? "בדיקת התראה" : "אפשר קבלת התראות על תורים חדשים"}</button>
      </div>`;
    const cSupport = supportCard();
    const cConnection = `
      <div class="section-title">🔌 חיבור וגרסה</div>
      <div class="card">
        <div class="conn-line">
          <span class="conn-dot ${Store.mode === "cloud" ? "" : "local"}"></span>
          ${Store.mode === "cloud" ? "מחובר לענן (Firebase) — סנכרון מלא בין כל המכשירים" : "מצב מקומי — לסנכרון בין מכשירים ראו את קובץ README"}
        </div>
        <div class="conn-line" style="margin-top:10px">
          <span class="conn-dot"></span>גרסה ${APP_VERSION}
        </div>
        <button class="btn btn-sm" data-act="force-update" style="margin-top:12px">🔄 בדיקת עדכון</button>
      </div>`;
    const cBackup = `
      <div class="section-title">💾 גיבוי הנתונים</div>
      <div class="card">
        <p class="hint" style="margin-top:0">הורידו עותק של כל נתוני המספרה — תורים, לקוחות, שירותים, מוצרים ושעות פעילות. שמרו אותו במקום בטוח. מומלץ לגבות אחת לחודש.</p>
        <div class="btn-row" style="margin-top:12px">
          <button class="btn btn-primary btn-sm" data-act="backup-download">⬇️ הורדת גיבוי</button>
          <button class="btn btn-sm" data-act="backup-download-full">⬇️ כולל תמונות</button>
        </div>
        <p class="hint" style="margin:12px 0 0">שחזור מגיבוי <b>דורס</b> את כל הנתונים הנוכחיים במספרה.</p>
        <button class="btn btn-sm" data-act="backup-restore" style="margin-top:10px">⬆️ שחזור מקובץ גיבוי</button>
        <input type="file" accept="application/json,.json" data-backupfile style="display:none">
      </div>`;
    const cLogout = `
      <div class="section-title">🚪 יציאה</div>
      <div class="card">
        <p class="hint" style="margin-top:0">יציאה מהניהול במכשיר הזה — שימושי במכשיר משותף או להחלפת מספרה. הנתונים נשמרים; כדי להיכנס שוב צריך את הכתובת והסיסמה.</p>
        <button class="btn btn-danger" data-act="owner-logout" style="margin-top:12px">🚪 יציאה / החלפת מספרה</button>
      </div>`;
    const cDanger = `
      <div class="section-title" style="color:var(--bad)">⚠️ אזור מסוכן</div>
      <div class="card danger-zone">
        <p class="hint" style="margin-top:0">מחיקת המספרה תמחק <b>לצמיתות</b> את כל התורים, הלקוחות, התמונות, המוצרים וההגדרות. הקישור שלכם יתפנה ואחרים יוכלו לקחת אותו. <b>אי אפשר לשחזר.</b></p>
        <button class="btn btn-danger" data-act="delete-shop" style="margin-top:12px">🗑️ מחיקת המספרה לצמיתות</button>
      </div>`;
    const footer = `
      <p class="hint" style="text-align:center;margin-top:20px">
        <a href="privacy.html" target="_blank" rel="noopener" style="color:var(--muted)">מדיניות פרטיות</a>
        · <a href="terms.html" target="_blank" rel="noopener" style="color:var(--muted)">תנאי שימוש</a>
        · BarberTor
      </p>`;

    // מבנה מסודר (כרגע "try"): קטגוריות → פריטים → ההגדרה עצמה (שלוש רמות)
    if (tidyOwner()) {
      // כרטיס עריכה לשדה בודד (רמה שלישית)
      const editCard = (title, inner, hint) => `
        <div class="section-title">${title}</div>
        <div class="card">
          ${inner}
          ${hint ? `<p class="hint" style="margin:10px 0 0">${hint}</p>` : ""}
          <button class="btn btn-primary" data-act="save-settings" style="margin-top:14px">שמירה</button>
        </div>`;
      const hideFreeLabel = (n) => !Number(n) ? "ללא"
        : (n >= 60 ? (n % 60 ? (n / 60).toFixed(1) : n / 60) + " שעות לפני" : n + " דקות לפני");
      const styleName = (WIZ_STYLES.find((s) => s.id === (st.shop.style || "sky")) || {}).name || "";
      const socCount = SOCIAL_PLATFORMS.filter((p) => socialHandle(st.shop[p.key] || "", p.key)).length;

      /* מבנה ההגדרות. לכל פריט: אייקון, צבע, כותרת, ערך נוכחי (val),
         ותוכן העריכה (card) — או type:"toggle" למתג ישיר בשורה. */
      const setPages = {
        business: [
          { id: "name", ico: "🏷️", color: "#0ea5e9", label: "שם העסק", val: st.shop.name,
            card: editCard("🏷️ שם העסק", `<input class="input" id="set-name" value="${esc(st.shop.name)}">`, "השם שהלקוחות רואים בראש עמוד ההזמנה.") },
          { id: "tagline", ico: "💬", color: "#38bdf8", label: "תיאור קצר", val: st.shop.tagline || "—",
            card: editCard("💬 תיאור קצר", `<input class="input" id="set-tag" value="${esc(st.shop.tagline || "")}" placeholder="למשל: תספורות גברים">`, "שורה קצרה שמופיעה מתחת לשם המספרה.") },
          { id: "about", ico: "📝", color: "#8b5cf6", label: "קצת עלינו", val: (st.shop.about || "").trim() ? "מולא" : "—",
            card: editCard("📝 קצת עלינו", `<textarea class="input" id="set-about" rows="4" placeholder="ספרו על המספרה — ותק, התמחות, אווירה…" style="resize:vertical;line-height:1.6">${esc(st.shop.about || "")}</textarea>`, "יוצג ללקוחות בעמוד ההזמנה.") },
          { id: "social", ico: "🌐", color: "#ec4899", label: "רשתות חברתיות", val: socCount ? socCount + " מולאו" : "—",
            card: `<div class="section-title">🌐 רשתות חברתיות</div><div class="card">${socialBlock}<button class="btn btn-primary" data-act="save-settings" style="margin-top:6px">שמירה</button></div>` },
          { id: "address", ico: "📍", color: "#22c55e", label: "כתובת המספרה", val: st.shop.address || "—",
            card: editCard("📍 כתובת המספרה", `<input class="input" id="set-addr" value="${esc(st.shop.address || "")}" placeholder="רבי טרפון 12, ירושלים">`, "משמשת לכפתור ״איך מגיעים״ בעמוד הלקוח.") },
          { id: "phone", ico: "📞", color: "#14b8a6", label: "טלפון", val: st.shop.phone || "—",
            card: editCard("📞 טלפון", `<input class="input" id="set-phone" type="tel" value="${esc(st.shop.phone || "")}">`, "מוצג ללקוחות, ומשמש גם לפניות בוואטסאפ על מוצרים.") },
          { id: "staff", ico: "🧑‍🔧", color: "#6366f1", label: "ספרים במספרה", val: (st.shop.staff || []).length ? (st.shop.staff || []).length + " ספרים" : "—", card: cStaff },
        ],
        booking: [
          { id: "step", ico: "⏱️", color: "#f59e0b", label: "מרווח בין תורים", val: (st.shop.slotStep || 45) + " דקות",
            card: editCard("⏱️ מרווח בין תורים", `<select class="input" id="set-step">${[30, 45, 60].map((n) => `<option value="${n}" ${st.shop.slotStep === n ? "selected" : ""}>${n} דקות</option>`).join("")}</select>`, "כל כמה זמן מתחיל תור חדש ביומן.") },
          { id: "remind", ico: "⏰", color: "#ef4444", label: "תזכורת לפני התור", val: (st.shop.reminderMinutes || 60) + " דקות לפני",
            card: editCard("⏰ תזכורת לפני התור", `<select class="input" id="set-remind">${[30, 60, 90, 120].map((n) => `<option value="${n}" ${st.shop.reminderMinutes === n ? "selected" : ""}>${n} דקות לפני</option>`).join("")}</select>`, "מתי תישלח ללקוח התראה על התור המתקרב.") },
          { id: "remindDay", type: "toggle", key: "remindDayBefore", ico: "📅", color: "#8b5cf6",
            label: "תזכורת יום לפני", sub: "תזכורת נוספת כיממה מראש — מקטינה ביטולים", on: st.shop.remindDayBefore !== false },
          { id: "hidefree", ico: "🚫", color: "#64748b", label: "סגירת ההרשמה", val: hideFreeLabel(st.shop.hideFreeBeforeMin),
            card: editCard("🚫 סגירת ההרשמה לפני התור",
              `<select class="input" id="set-hidefree"><option value="0" ${!Number(st.shop.hideFreeBeforeMin) ? "selected" : ""}>ללא — אפשר להזמין עד הרגע האחרון</option>${[30, 60, 90, 120, 180, 240, 360, 720].map((n) => `<option value="${n}" ${Number(st.shop.hideFreeBeforeMin) === n ? "selected" : ""}>${hideFreeLabel(n)}</option>`).join("")}</select>`,
              "תור שנשאר פנוי כשנותר פחות מהזמן הזה ייעלם ממסך הלקוחות. אצלכם ביומן הוא ממשיך להופיע, ותוכלו לרשום אליו לקוח מזדמן.") },
        ],
        brand: [
          { id: "logo", ico: "🖼️", color: "#0ea5e9", label: "לוגו המספרה", val: shopLogo(st) ? "הועלה" : "—", card: cLogo },
          { id: "cover", ico: "🌄", color: "#22c55e", label: "תמונת נושא", val: shopCover(st) ? "הועלתה" : "—", card: cCover },
          { id: "style", ico: "🎨", color: "#ec4899", label: "סגנון העיצוב", val: styleName, card: cStyle },
        ],
        client: [
          { id: "showReviews", type: "toggle", key: "showReviews", ico: "⭐", color: "#f59e0b", label: "ביקורות", sub: "הצגת ביקורות בעמוד הלקוח", on: cShow(st, "showReviews") },
          { id: "showGallery", type: "toggle", key: "showGallery", ico: "🖼️", color: "#0ea5e9", label: "גלריית תספורות", sub: "הצגת הגלריה בעמוד הלקוח", on: cShow(st, "showGallery") },
          { id: "showProducts", type: "toggle", key: "showProducts", ico: "🛍️", color: "#22c55e", label: "מוצרים למכירה", sub: "הצגת המוצרים בעמוד הלקוח", on: cShow(st, "showProducts") },
          { id: "showHours", type: "toggle", key: "showHours", ico: "🕒", color: "#8b6f47", label: "שעות פעילות", sub: "הצגת שעות הפעילות בעמוד הלקוח", on: cShow(st, "showHours") },
          { id: "showShare", type: "toggle", key: "showShare", ico: "📣", color: "#ef4444", label: "כפתור שיתוף", sub: "הצגת כרטיס השיתוף בעמוד הלקוח", on: cShow(st, "showShare") },
          { id: "gallery", ico: "📷", color: "#6366f1", label: "ניהול הגלריה", val: Store.getGallery().length + " תמונות", card: cGallery },
        ],
        alerts: [
          { id: "notif", ico: "🔔", color: "#f97316", label: "התראות", val: Notify.permission() === "granted" ? "פעילות" : "כבויות", card: cNotif },
          { id: "security", ico: "🔒", color: "#22c55e", label: "אבטחת החשבון", val: (st.shop && st.shop.ownerUid) ? "מאובטח" : "לא מאובטח", card: cSecurity },
        ].concat(cInstall ? [{ id: "install", ico: "📲", color: "#0ea5e9", label: "התקנה על מסך הבית", val: "", card: cInstall }] : []),
        tools: [
          { id: "backup", ico: "💾", color: "#0ea5e9", label: "גיבוי הנתונים", val: "", card: cBackup },
          { id: "connection", ico: "🔌", color: "#64748b", label: "חיבור וגרסה", val: "גרסה " + APP_VERSION, card: cConnection },
        ].concat(cSupport ? [{ id: "support", ico: "🛟", color: "#22c55e", label: "תמיכה", val: "", card: cSupport }] : []),
        account: [
          { id: "logout", ico: "🚪", color: "#f59e0b", label: "יציאה מהניהול", val: "", card: cLogout },
          { id: "danger", ico: "🗑️", color: "#ef4444", label: "מחיקת המספרה", val: "", card: cDanger },
        ],
      };

      const backBtn = (act, label) => `<button class="btn btn-ghost btn-sm home-back" data-act="${act}">‹ ${label}</button>`;

      // רמה 3 — ההגדרה עצמה
      if (view.settingsPage && view.settingsItem) {
        const item = (setPages[view.settingsPage] || []).find((x) => x.id === view.settingsItem);
        if (item && item.card) return backBtn("settings-item-back", "חזרה") + item.card + footer;
        view.settingsItem = null;
      }

      // שורה בעיצוב אחיד: אייקון צבעוני + כותרת + ערך/מתג + חץ
      const rowInner = (ico, color, label, sub) => `
        <span class="sr-ico" style="background:${color}">${ico}</span>
        <span class="sr-body"><span class="sr-label">${esc(label)}</span>${sub ? `<span class="sr-sub">${esc(sub)}</span>` : ""}</span>`;
      const navRow = (nav, ico, color, label, sub, val) => `
        <button class="set-row" ${nav}>${rowInner(ico, color, label, sub)}
          ${val ? `<span class="sr-val">${esc(val)}</span>` : ""}
          <span class="sr-chev">‹</span>
        </button>`;
      const toggleRow = (it) => `
        <div class="set-row set-row-static">${rowInner(it.ico, it.color, it.label, it.sub)}
          <label class="switch">
            <input type="checkbox" data-settoggle="${it.key}" ${it.on ? "checked" : ""}>
            <span class="track"></span><span class="thumb"></span>
          </label>
        </div>`;

      // רמה 2 — פריטי הקטגוריה
      if (view.settingsPage && setPages[view.settingsPage]) {
        const items = setPages[view.settingsPage];
        return backBtn("settings-back", "חזרה להגדרות") + `
          <div class="card set-list">
            ${items.map((it) => it.type === "toggle" ? toggleRow(it)
              : navRow(`data-act="settings-item" data-item="${it.id}"`, it.ico, it.color, it.label, it.sub, it.val)).join("")}
          </div>${footer}`;
      }
      view.settingsPage = null;

      // רמה 1 — רשימת הקטגוריות
      const pageRow = (page, ico, color, label, sub) => navRow(`data-act="settings-page" data-page="${page}"`, ico, color, label, sub, "");
      const tabRow = (tab, ico, color, label, sub) => navRow(`data-otab="${tab}"`, ico, color, label, sub, "");
      return `
        <div class="section-title">🗂️ ניהול יומי</div>
        <div class="card set-list">
          ${tabRow("hours", "🕐", "#8b6f47", "שעות פעילות", "ימים ושעות עבודה, חופשות")}
          ${tabRow("services", "✂️", "#0ea5e9", "שירותים", "שמות, מחירים ומשכי זמן")}
          ${tabRow("products", "🛍️", "#22c55e", "מוצרים", "מוצרים למכירה בעמוד הלקוח")}
          ${tabRow("clients", "👥", "#6366f1", "לקוחות", "ספר הלקוחות וחסימות")}
          ${tabRow("report", "📊", "#a855f7", "דוח והכנסות", "סיכום חודשי וביקורות")}
          ${tabRow("publish", "📣", "#ef4444", "פרסום וקישור", "הקישור, QR והודעה ללקוחות")}
        </div>

        <div class="section-title">⚙️ הגדרות המספרה</div>
        <div class="card set-list">
          ${pageRow("business", "📇", "#0ea5e9", "פרטי העסק", "שם, תיאור, רשתות, כתובת וטלפון")}
          ${pageRow("booking", "⏰", "#f59e0b", "תורים ותזכורות", "מרווח, תזכורות וסגירת הרשמה")}
          ${pageRow("brand", "🎨", "#ec4899", "מיתוג ועיצוב", "לוגו, תמונת נושא וסגנון")}
          ${pageRow("client", "👁️", "#14b8a6", "עמוד הלקוח", "מה מוצג ללקוחות + גלריה")}
        </div>

        <div class="card set-list">
          ${pageRow("alerts", "🔔", "#f97316", "התראות ואבטחה", "התראות פוש ואבטחת חשבון")}
          ${pageRow("tools", "🛠️", "#64748b", "כלים ותחזוקה", "גיבוי, גרסה ותמיכה")}
          ${pageRow("account", "🚪", "#ef4444", "חשבון", "יציאה ומחיקת המספרה")}
        </div>
        ${footer}`;
    }

    // מבנה שטוח מקורי — לכל שאר המספרות (סדר זהה למקור)
    return cLogo + cCover + cLink + cQr + cStyle + cStaff + cSecurity + cGallery +
      cClientShow + cBusiness + cInstall + cNotif + cSupport + cConnection +
      cBackup + cLogout + cDanger + footer;
  }

  function confirmOwnerLogout() {
    openModal(`
      <div class="m-title">יציאה מהמערכת</div>
      <div class="m-sub">תצאו מהניהול ותחזרו למסך הפתיחה</div>
      <p style="font-size:14px;color:var(--muted);margin:6px 0 20px">המספרה וכל הנתונים נשמרים. כדי להיכנס שוב תצטרכו את הכתובת האישית והסיסמה.</p>
      <button class="btn btn-danger" data-act="do-owner-logout">כן, יציאה</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>
    `);
  }

  /* ---------- מחיקת נתוני לקוח ----------
     מבטל תורים עתידיים, מוחק ביקורות/רשימת המתנה ומנקה את הזיהוי במכשיר.
     תורים שכבר התקיימו נשארים אצל הספר כרישום עסקי — כך גם מוסבר ללקוח. */
  function confirmDeleteMyData() {
    const st = Store.get();
    const now = Date.now();
    const future = (st.bookings || []).filter((b) =>
      b.userId === identity.userId && b.status !== "cancelled" &&
      u.dateTime(b.date, b.start).getTime() > now);
    openModal(`
      <div class="m-title">מחיקת הנתונים שלי</div>
      <div class="m-sub">${esc(st.shop.name || "")}</div>
      <p style="font-size:14px;margin:14px 0 0">יימחקו:</p>
      <ul style="font-size:14px;color:var(--muted);margin:8px 20px 0;line-height:1.9">
        <li>הפרטים שלכם במכשיר הזה (שם, טלפון, אימייל)</li>
        <li>${future.length} תורים עתידיים — יבוטלו</li>
        <li>הביקורות שכתבתם והמתנות לתור שהתפנה</li>
      </ul>
      <p class="hint" style="margin:14px 0 0;line-height:1.7">תורים שכבר התקיימו נשארים ביומן של הספר כרישום עסקי. להסרתם פנו ישירות למספרה או במייל שבמדיניות הפרטיות.</p>
      <button class="btn btn-danger" data-act="do-delete-my-data" style="margin-top:18px">כן, מחקו את הנתונים שלי</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>
    `);
  }

  async function doDeleteMyData() {
    const btn = $("[data-act='do-delete-my-data']"); if (btn) { btn.disabled = true; btn.textContent = "מוחק…"; }
    const st = Store.get();
    const now = Date.now();
    const uid = identity.userId;
    try {
      // ביטול כל התורים העתידיים
      const future = (st.bookings || []).filter((b) =>
        b.userId === uid && b.status !== "cancelled" &&
        u.dateTime(b.date, b.start).getTime() > now);
      for (const b of future) {
        if (clientCancelSeen) clientCancelSeen.add(b.id);
        await Store.setBookingStatus(b.id, "cancelled", "client");
      }
      // ביקורות + רשימת המתנה + התראות שממתינות
      await Store.purgeClient(uid);
    } catch (e) {}
    // ניקוי מקומי
    try {
      localStorage.removeItem("ug_identity");
      localStorage.removeItem(PRIVACY_KEY);
      localStorage.removeItem("ug_ctab__" + SHOP);
      sessionStorage.removeItem("ug_gauth");
    } catch (e) {}
    try { if (UG.Auth) await UG.Auth.signOut(); } catch (e) {}
    closeModal();
    location.reload();
  }

  /* ---------- ייצוא הדוח לאקסל ----------
     קובץ CSV עם BOM של UTF-8 — כך אקסל מזהה עברית נכון ופותח אותו בלחיצה. */
  function csvCell(v) {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }
  function downloadFile(name, text, mime) {
    try {
      const blob = new Blob([text], { type: (mime || "text/plain") + ";charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = name;
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      return true;
    } catch (e) { return false; }
  }
  function exportReportCsv() {
    const st = Store.get();
    const ym = view.statMonth || ymNow();
    const rows = st.bookings
      .filter((b) => b.status === "confirmed" && b.date.startsWith(ym))
      .map((b) => ({ b, ts: u.dateTime(b.date, b.start).getTime() }))
      .sort((a, z) => a.ts - z.ts)
      .map((x) => x.b);
    if (!rows.length) { toast("אין נתונים לייצוא בחודש זה", "", "📊"); return; }
    const head = ["תאריך", "שעה", "לקוח", "טלפון", "שירות", "ספר", "מחיר"];
    const lines = [head.map(csvCell).join(",")];
    let total = 0;
    rows.forEach((b) => {
      total += Number(b.price || 0);
      lines.push([
        b.date, b.start, bkName(b) || "לקוח", bkPhone(b) || "", b.serviceName || "",
        b.staff || "", Number(b.price || 0),
      ].map(csvCell).join(","));
    });
    lines.push("");
    lines.push([csvCell("סה״כ " + rows.length + " תספורות"), "", "", "", "", "", total].join(","));
    const name = "barbertor-" + SHOP + "-" + ym + ".csv";
    const ok = downloadFile(name, "﻿" + lines.join("\r\n"), "text/csv");
    toast(ok ? "הדוח הורד — נפתח באקסל ✓" : "הייצוא נכשל", ok ? "good" : "", ok ? "📊" : "⚠️");
  }

  /* ---------- גיבוי ושחזור ---------- */
  let pendingBackup = null;   // הגיבוי שנבחר, ממתין לאישור הדריסה

  function downloadBackup(withGallery) {
    let dump;
    try { dump = Store.exportData(!!withGallery); }
    catch (e) { toast("ההורדה נכשלה", "", "⚠️"); return; }
    const name = "barbertor-" + SHOP + "-" + u.dateKey(new Date()) + ".json";
    const ok = downloadFile(name, JSON.stringify(dump, null, 2), "application/json");
    toast(ok ? "הגיבוי הורד ✓" : "ההורדה נכשלה", ok ? "good" : "", ok ? "💾" : "⚠️");
  }

  async function handleBackupFile(file) {
    if (!file) return;
    let dump;
    try { dump = JSON.parse(await file.text()); }
    catch (e) { toast("הקובץ אינו JSON תקין", "", "⚠️"); return; }
    if (!dump || dump.format !== "barbertor-backup") {
      toast("הקובץ אינו גיבוי של BarberTor", "", "⚠️"); return;
    }
    const when = dump.exportedAt ? u.longDate(u.dateKey(new Date(dump.exportedAt))) : "לא ידוע";
    const bk = (dump.state && dump.state.bookings || []).length;
    const from = dump.shopId && dump.shopId !== SHOP
      ? `<p class="hint" style="color:var(--bad);margin:8px 0 0">⚠️ הגיבוי הזה שייך למספרה אחרת (${esc(dump.shopId)}).</p>` : "";
    pendingBackup = dump;
    openModal(`
      <div class="m-title">שחזור מגיבוי</div>
      <div class="m-sub">מתאריך ${esc(when)} · ${bk} תורים</div>
      <p style="font-size:14px;color:var(--muted);margin:10px 0 0">כל הנתונים הנוכחיים במספרה יימחקו ויוחלפו בנתונים מהגיבוי. פעולה זו אינה הפיכה.</p>
      ${from}
      <button class="btn btn-danger" data-act="do-restore" style="margin-top:18px">שחזור — דרוס את הנתונים</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>
    `);
  }

  async function doRestore() {
    if (!pendingBackup) { closeModal(); return; }
    const btn = $("[data-act='do-restore']"); if (btn) { btn.disabled = true; btn.textContent = "משחזר…"; }
    const res = await Store.importData(pendingBackup);
    pendingBackup = null;
    closeModal();
    if (!res.ok) { toast(res.reason || "השחזור נכשל", "", "⚠️"); return; }
    toast("הנתונים שוחזרו ✓", "good", "💾");
    render();
  }

  /* ---------- מחיקת המספרה לצמיתות ----------
     שלב אימות בהקלדה (ולא רק "כן") — הפעולה בלתי הפיכה ומוחקת עסק שלם. */
  function confirmDeleteShop() {
    const st = Store.get();
    const bk = (st.bookings || []).filter((b) => b.status !== "cancelled").length;
    const cl = (Store.getContacts()).length;
    openModal(`
      <div class="m-title" style="color:var(--bad)">🗑️ מחיקת המספרה</div>
      <div class="m-sub">${esc(st.shop.name || SHOP)} · ${esc(SHOP)}</div>
      <p style="font-size:14px;margin:14px 0 0">יימחקו לצמיתות:</p>
      <ul style="font-size:14px;color:var(--muted);margin:8px 20px 0;line-height:1.9">
        <li>${bk} תורים${cl ? " ו-" + cl + " אנשי קשר" : ""}</li>
        <li>כל השירותים, המוצרים והתמונות</li>
        <li>שעות הפעילות וההגדרות</li>
        <li>הקישור האישי — יתפנה לאחרים</li>
      </ul>
      <p style="font-size:14px;color:var(--bad);font-weight:700;margin:14px 0 0">אי אפשר לשחזר. מומלץ להוריד גיבוי קודם.</p>
      <button class="btn btn-sm" data-act="backup-download-full" style="margin-top:10px;width:100%">⬇️ הורדת גיבוי עכשיו</button>
      <div class="field" style="margin-top:18px">
        <label>להמשך, הקלידו <b>מחק</b> בתיבה:</label>
        <input class="input" id="del-confirm" placeholder="מחק" autocomplete="off">
      </div>
      <button class="btn btn-danger" data-act="do-delete-shop">מחיקה סופית</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>
    `);
    setTimeout(() => { const el = $("#del-confirm"); if (el) el.focus(); }, 100);
  }

  async function doDeleteShop() {
    const el = $("#del-confirm");
    if (!el || el.value.trim() !== "מחק") {
      toast("הקלידו ״מחק״ כדי לאשר", "", "⌨️");
      if (el) el.focus();
      return;
    }
    const btn = $("[data-act='do-delete-shop']"); if (btn) { btn.disabled = true; btn.textContent = "מוחק…"; }
    const st = Store.get();
    let passHash = "";
    try { if (st.shop.ownerPass) passHash = await sha256Hex(String(st.shop.ownerPass)); } catch (e) {}
    const res = await Store.deleteShop(passHash);
    if (!res.ok) {
      if (btn) { btn.disabled = false; btn.textContent = "מחיקה סופית"; }
      toast(res.reason || "המחיקה נכשלה", "", "⚠️");
      return;
    }
    // ניקוי כל העקבות המקומיים של המספרה הזו
    try {
      localStorage.removeItem(AUTHKEY);
      localStorage.removeItem(ROUTEKEY);
      localStorage.removeItem("ug_ctab__" + SHOP);
      if ((localStorage.getItem("ug_my_shop") || "") === SHOP) localStorage.removeItem("ug_my_shop");
      if ((localStorage.getItem("ug_last_shop") || "") === SHOP) localStorage.removeItem("ug_last_shop");
    } catch (e) {}
    try { if (UG.Auth) await UG.Auth.signOut(); } catch (e) {}
    closeModal();
    location.hash = "";
    location.reload();
  }

  /* =======================================================================
     מצב ריק
     =======================================================================*/
  function emptyState(ico, title, sub) {
    return `<div class="empty"><div class="em-ico">${ico}</div><b>${esc(title)}</b><p>${esc(sub)}</p></div>`;
  }

  /* =======================================================================
     רינדור ראשי
     =======================================================================*/
  /* קונסולידציה — קריאות render() קרובות בזמן מתאחדות לרינדור אחד ב-frame הבא.
     חשוב כי כל mutation מקומית מפעילה גם emit מקומי וגם echo מהשרת → ללא כינוס נקבל
     שני רינדורים מלאים על כל פעולה. rAF גם מסתנכרן עם ציור המסך כך שהמעברים חלקים יותר. */
  let _renderScheduled = false;
  function render() {
    if (_renderScheduled) return;
    _renderScheduled = true;
    requestAnimationFrame(() => { _renderScheduled = false; renderNow(); });
  }
  function renderNow() {
    try { clearTimeout(window.__ugBootFail); } catch (e) {}   // הצגה הצליחה — לבטל את רשת הביטחון
    syncNav();   // הקלטת ניווט ל"אחורה" חכם (לפני הצגת המסך)
    if (view.onboarding) { document.title = "BarberTor — תורים לספרים"; $("#root").innerHTML = renderOnboarding(); return; }
    if (view.notFound) { document.title = "BarberTor"; $("#root").innerHTML = renderNotFound(); return; }
    if (!Store.get()) return;
    // סגנון העיצוב שהספר בחר — חל גם על הלקוחות
    applyShopStyle((Store.get().shop && Store.get().shop.style) || "sky");
    // כותרת לשונית דינמית — שם המספרה של הספר, עם מיתוג BarberTor
    const shopName = (Store.get().shop && Store.get().shop.name) || "BarberTor";
    document.title = shopName + " · BarberTor";
    // שם האייקון במסך הבית באייפון — שם המספרה, כך שהלקוח רואה את המספרה שלו
    try {
      const mt = document.querySelector('meta[name="apple-mobile-web-app-title"]');
      if (mt && shopName) mt.setAttribute("content", shopName);
    } catch (e) {}
    // שמירת הלשונית הנוכחית — כדי שרענון הדף לא יחזיר להתחלה
    if (view.route === "client") { try { localStorage.setItem("ug_ctab__" + SHOP, view.clientTab); } catch (e) {} }
    // גלילת סרגל התפריט התחתון (אצל הבעלים) — נשמרת בין רינדורים כדי שלא תקפוץ
    // להתחלה מעצמה; זזה רק כשהספר עצמו גולל.
    const prevTabbar = $("#otabbar");
    const tabbarScroll = prevTabbar ? prevTabbar.scrollLeft : null;
    // אותו דבר עבור סרגל בחירת הימים (קביעת תור) — בחירת יום גוללת מחדש את כל
    // המסך, ובלי זה הגלילה הצידה של הימים הייתה קופצת בחזרה ליום הראשון.
    const prevDays = $(".days-scroll");
    const daysScroll = prevDays ? prevDays.scrollLeft : null;
    $("#root").innerHTML = view.route === "owner" ? renderOwner() : renderClient();
    const newDays = $(".days-scroll");
    if (newDays && daysScroll !== null) newDays.scrollLeft = daysScroll;
    const newTabbar = $("#otabbar");
    if (newTabbar) {
      if (tabbarScroll !== null) {
        newTabbar.scrollLeft = tabbarScroll;   // נשארים בדיוק איפה שהיו — לא קופצים לבד
      } else {
        // רינדור ראשון (למשל נכנסים ישר ללשונית ״הגדרות״ שנשמרה) — לוודא שהלשונית הפעילה נראית
        const activeBtn = newTabbar.querySelector("button.active");
        if (activeBtn) activeBtn.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
      wireTabbarArrows(newTabbar);
      updateTabbarArrows();
    }
    startTrialTicker();
  }

  /* ---------- חצים בסרגל הבעלים — כדי שהספר ידע שיש עוד לשוניות ---------- */
  function wireTabbarArrows(el) {
    if (!el || el.__arrowsWired) return;
    el.__arrowsWired = true;
    el.addEventListener("scroll", updateTabbarArrows, { passive: true });
  }
  function updateTabbarArrows() {
    const el = $("#otabbar"); if (!el) return;
    const wrap = el.closest(".otabbar-wrap"); if (!wrap) return;
    const max = el.scrollWidth - el.clientWidth;
    if (max <= 4) { wrap.classList.add("at-start", "at-end"); return; }
    const sl = Math.abs(el.scrollLeft);          // בעברית (RTL) scrollLeft עשוי להיות שלילי
    wrap.classList.toggle("at-start", sl < 4);   // בתחילת הגלילה — אין לאן לחזור
    wrap.classList.toggle("at-end", sl > max - 4); // בסוף — אין עוד לשוניות
  }
  function scrollTabbar(dir) {   // dir: "start" | "end"
    const el = $("#otabbar"); if (!el) return;
    const amount = Math.round(el.clientWidth * 0.6);
    const rtl = getComputedStyle(el).direction === "rtl";
    let delta = dir === "end" ? amount : -amount;
    if (rtl) delta = -delta;   // בגלילת RTL הכיוונים הפוכים
    el.scrollBy({ left: delta, behavior: "smooth" });
  }

  /* =======================================================================
     פתיחת מספרה חדשה (רישום ספר) + "מספרה לא נמצאה"
     =======================================================================*/
  /* =======================================================================
     שאלון פתיחת מספרה — חוויית Onboarding (עמוד אחרי עמוד)
     =======================================================================*/
  const WIZ_STYLES = [
    { id: "sky", name: "תכלת מודרני", desc: "נקי, מודרני וקריא", emoji: "💧", c1: "#38bdf8", c2: "#0ea5e9" },
    { id: "gold", name: "זהב יוקרתי", desc: "שחור וזהב — יוקרתי", emoji: "👑", c1: "#e3b341", c2: "#c08a1e" },
    { id: "royal", name: "סגול מלכותי", desc: "נועז וייחודי", emoji: "🔮", c1: "#a78bfa", c2: "#7c3aed" },
  ];
  // החלת סגנון העיצוב של המספרה (נשמר במסד — הלקוחות רואים את אותו סגנון)
  function applyShopStyle(id) {
    const ok = WIZ_STYLES.some((s) => s.id === id);
    document.documentElement.setAttribute("data-style", ok ? id : "sky");
  }

  const wiz = {
    step: 0, busy: false, returnTo: 0,
    data: {
      owner: "", name: "", handle: "", phone: "", city: "", street: "", houseNo: "", address: "",
      services: [{ name: "תספורת גבר", price: 60, durationMin: 30 }],
      multiStaff: false, staff: [""],
      about: "", instagram: "", tiktok: "", facebook: "", youtube: "", logo: "", heardFrom: "",
      privacyOk: false,
      style: "sky", pass: "", pass2: "",
    },
  };

  // "מאיפה הגעת אלינו" — לדעת מאיפה מגיעים ספרים חדשים
  const WIZ_SOURCES = [
    { id: "friend", label: "שמעתי מחבר" },
    { id: "social", label: "ראיתי אתכם ברשתות החברתיות", sub: "אינסטגרם, פייסבוק או טיקטוק" },
    { id: "google", label: "חיפוש בגוגל" },
    { id: "other", label: "אחר" },
  ];
  // הרכבת כתובת מלאה מהשדות הנפרדים (רחוב + מספר, עיר)
  function wizComposeAddress(d) {
    const line1 = [d.street, d.houseNo].filter(Boolean).join(" ").trim();
    return [line1, d.city].filter(Boolean).join(", ").trim();
  }
  const WIZ_QUESTIONS = 13;  // שלבים 1..13 (13 = מסך הסיכום)
  // שלבים שאפשר לדלג עליהם — פרטים שאפשר להשלים אחר כך מההגדרות
  const WIZ_SKIPPABLE = [4, 7, 8, 9, 12];

  // רטט קצר למשוב מגע (נתמך באנדרואיד; באייפון פשוט מתעלם)
  function haptic(ms) { try { if (navigator.vibrate) navigator.vibrate(ms || 12); } catch (e) {} }

  function wizStepHtml() {
    const d = wiz.data;
    const base = shareBase() + "#";
    switch (wiz.step) {
      case 0:
        return `
          <div class="wiz-hero">
            <div class="wiz-logo">💈</div>
            <h1 class="wiz-title">ברוך הבא ל-BarberTor</h1>
            <p class="wiz-lead">מערכת התורים שלך — מוכנה תוך דקה.<br>נשאל אותך כמה שאלות קצרות ונבנה לך הכול.</p>
          </div>
          <div class="wiz-perks">
            <div class="wiz-perk"><span>📅</span><div><b>יומן חכם</b><div class="hint">הלקוחות מזמינים לבד</div></div></div>
            <div class="wiz-perk"><span>💰</span><div><b>הכנסות ובקרת לקוחות</b><div class="hint">רואים כמה הכנסתם, מי הלקוחות וכמה ביקרו</div></div></div>
            <div class="wiz-perk"><span>🔔</span><div><b>תזכורות אוטומטיות</b><div class="hint">פחות ביטולים</div></div></div>
            <div class="wiz-perk"><span>🔗</span><div><b>קישור אישי</b><div class="hint">שולחים ללקוחות</div></div></div>
          </div>`;
      case 1:
        return wizQ("👋", "איך קוראים לך?", "נעים להכיר! ככה נדע איך לפנות אליך.",
          `<input class="input wiz-input" id="wz-owner" placeholder="השם שלך" value="${esc(d.owner)}" autocomplete="given-name">`);
      case 2:
        return wizQ("💈", "איך קוראים למספרה?", "השם שהלקוחות שלך יראו בראש מסך ההזמנה.",
          `<input class="input wiz-input" id="wz-name" placeholder="למשל: מספרת דני" value="${esc(d.name)}">`);
      case 3:
        return wizQ("🔗", "בחר/י כתובת אישית", "זה הקישור שתשלח/י ללקוחות. באנגלית, קצר וקל לזכור.",
          `<input class="input wiz-input" id="wz-handle" placeholder="dani" value="${esc(d.handle)}"
                  autocapitalize="off" autocomplete="off" spellcheck="false" inputmode="latin">
           <div class="wiz-link" id="wz-linkPrev">${esc(base)}<b>${esc(d.handle || "הכתובת-שלך")}</b></div>`);
      case 4:
        return wizQ("📍", "פרטי המספרה", "כדי שהלקוחות ידעו איך להגיע ואיך להתקשר. אפשר לדלג ולמלא אחר כך.",
          `<input class="input wiz-input" id="wz-phone" type="tel" inputmode="tel" placeholder="טלפון · 050-0000000" value="${esc(d.phone)}">
           <div class="field-row wiz-addr-row" style="margin-top:10px">
             <input class="input wiz-input" id="wz-city" placeholder="עיר / יישוב" value="${esc(d.city)}" style="flex:1.1">
             <input class="input wiz-input" id="wz-street" placeholder="רחוב" value="${esc(d.street)}" style="flex:1.3">
             <input class="input wiz-input" id="wz-houseno" placeholder="מס׳" value="${esc(d.houseNo)}" style="flex:.6" inputmode="numeric">
           </div>`);
      case 5:
        return wizQ("✂️", "אילו שירותים אתם מציעים?", "שם, מחיר ומשך — הלקוחות יבחרו מתוך אלה. תמיד אפשר להוסיף ולשנות שירותים אחר כך מלשונית ״שירותים״.",
          `<div class="wiz-svc-head"><span class="h-name">שם השירות</span><span class="h-price">מחיר ₪</span><span class="h-dur">דק׳</span><span class="h-sp"></span></div>
           <div id="wz-svc-list">${(d.services || []).map((s, i) => `
             <div class="wiz-svc-row" data-svc-row="${i}">
               <input class="input sv-name" placeholder="למשל: תספורת גבר" value="${esc(s.name || "")}">
               <input class="input sv-price" type="number" inputmode="numeric" min="0" placeholder="60" value="${esc(s.price)}">
               <input class="input sv-dur" type="number" inputmode="numeric" min="5" step="5" placeholder="30" value="${esc(s.durationMin)}">
               <button type="button" class="sv-del" data-act="wiz-svc-del" data-i="${i}" aria-label="מחיקה">✕</button>
             </div>`).join("")}</div>
           <button type="button" class="btn btn-sm" data-act="wiz-svc-add" style="width:100%;margin-top:6px">＋ הוספת שירות</button>`);
      case 6:
        return wizQ("🧑‍🔧", "כמה ספרים עובדים אצלכם?", "אם יש כמה ספרים, הלקוח יוכל לבקש ספר מסוים בעת הזמנת התור (בקשה בלבד — לא התחייבות).",
          `<div class="staff-mode">
             <button type="button" class="staff-opt ${!d.multiStaff ? "selected" : ""}" data-act="wiz-staff-mode" data-multi="0">
               <span class="stm-emoji">🧑</span><span class="stm-name">ספר יחיד</span><span class="so-check">✓</span></button>
             <button type="button" class="staff-opt ${d.multiStaff ? "selected" : ""}" data-act="wiz-staff-mode" data-multi="1">
               <span class="stm-emoji">🧑‍🤝‍🧑</span><span class="stm-name">כמה ספרים</span><span class="so-check">✓</span></button>
           </div>
           ${d.multiStaff ? `<div id="wz-staff-list" style="margin-top:14px">${(d.staff || [""]).map((n, i) => `
             <div class="wiz-staff-row" data-staff-row="${i}">
               <input class="input st-name" placeholder="שם הספר" value="${esc(n || "")}">
               <button type="button" class="sv-del" data-act="wiz-staff-del" data-i="${i}" aria-label="מחיקה">✕</button>
             </div>`).join("")}
             <button type="button" class="btn btn-sm" data-act="wiz-staff-add" style="width:100%;margin-top:6px">＋ הוספת ספר</button>
           </div>` : ""}`);
      case 7:
        return wizQ("📝", "כמה מילים על העסק", "עוזר ללקוחות להבין מה מייחד אתכם — יופיע בעמוד ההזמנה. אפשר לדלג ולמלא אחר כך.",
          `<textarea class="input wiz-input" id="wz-about" rows="4" maxlength="170"
                     placeholder="למשל: אצלנו תקבלו שירות ייחודי עם מגע אישי ומותאם ללקוח"
                     style="resize:vertical;line-height:1.6">${esc(d.about)}</textarea>
           <div class="wiz-count"><span id="wz-about-n">${(d.about || "").length}</span>/170</div>`);
      case 8:
        return wizQ("🌐", "רשתות חברתיות", "הלקוחות יוכלו לעבור לעמוד שלכם ישירות מדף ההזמנה. מלאו רק את מה שיש לכם — אפשר לדלג על השאר.",
          SOCIAL_PLATFORMS.map((p) => {
            const val = p.key === "instagram" ? d.instagram : (d[p.key] || "");
            return `<div class="field" style="text-align:start">
              <label>${p.emoji} ${esc(p.label)} <span class="opt">(לא חובה)</span></label>
              <input class="input wiz-input" id="wz-${p.key}" placeholder="${esc(p.placeholder)}" value="${esc(val)}"
                     autocapitalize="off" autocomplete="off" spellcheck="false" inputmode="latin">
              <div class="ig-help">
                <span class="ig-prev" id="prev-wz-${p.key}">${esc(p.previewPrefix)}<b>${esc(socialHandle(val, p.key) || "השם-שלך")}</b></span>
                <button type="button" class="btn btn-sm" data-act="soc-test" data-p="${p.key}" data-src="#wz-${p.key}">פתחו לבדיקה ↗</button>
              </div>
            </div>`;
          }).join(""));
      case 9:
        return wizQ("🖼️", "אייקון המספרה", "התמונה שהלקוחות יראו בראש העמוד ובאייקון האפליקציה. אפשר לדלג ולהעלות אחר כך.",
          `<div class="wiz-logo-pick">
             <div class="logo-preview${d.logo ? " has-img" : ""}" id="wz-logo-prev">${d.logo
               ? `<img src="${esc(d.logo)}" alt="לוגו">`
               : esc((d.name || "מ").trim()[0] || "מ")}</div>
             <div class="btn-row" style="margin-top:12px;justify-content:center">
               <button type="button" class="btn btn-sm" data-act="wiz-logo-pick">${d.logo ? "החלפת תמונה" : "העלאת תמונה"}</button>
               ${d.logo ? `<button type="button" class="btn btn-danger btn-sm" data-act="wiz-logo-clear">הסרה</button>` : ""}
             </div>
             <input type="file" accept="image/*" data-wizlogofile style="display:none">
           </div>`);
      case 10:
        return wizQ("🎨", "בחרו סגנון עיצוב", "ככה ייראה האתר שלכם — גם אצלכם וגם אצל הלקוחות. אפשר לשנות בכל רגע מההגדרות.",
          `<div class="style-picker">${WIZ_STYLES.map((s) => `
             <button type="button" class="style-opt ${d.style === s.id ? "selected" : ""}" data-act="wiz-style" data-style="${s.id}">
               <span class="style-swatch" style="background:linear-gradient(145deg, ${s.c1}, ${s.c2})">${s.emoji}</span>
               <span class="so-body"><span class="so-name">${esc(s.name)}</span><span class="hint" style="display:block">${esc(s.desc)}</span></span>
               <span class="so-check">✓</span>
             </button>`).join("")}</div>`);
      case 11:
        return wizQ("🔒", "סיסמת ניהול", "רק איתה נכנסים לנהל את המספרה. שמור/י אותה במקום בטוח!",
          `<div class="pw-field">
             <input class="input wiz-input" id="wz-pass" type="password" placeholder="בחר/י סיסמה" value="${esc(d.pass)}">
             <button type="button" class="pw-eye" data-act="toggle-pw" aria-label="הצג סיסמה">👁️</button>
           </div>
           <input class="input wiz-input" id="wz-pass2" type="password" placeholder="הקלד/י שוב לאימות" value="${esc(d.pass2 || "")}" style="margin-top:10px">`);
      case 12:
        return wizQ("💬", "מאיפה הגעת אלינו?", "שאלה אחרונה — זה עוזר לנו לדעת איפה כדאי לספר על BarberTor.",
          `<div class="src-picker">${WIZ_SOURCES.map((s) => `
             <button type="button" class="src-opt ${d.heardFrom === s.id ? "selected" : ""}" data-act="wiz-src" data-src="${s.id}">
               <span class="src-radio"></span>
               <span class="src-body"><span class="src-name">${esc(s.label)}</span>${s.sub ? `<span class="hint" style="display:block">${esc(s.sub)}</span>` : ""}</span>
             </button>`).join("")}</div>`);
      case 13: {
        const addr = wizComposeAddress(d);
        const socLabels = SOCIAL_PLATFORMS
          .map((p) => ({ p: p, h: socialHandle(d[p.key] || "", p.key) }))
          .filter((x) => x.h)
          .map((x) => x.p.label)
          .join(", ");
        const styleName = (WIZ_STYLES.find((s) => s.id === d.style) || {}).name || d.style;
        const row = (ico, label, value, step) => `
          <div class="sum-row">
            <span class="sum-ico">${ico}</span>
            <span class="sum-label">${esc(label)}</span>
            <span class="sum-value${value ? "" : " empty"}">${esc(value || "ללא")}</span>
            <button type="button" class="sum-edit" data-act="wiz-goto" data-step="${step}" aria-label="עריכה">✏️</button>
          </div>`;
        const svcCount = (d.services || []).filter((s) => s && s.name).length;
        return `
          <div class="wiz-q-wrap">
            <div class="wiz-emoji">✅</div>
            <h2 class="wiz-q">סיכום</h2>
            <p class="wiz-sub">בדקו שהכול נכון. כל הפרטים ניתנים לשינוי מאוחר יותר בהגדרות.</p>
            <div class="wiz-field">
              <div class="logo-preview${d.logo ? " has-img" : ""}" style="margin:0 auto 16px">${d.logo
                ? `<img src="${esc(d.logo)}" alt="לוגו">`
                : esc((d.name || "מ").trim()[0] || "מ")}</div>
              <div class="sum-card">
                ${row("💈", "שם המספרה", d.name, 2)}
                ${row("🔗", "כתובת אישית", d.handle, 3)}
                ${row("📞", "טלפון", d.phone, 4)}
                ${row("📍", "כתובת", addr, 4)}
                ${row("✂️", "שירותים", svcCount ? svcCount + " שירותים" : "", 5)}
                ${row("🧑‍🔧", "ספרים", d.multiStaff ? (d.staff || []).filter(Boolean).join(", ") : "ספר יחיד", 6)}
                ${row("📝", "תיאור", d.about, 7)}
                ${row("🌐", "רשתות חברתיות", socLabels, 8)}
                ${row("🎨", "עיצוב", styleName, 10)}
              </div>
              <label class="ab-custom privacy-agree">
                <input type="checkbox" id="wz-privacy" ${d.privacyOk ? "checked" : ""}>
                <span>קראתי ואני מסכים/ה ל<a href="privacy.html" target="_blank" rel="noopener">מדיניות הפרטיות</a> ול<a href="terms.html" target="_blank" rel="noopener">תנאי השימוש</a></span>
              </label>
            </div>
          </div>`;
      }
      default:
        return "";
    }
  }

  function wizQ(emoji, title, sub, field) {
    return `
      <div class="wiz-q-wrap">
        <div class="wiz-emoji">${emoji}</div>
        <h2 class="wiz-q">${esc(title)}</h2>
        <p class="wiz-sub">${esc(sub)}</p>
        <div class="wiz-field">${field}</div>
      </div>`;
  }

  function renderOnboarding() {
    // מסך "בונים לך את המערכת"
    if (wiz.step === 99) {
      return `
      <div class="screen active">
        <div class="wiz-wrap building">
          <div class="build-ring"><div class="build-emoji">✂️</div></div>
          <h2 class="wiz-q" style="margin-top:26px">בונים לך את המספרה המושלמת…</h2>
          <p class="wiz-sub" id="build-status">מכינים את היומן שלך</p>
          <div class="build-bar"><div class="build-fill" id="build-fill"></div></div>
        </div>
      </div>`;
    }

    const first = (wiz.data.owner || "").trim().split(/\s+/)[0];
    const greet = wiz.step > 1 && first ? `<div class="wiz-greet">ברוך הבא, ${esc(first)} 👋</div>` : "";
    const dots = wiz.step >= 1
      ? `<div class="wiz-dots">${Array.from({ length: WIZ_QUESTIONS }, (_, i) =>
          `<span class="wd ${i + 1 < wiz.step ? "done" : ""}${i + 1 === wiz.step ? " on" : ""}"></span>`).join("")}</div>`
      : "";

    const isLast = wiz.step === WIZ_QUESTIONS;
    const nav = wiz.step === 0
      ? `<button class="btn btn-primary btn-lg" data-act="wiz-next">בוא נתחיל 🚀</button>
         <button class="btn btn-ghost btn-sm" data-act="wiz-existing" style="margin-top:10px;width:100%">כבר יש לי מערכת</button>`
      : `<button class="btn btn-primary btn-lg" data-act="wiz-next">${isLast ? "יצירת המספרה ✨" : "המשך ›"}</button>
         ${WIZ_SKIPPABLE.includes(wiz.step) ? `<button class="btn btn-ghost btn-sm" data-act="wiz-skip" style="margin-top:8px;width:100%">דלג/י על זה</button>` : ""}
         <button class="btn btn-ghost btn-sm" data-act="wiz-back" style="margin-top:6px;width:100%">‹ חזרה</button>`;

    return `
    <div class="screen active">
      <div class="wiz-wrap">
        ${greet}${dots}
        <div class="wiz-body" id="wizBody">${wizStepHtml()}</div>
        <div class="wiz-nav">${nav}</div>
      </div>
    </div>`;
  }

  // חיווט אירועים לשלב הנוכחי (בטוח לקריאה חוזרת)
  function wizBindStep() {
    // תצוגה מקדימה חיה של הקישור
    const h = $("#wz-handle");
    if (h && !h.__bound) {
      h.__bound = true;
      h.addEventListener("input", () => {
        const v = h.value.trim().toLowerCase().replace(/[^a-z0-9-]/g, "");
        const p = $("#wz-linkPrev");
        if (p) p.innerHTML = esc(shareBase() + "#") + "<b>" + esc(v || "הכתובת-שלך") + "</b>";
      });
    }
    // מונה תווים חי בשלב התיאור
    const ab = $("#wz-about");
    if (ab && !ab.__bound) {
      ab.__bound = true;
      ab.addEventListener("input", () => {
        const n = $("#wz-about-n");
        if (n) n.textContent = String(ab.value.length);
      });
    }
    // Enter = המשך (למעט שלבים עם כמה שדות / טקסט חופשי)
    const body = $("#wizBody");
    if (body && !body.__bound) {
      body.__bound = true;
      body.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && wiz.step !== 5 && wiz.step !== 7) { e.preventDefault(); wizNext(); }
      });
    }
  }

  function wizFocus() {
    wizBindStep();
    const el = $("#wizBody") && $("#wizBody").querySelector("input");
    if (el) setTimeout(() => el.focus(), 120);
  }

  // שמירת שדות שלב 4 (טלפון + כתובת מפורקת) מתוך הטופס לתוך wiz.data
  function wizCaptureStep4() {
    const d = wiz.data;
    if ($("#wz-phone")) d.phone = $("#wz-phone").value.trim();
    if ($("#wz-city")) d.city = $("#wz-city").value.trim();
    if ($("#wz-street")) d.street = $("#wz-street").value.trim();
    if ($("#wz-houseno")) d.houseNo = $("#wz-houseno").value.trim();
    d.address = wizComposeAddress(d);
  }

  // קריאת שורות השירותים מהטופס אל wiz.data
  function wizCaptureServices() {
    const list = $("#wz-svc-list");
    if (!list) return;
    wiz.data.services = [...list.querySelectorAll("[data-svc-row]")].map((row) => ({
      name: (row.querySelector(".sv-name") || {}).value ? row.querySelector(".sv-name").value.trim() : "",
      price: Number((row.querySelector(".sv-price") || {}).value || 0),
      durationMin: Number((row.querySelector(".sv-dur") || {}).value || 0),
    }));
  }
  // רענון גוף השאלון בלבד (בלי לקפוץ עם הפוקוס)
  function wizRenderBody() {
    const body = $("#wizBody");
    if (!body) { render(); return; }
    body.innerHTML = wizStepHtml();
    wizBindStep();
  }

  function wizGo(step) {
    // עריכה שהתחילה ממסך הסיכום — אחרי אישור השלב חוזרים ישר לסיכום
    if (wiz.returnTo && step > wiz.step && step !== wiz.returnTo) step = wiz.returnTo;
    if (step === wiz.returnTo) wiz.returnTo = 0;
    wiz.step = step;
    haptic(14);
    render();
    wizFocus();
  }

  async function wizNext() {
    if (wiz.busy) return;
    const d = wiz.data;
    if (wiz.step === 0) { wizGo(1); return; }
    if (wiz.step === 1) {
      d.owner = ($("#wz-owner") && $("#wz-owner").value.trim()) || "";
      if (!d.owner) { toast("איך קוראים לך?", "", "✋"); haptic(40); return; }
      wizGo(2); return;
    }
    if (wiz.step === 2) {
      d.name = ($("#wz-name") && $("#wz-name").value.trim()) || "";
      if (!d.name) { toast("נא להזין שם למספרה", "", "✋"); haptic(40); return; }
      wizGo(3); return;
    }
    if (wiz.step === 3) {
      const v = (($("#wz-handle") && $("#wz-handle").value) || "").trim().toLowerCase().replace(/[^a-z0-9-]/g, "");
      if (!/^[a-z0-9-]{3,20}$/.test(v)) { toast("כתובת: 3–20 אותיות באנגלית/מספרים", "", "✋"); haptic(40); return; }
      if (["main", "new", "signup"].includes(v)) { toast("כתובת שמורה — בחרו אחרת", "", "✋"); haptic(40); return; }
      wiz.busy = true;
      const btn = $("[data-act='wiz-next']"); if (btn) { btn.disabled = true; btn.textContent = "בודקים…"; }
      let taken = false;
      try { taken = await Store.shopExists(v); } catch (e) {}
      wiz.busy = false;
      if (taken) {
        toast("הכתובת הזו תפוסה — נסו אחרת", "", "🔁"); haptic(40);
        if (btn) { btn.disabled = false; btn.textContent = "המשך ›"; }
        return;
      }
      d.handle = v;
      wizGo(4); return;
    }
    if (wiz.step === 4) { wizCaptureStep4(); wizGo(5); return; }
    if (wiz.step === 5) {
      wizCaptureServices();
      const valid = (d.services || []).filter((s) => s.name && s.price >= 0 && s.durationMin >= 5);
      if (!valid.length) { toast("הוסיפו לפחות שירות אחד (שם, מחיר ומשך)", "", "✂️"); haptic(40); return; }
      d.services = valid;
      wizGo(6); return;
    }
    if (wiz.step === 6) {
      wizCaptureStaff();
      if (d.multiStaff) {
        const names = (d.staff || []).map((n) => (n || "").trim()).filter(Boolean);
        if (!names.length) { toast("הוסיפו לפחות שם ספר אחד", "", "🧑‍🔧"); haptic(40); return; }
        d.staff = names;
      } else { d.staff = []; }
      wizGo(7); return;
    }
    if (wiz.step === 7) {
      d.about = ($("#wz-about") && $("#wz-about").value.trim()) || "";
      wizGo(8); return;
    }
    if (wiz.step === 8) {
      SOCIAL_PLATFORMS.forEach((p) => {
        const el = $("#wz-" + p.key);
        d[p.key] = socialHandle(el ? el.value : "", p.key);
      });
      wizGo(9); return;
    }
    if (wiz.step === 9) { wizGo(10); return; }   // אייקון — נשמר בעת הבחירה
    if (wiz.step === 10) { wizGo(11); return; }  // סגנון — נשמר בעת הבחירה
    if (wiz.step === 11) {                        // סיסמת ניהול
      d.pass = ($("#wz-pass") && $("#wz-pass").value.trim()) || "";
      d.pass2 = ($("#wz-pass2") && $("#wz-pass2").value.trim()) || "";
      if (d.pass.length < 4) { toast("סיסמה קצרה מדי (לפחות 4 תווים)", "", "✋"); haptic(40); return; }
      if (d.pass !== d.pass2) { toast("הסיסמאות אינן תואמות", "", "🔁"); haptic(40); return; }
      // קוד כניסה חייב להיות ייחודי בין כל המספרות
      wiz.busy = true;
      const btn = $("[data-act='wiz-next']"); if (btn) { btn.disabled = true; btn.textContent = "בודקים…"; }
      let taken = false;
      try { taken = await Store.passcodeTaken(await sha256Hex(d.pass)); } catch (e) {}
      wiz.busy = false;
      if (btn) { btn.disabled = false; btn.textContent = "המשך ›"; }
      if (taken) { toast("קוד הכניסה הזה כבר בשימוש — בחרו קוד אחר", "", "🔁"); haptic(40); return; }
      wizGo(12); return;
    }
    if (wiz.step === 12) { wizGo(13); return; }  // מקור ההגעה — נשמר בעת הבחירה
    if (wiz.step === 13) {                        // סיכום — אישור פרטיות ואז יצירה
      d.privacyOk = !!($("#wz-privacy") && $("#wz-privacy").checked);
      if (!d.privacyOk) { toast("יש לאשר את מדיניות הפרטיות כדי להמשיך", "", "🔒"); haptic(40); return; }
      wizBuild(); return;
    }
  }

  // קריאת שמות הספרים מהטופס אל wiz.data
  function wizCaptureStaff() {
    const list = $("#wz-staff-list");
    if (!list) return;
    wiz.data.staff = [...list.querySelectorAll("[data-staff-row] .st-name")].map((el) => el.value.trim());
    if (!wiz.data.staff.length) wiz.data.staff = [""];
  }

  function wizBack() {
    if (wiz.step <= 0) return;
    wizCaptureCurrent();
    wiz.returnTo = 0;    // חזרה ידנית מבטלת את החזרה האוטומטית לסיכום
    wizGo(wiz.step - 1);
  }

  /* שמירת מה שהוקלד בשלב הנוכחי — לפני מעבר אחורה או קפיצה מהסיכום */
  function wizCaptureCurrent() {
    const map = {
      1: ["owner", "#wz-owner"], 2: ["name", "#wz-name"], 3: ["handle", "#wz-handle"],
      7: ["about", "#wz-about"], 11: ["pass", "#wz-pass"],
    };
    const m = map[wiz.step];
    if (m && $(m[1])) wiz.data[m[0]] = $(m[1]).value.trim();
    if (wiz.step === 8) SOCIAL_PLATFORMS.forEach((p) => {
      const el = $("#wz-" + p.key);
      if (el) wiz.data[p.key] = socialHandle(el.value, p.key);
    });
    if (wiz.step === 11 && $("#wz-pass2")) wiz.data.pass2 = $("#wz-pass2").value.trim();
    if (wiz.step === 13 && $("#wz-privacy")) wiz.data.privacyOk = $("#wz-privacy").checked;
    if (wiz.step === 4) wizCaptureStep4();
    if (wiz.step === 5) wizCaptureServices();
    if (wiz.step === 6) wizCaptureStaff();
  }

  // מסך "בונים לך את המספרה" + יצירה בפועל
  async function wizBuild() {
    wiz.step = 99;
    haptic([18, 60, 18]);
    render();
    const steps = ["מכינים את היומן שלך", "מגדירים שירותים ראשוניים", "יוצרים את הקישור האישי", "כמעט מוכן…"];
    let i = 0;
    const fill = () => { const f = $("#build-fill"); if (f) f.style.width = (18 + i * 22) + "%"; };
    fill();
    const timer = setInterval(() => {
      i++;
      const s = $("#build-status");
      if (s && steps[i]) s.textContent = steps[i];
      fill();
      if (i >= steps.length - 1) clearInterval(timer);
    }, 620);

    const d = wiz.data;
    let passHash = "";
    try { passHash = await sha256Hex(d.pass); } catch (e) {}
    const ownerPassHash = await ownerHash(d.handle, d.pass);   // hash מלוחלח (במקום סיסמה גלויה)
    const res = await Store.createShop(d.handle, {
      name: d.name, ownerPassHash: ownerPassHash, phone: d.phone, address: d.address, ownerName: d.owner,
      style: d.style, services: d.services, staff: d.multiStaff ? d.staff : [],
      about: d.about, instagram: d.instagram, tiktok: d.tiktok, facebook: d.facebook, youtube: d.youtube,
      logo: d.logo, heardFrom: d.heardFrom,
    }, passHash);
    clearInterval(timer);
    if (!res.ok) {
      toast(res.reason || "שגיאה ביצירת המספרה", "", "⚠️");
      // חוזרים לשלב הרלוונטי: קוד כניסה תפוס → שלב הסיסמה, אחרת → הכתובת
      wizGo(/קוד/.test(res.reason || "") ? 11 : 3);
      return;
    }
    const f = $("#build-fill"); if (f) f.style.width = "100%";
    const s = $("#build-status"); if (s) s.textContent = "מוכן! 🎉";
    haptic([20, 70, 40]);
    // נכנסים ישר לניהול המספרה החדשה
    localStorage.setItem("ug_owner_auth__" + d.handle, "1");
    localStorage.setItem("ug_route__" + d.handle, "owner");
    localStorage.setItem("ug_otab__" + d.handle, "publish");
    // המספרה שבבעלות המכשיר הזה — פתיחת האפליקציה תוביל ישר לניהול שלה (כניסת הספר)
    try { localStorage.setItem("ug_my_shop", d.handle); localStorage.setItem("ug_known_handle", d.handle); } catch (e) {}
    setTimeout(() => { location.hash = d.handle; location.reload(); }, 800);
  }

  function wizExisting() {
    // הכתובת נזכרת מהכניסה הקודמת במכשיר — הספר בדרך כלל רק מקליד סיסמה
    let known = "";
    try { known = (localStorage.getItem("ug_known_handle") || "").trim(); } catch (e) {}
    openModal(`
      ${authHeader()}
      <div class="field"><label>הכתובת האישית שלך <span class="req">*</span></label>
        <input class="input" id="lg-handle" placeholder="dani" value="${esc(known)}" autocapitalize="off" autocomplete="off" spellcheck="false">
        <div class="hint" style="margin-top:5px">הכתובת שבחרת ברישום — זו שאחרי הסלאש בקישור ללקוחות (למשל barbertor.web.app/#<b>dani</b>)</div></div>
      <div class="field pw-field"><label>סיסמת ניהול <span class="req">*</span></label>
        <input class="input" id="lg-pass" type="password" placeholder="הסיסמה שקבעת ברישום">
        <button type="button" class="pw-eye" data-act="toggle-pw" style="bottom:0" aria-label="הצג סיסמה">👁️</button></div>
      <p class="hint" id="lg-err" style="min-height:15px;margin-top:0"></p>
      <button class="btn btn-primary" data-act2="do-existing-login">כניסה לניהול</button>
      <button class="btn btn-ghost btn-sm" data-act="close-modal" style="margin-top:8px;width:100%">ביטול</button>
    `);
    const err = (m) => { const e = $("#lg-err"); if (e) { e.style.color = "var(--bad)"; e.textContent = m; } };
    const login = async () => {
      const handle = (($("#lg-handle") && $("#lg-handle").value) || "").trim().toLowerCase().replace(/[^a-z0-9-]/g, "");
      const pass = ($("#lg-pass") && $("#lg-pass").value) || "";
      if (!handle) { err("הזינו את הכתובת האישית"); return; }
      if (!pass) { err("הזינו סיסמה"); return; }
      const btn = $("[data-act2='do-existing-login']"); if (btn) { btn.disabled = true; btn.textContent = "בודקים…"; }
      let data = null;
      try { data = await Store.peekShop(handle); } catch (e) {}
      if (btn) { btn.disabled = false; btn.textContent = "כניסה לניהול"; }
      if (!data || !data.shop) { err("לא נמצאה מערכת בכתובת הזו"); return; }
      const pHash = await ownerHash(handle, pass);
      const okHash = !!(data.shop.ownerPassHash && pHash && pHash === data.shop.ownerPassHash);
      const okLegacy = !!(data.shop.ownerPass && pass === String(data.shop.ownerPass));   // מספרה ותיקה
      const okConfig = (handle === "main") && await ownerConfigCodeMatches(pass);   // קודי אורי (config) — למספרה הראשית
      const ok = okHash || okLegacy || okConfig;
      if (!ok) { err("סיסמה שגויה"); haptic(40); return; }
      // מיגרציה שקטה: מספרה ותיקה עם סיסמה גלויה → שומר hash מלוחלח ומוחק את הגלויה
      if ((okHash || okLegacy) && (!data.shop.ownerPassHash || data.shop.ownerPass)) {
        try { await Store.setOwnerPassHash(handle, pHash); } catch (e) {}
      }
      // כניסה מוצלחת — ישר לניהול. משריינים את קוד הכניסה של המספרה הוותיקה
      // (backfill) כדי שמספרה חדשה לא תוכל להשתמש בו — כל עוד הסיסמה הגלויה עדיין קיימת.
      if (data.shop.ownerPass) { sha256Hex(String(data.shop.ownerPass)).then((h) => Store.registerPasscodeIfFree(h)).catch(() => {}); }
      localStorage.setItem("ug_owner_auth__" + handle, "1");
      localStorage.setItem("ug_route__" + handle, "owner");
      // המספרה שבבעלות המכשיר הזה — פתיחת האפליקציה תוביל ישר לניהול שלה (כניסת הספר)
      try { localStorage.setItem("ug_my_shop", handle); localStorage.setItem("ug_known_handle", handle); } catch (e) {}
      haptic(16);
      location.hash = handle; location.reload();
    };
    const lb = $("[data-act2='do-existing-login']"); if (lb) lb.addEventListener("click", login);
    const pw = $("#lg-pass"); if (pw) pw.addEventListener("keydown", (e) => { if (e.key === "Enter") login(); });
    // כתובת כבר ממולאת → מדלגים ישר לסיסמה
    setTimeout(() => { const el = known ? $("#lg-pass") : $("#lg-handle"); if (el) el.focus(); }, 100);
  }

  function renderNotFound() {
    return `
    <div class="screen active">
      <div class="role-wrap" style="text-align:center">
        <div class="role-hero">
          <div class="rh-logo">🔍</div>
          <h1>המספרה לא נמצאה</h1>
          <p>ייתכן שהקישור שגוי או שהמספרה עדיין לא נפתחה</p>
        </div>
        <button class="btn btn-primary" data-act="open-signup">פתיחת מספרה חדשה</button>
      </div>
    </div>`;
  }

  async function doCreateShop() {
    const name = ($("#ob-name") && $("#ob-name").value.trim()) || "";
    let handle = (($("#ob-handle") && $("#ob-handle").value) || "").trim().toLowerCase().replace(/[^a-z0-9-]/g, "");
    const pass = ($("#ob-pass") && $("#ob-pass").value.trim()) || "";
    if (!name) { toast("נא להזין שם מספרה", "", "✋"); return; }
    if (!/^[a-z0-9-]{3,20}$/.test(handle)) { toast("כתובת: 3–20 אותיות באנגלית/מספרים", "", "✋"); return; }
    if (handle === "main" || handle === "new" || handle === "signup") { toast("כתובת שמורה — בחרו אחרת", "", "✋"); return; }
    if (pass.length < 3) { toast("סיסמה קצרה מדי (לפחות 3 תווים)", "", "✋"); return; }
    const btn = $("[data-act='create-shop']"); if (btn) { btn.disabled = true; btn.textContent = "יוצר…"; }
    let passHash = "";
    try { passHash = await sha256Hex(pass); } catch (e) {}
    const ownerPassHash = await ownerHash(handle, pass);   // hash מלוחלח לאימות (במקום סיסמה גלויה)
    const res = await Store.createShop(handle, { name: name, ownerPassHash: ownerPassHash }, passHash);
    if (!res.ok) {
      toast(res.reason || "שגיאה ביצירת המספרה", "", "⚠️");
      if (btn) { btn.disabled = false; btn.textContent = "יצירת המספרה"; }
      return;
    }
    // נכנסים למספרה החדשה כמנהל
    localStorage.setItem("ug_owner_auth__" + handle, "1");
    localStorage.setItem("ug_route__" + handle, "owner");
    location.hash = handle;
    location.reload();
  }

  /* =======================================================================
     חיווט אירועים (delegation)
     =======================================================================*/
  function wire() {
    // שמירת מצב פתיחה/סגירה של קבוצות ההגדרות. אירוע toggle אינו עולה (bubbling),
    // אבל נלכד בשלב ה-capture, כך שמאזין יחיד על document מספיק.
    document.addEventListener("toggle", (e) => {
      const d = e.target;
      if (d && d.classList && d.classList.contains("set-group") && d.id) {
        setGroupOpen[d.id.replace(/^setg-/, "")] = d.open;
      }
    }, true);
    // מעטפת Cordova: פתיחת קישורים חיצוניים (Waze / מפות) בדפדפן המערכת במקום בתוך האפליקציה
    if (isCordovaOnly()) {
      document.addEventListener("click", (e) => {
        const a = e.target.closest && e.target.closest('a[href^="http"],a[href^="tel:"],a[href^="mailto:"]');
        if (a && a.href) { e.preventDefault(); window.open(a.href, "_system"); }
      });
    }
    document.addEventListener("click", async (e) => {
      // כניסת מנהל נסתרת: 3 הקשות רצופות על הלוגו (בתצוגת לקוח בלבד)
      if (e.target.closest(".logo-dot") && view.route === "client") { onLogoTap(); return; }

      const t = e.target.closest("[data-act],[data-svc],[data-day],[data-oday],[data-slot],[data-wait],[data-photo],[data-delphoto],[data-tab],[data-otab],[data-tabscroll],[data-active],[data-abday],[data-abslot]");
      if (!t) return;

      // בורר היום/השעה במודאל «הוספת תור ידני» — לפני הבוררים של הלקוח
      if (t.dataset.abday) { if (!addBk) return; captureAddBooking(); addBk.date = t.dataset.abday; addBk.start = ""; refreshAddBooking(); return; }
      if (t.dataset.abslot) { if (!addBk) return; captureAddBooking(); addBk.start = t.dataset.abslot; addBk.custom = false; refreshAddBooking(); return; }

      if (t.dataset.photo) { openPhoto(t.dataset.photo); return; }
      if (t.dataset.delphoto !== undefined) {
        Store.removePhoto(t.dataset.delphoto).then(() => { toast("התמונה נמחקה", "", "🗑️"); render(); });
        return;
      }

      // בורר שירות
      if (t.dataset.svc) { view.selService = t.dataset.svc; view.selSlot = null; render(); return; }
      // הקשה על שעה פנויה פותחת ישירות את אישור ההזמנה — פחות הקשות
      if (t.dataset.slot) { view.selSlot = t.dataset.slot; render(); openConfirm(); return; }
      if (t.dataset.wait) { const [dk, tm] = t.dataset.wait.split("|"); openWaitlist(dk, tm); return; }
      if (t.dataset.day && t.classList.contains("day-chip")) { view.selDate = t.dataset.day; view.selSlot = null; render(); return; }
      if (t.dataset.oday) { view.oDate = t.dataset.oday; render(); return; }
      if (t.dataset.tabscroll) { scrollTabbar(t.dataset.tabscroll); return; }
      if (t.dataset.tab) { view.clientTab = t.dataset.tab; render(); return; }
      if (t.dataset.otab) {
        view.ownerTab = t.dataset.otab;
        view.settingsPage = null; view.settingsItem = null; view.subPage = null;   // מעבר לשונית מאפס ניווט פנימי
        try { localStorage.setItem("ug_otab__" + SHOP, view.ownerTab); } catch (e2) {}
        try { $("#oscroll") && ($("#oscroll").scrollTop = 0); } catch (e3) {}
        render(); return;
      }

      const act = t.dataset.act;
      if (!act) return;

      // רשת ביטחון מרכזית: פעולה שכותבת לשרת (ביטול/ביקורת/המתנה/הגדרות…) ונכשלת
      // (רשת/הרשאה) לא תישאר בשקט — נודיע ללקוח כדי שידע לנסות שוב. איננו סוגרים
      // חלונות/מרעננים כאן, כדי לא לשבש זרימות אחרות (למשל בקשת התחברות-מחדש לבעלים).
      try {
      switch (act) {
        case "close-modal": closeModal(); break;

        // ניווט בהגדרות המסודרות: קטגוריה → פריט → חזרה
        case "settings-page":
          view.settingsPage = t.dataset.page || null; view.settingsItem = null;
          try { $("#oscroll") && ($("#oscroll").scrollTop = 0); } catch (e4) {}
          render(); break;
        case "settings-back":
          view.settingsPage = null; view.settingsItem = null;
          try { $("#oscroll") && ($("#oscroll").scrollTop = 0); } catch (e4) {}
          render(); break;
        case "settings-item":
          view.settingsItem = t.dataset.item || null;
          try { $("#oscroll") && ($("#oscroll").scrollTop = 0); } catch (e4) {}
          render(); break;
        case "settings-item-back":
          view.settingsItem = null;
          try { $("#oscroll") && ($("#oscroll").scrollTop = 0); } catch (e4) {}
          render(); break;
        // עמוד-משנה בתוך לשוניות הניהול (יום בשעות, קישור/QR בפרסום וכו')
        case "sub-page":
          view.subPage = t.dataset.sub || null;
          try { $("#oscroll") && ($("#oscroll").scrollTop = 0); } catch (e4) {}
          render(); break;
        case "sub-back":
          view.subPage = null;
          try { $("#oscroll") && ($("#oscroll").scrollTop = 0); } catch (e4) {}
          render(); break;

        case "open-confirm": openConfirm(); break;
        case "do-book": doBook(); break;

        case "confirm-arrival":
          await Store.setBookingStatus(t.dataset.id, "confirmed");
          toast("הגעתך אושרה ✓", "good", "📍"); render(); break;

        case "owner-cancel":
          await Store.setBookingStatus(t.dataset.id, "cancelled", "owner");
          toast("התור בוטל", "", "🗑️"); render(); break;

        // ביטול תור ע״י הלקוח — עם אישור למניעת ביטול בטעות
        case "cancel-booking": confirmCancelBooking(t.dataset.id); break;
        case "do-cancel-booking":
          if (clientCancelSeen) clientCancelSeen.add(t.dataset.id);   // ביטול עצמי — בלי התראת "בוטל"
          await Store.setBookingStatus(t.dataset.id, "cancelled", "client");
          closeModal(); toast("התור בוטל", "", "🗑️"); render(); break;

        // שינוי מועד — טעינת התור לזרימת ההזמנה לבחירת מועד חדש
        case "reschedule": {
          const bk = Store.get().bookings.find((x) => x.id === t.dataset.id);
          if (!bk) break;
          view.rescheduleId = bk.id;
          view.selService = bk.serviceId;
          view.selSlot = null;
          view.selDate = null;   // clientBook יבחר יום פתוח כברירת מחדל
          view.clientTab = "book";
          toast("בחרו מועד חדש לתור", "sky", "🔄");
          render();
          break;
        }
        case "cancel-reschedule":
          view.rescheduleId = null; view.selSlot = null; view.clientTab = "mine";
          toast("שינוי המועד בוטל", "", "↩️"); render(); break;

        // "קבע שוב כמו פעם קודמת" — טוען את השירות (והספר) מהתור האחרון וקופץ לבחירת מועד
        case "book-again": {
          const st = Store.get();
          const svcId = t.dataset.service;
          const svcOk = (st.services || []).some((s) => s.id === svcId && s.active !== false);
          view.rescheduleId = null;
          view.selService = svcOk ? svcId : null;
          view.selStaff = t.dataset.staff || "";
          view.selSlot = null; view.selDate = null;
          view.clientTab = "book";
          toast(svcOk ? "בחרו מועד לתור החדש" : "בחרו שירות ומועד", "sky", "🔁");
          render();
          break;
        }

        case "enable-notif": handleEnableNotif(); break;
        case "dismiss-spam": spamDismissed = Date.now(); render(); break;
        case "notif-help": notifHelp(); break;
        case "export-report": exportReportCsv(); break;
        case "backup-download": downloadBackup(false); break;
        case "backup-download-full": downloadBackup(true); break;
        case "backup-restore": { const f = $("[data-backupfile]"); if (f) f.click(); break; }
        case "do-restore": doRestore(); break;
        case "delete-shop": confirmDeleteShop(); break;
        case "do-delete-shop": doDeleteShop(); break;
        case "delete-my-data": confirmDeleteMyData(); break;
        case "do-delete-my-data": doDeleteMyData(); break;
        // כפיית עדכון — מנקה מטמון ומרענן, למקרה שהדפדפן מחזיק גרסה ישנה
        case "force-update": forceUpdate(); break;
        case "owner-login": promptOwner(); break;   // כניסת מנהל ייעודית (במקום 3 לחיצות על הלוגו)
        case "owner-logout": confirmOwnerLogout(); break;
        case "do-owner-logout":
          try {
            localStorage.removeItem("ug_owner_auth__" + SHOP);
            localStorage.removeItem("ug_route__" + SHOP);
            localStorage.removeItem("ug_otab__" + SHOP);
            localStorage.removeItem("ug_my_shop");
            localStorage.removeItem("ug_last_shop");
          } catch (e2) {}
          if (UG.Auth && UG.Auth.signOut) { try { await UG.Auth.signOut(); } catch (e3) {} }
          location.hash = "new"; location.reload();
          break;
        case "toggle-theme": toggleTheme(); break;
        case "toggle-pw": {
          const field = t.closest(".pw-field"); const inp = field && field.querySelector("input");
          if (inp) { inp.type = inp.type === "password" ? "text" : "password"; t.textContent = inp.type === "password" ? "👁️" : "🙈"; }
          break;
        }
        case "client-detail": clientDetail(t.dataset.key); break;
        case "secure-shop": openSecureShop(); break;
        case "release-security": releaseSecurity(); break;
        case "auth-signout":
          if (UG.Auth) UG.Auth.signOut().finally(() => { toast("התנתקת מהחשבון", "", "🔓"); render(); });
          break;
        case "install-app": doInstall(); break;
        case "install-dismiss": suppressInstall(); hideInstallBar(); break;
        case "cookie-ok":
          localStorage.setItem("ug_cookie_ok", "1"); hideCookieBar();
          setTimeout(maybeShowInstall, 400); break;
        case "add-cal": addToCalendar(t.dataset.id); break;
        case "qr-download": downloadQr(); break;
        case "logo-pick": { const inp = $("[data-logofile]"); if (inp) inp.click(); break; }
        case "logo-remove":
          await Store.setShopMedia("logo", "");
          toast("הלוגו הוסר", "", "🗑️"); render(); break;
        case "cover-pick": { const inp = $("[data-coverfile]"); if (inp) inp.click(); break; }
        case "cover-remove":
          await Store.setShopMedia("cover", "");
          toast("תמונת הנושא הוסרה", "", "🗑️"); render(); break;
        case "share-app": shareApp(); break;
        case "share-wa": {
          const stw = Store.get();
          const txt = "קביעת תור ל" + ((stw.shop && stw.shop.name) || "מספרה") + " 💈✂️\n" + clientLink();
          openExternal("https://wa.me/?text=" + encodeURIComponent(txt));
          break;
        }
        case "stay": closeModal(); break;
        case "do-exit": performExit(); break;
        // רב-משתמשי: פתיחת מספרה / ניווט להרשמה
        case "create-shop": doCreateShop(); break;
        // שאלון פתיחת מספרה
        case "wiz-next": wizNext(); break;
        case "wiz-back": wizBack(); break;
        case "wiz-skip": wizCaptureCurrent(); wizGo(wiz.step + 1); break;
        case "wiz-goto":
          wizCaptureCurrent();
          wiz.returnTo = wiz.step;               // לחזור לסיכום אחרי העריכה
          wizGo(Number(t.dataset.step)); break;
        case "wiz-src":
          wiz.data.heardFrom = t.dataset.src;
          haptic(14); wizRenderBody(); break;
        case "wiz-logo-pick": {
          const f = document.querySelector("[data-wizlogofile]");
          if (f) f.click();
          break;
        }
        case "wiz-logo-clear":
          wiz.data.logo = "";
          haptic(14); wizRenderBody(); break;
        case "wiz-svc-add":
          wizCaptureServices();
          wiz.data.services.push({ name: "", price: "", durationMin: 30 });
          haptic(10); wizRenderBody();
          setTimeout(() => {
            const rows = document.querySelectorAll("#wz-svc-list .sv-name");
            if (rows.length) rows[rows.length - 1].focus();
          }, 60);
          break;
        case "wiz-svc-del": {
          wizCaptureServices();
          const i = Number(t.dataset.i);
          wiz.data.services.splice(i, 1);
          if (!wiz.data.services.length) wiz.data.services.push({ name: "", price: "", durationMin: 30 });
          haptic(14); wizRenderBody();
          break;
        }
        case "wiz-staff-mode":
          wizCaptureStaff();
          wiz.data.multiStaff = t.dataset.multi === "1";
          if (wiz.data.multiStaff && !(wiz.data.staff || []).some((n) => (n || "").trim())) wiz.data.staff = [""];
          haptic(12); wizRenderBody();
          break;
        case "wiz-staff-add":
          wizCaptureStaff();
          wiz.data.staff.push("");
          haptic(10); wizRenderBody();
          setTimeout(() => {
            const rows = document.querySelectorAll("#wz-staff-list .st-name");
            if (rows.length) rows[rows.length - 1].focus();
          }, 60);
          break;
        case "wiz-staff-del": {
          wizCaptureStaff();
          const i = Number(t.dataset.i);
          wiz.data.staff.splice(i, 1);
          if (!wiz.data.staff.length) wiz.data.staff.push("");
          haptic(14); wizRenderBody();
          break;
        }
        case "wiz-style":
          wiz.data.style = t.dataset.style;
          applyShopStyle(wiz.data.style);       // תצוגה מקדימה חיה
          haptic(14); wizRenderBody();
          break;
        case "wiz-existing": wizExisting(); break;
        case "goto-shop": {
          const h = (($("#ob-existing") && $("#ob-existing").value) || "").trim().toLowerCase().replace(/[^a-z0-9-]/g, "");
          if (!h) { toast("הזינו את הכתובת האישית שלכם", "", "✋"); break; }
          location.hash = h; location.reload(); break;
        }
        case "open-signup": location.hash = "new"; location.reload(); break;
        case "ob-cancel": location.hash = ""; location.reload(); break;
        case "copy-link":
          (async () => {
            try { await navigator.clipboard.writeText(clientLink()); toast("הקישור הועתק ✓", "good", "📋"); }
            catch (e) { shareApp(); }
          })();
          break;

        // רשימת המתנה
        case "join-wait": doJoinWait(t.dataset.key); break;
        case "leave-wait":
          await Store.leaveWaitlist(t.dataset.id); closeModal();
          toast("הוסרת מרשימת ההמתנה", "", "🔕"); render(); break;

        // התראת "התפנה תור"
        case "alert-book": {
          const { id, date, start } = t.dataset;
          view.clientTab = "book"; view.selDate = date;
          const free = gridSlots(date).some((s) => s.start === start && !s.booking && !s.blocked && !s.past && !s.hidden);
          if (free) {
            view.selSlot = start; render(); openConfirm();
          } else {
            view.selSlot = null; render();
            toast("השעה נתפסה שוב — אפשר לחזור לרשימת ההמתנה", "", "😕");
            await Store.consumeAlert(id);
          }
          break;
        }
        case "alert-dismiss": await Store.consumeAlert(t.dataset.id); render(); break;

        // דירוג וביקורת
        case "clear-history": confirmClearHistory(); break;
        case "do-clear-history":
          localStorage.setItem("ug_hist_cleared__" + SHOP, String(Date.now()));
          closeModal(); toast("ההיסטוריה נמחקה", "", "🗑️"); render(); break;
        case "review-never":
          localStorage.setItem("ug_reviews_off__" + SHOP, "1");
          toast("לא נטריד אותך שוב 🙂", "good", "👍"); render(); break;
        case "open-review": openReview(t.dataset.id); break;
        case "add-review": openNewReview(); break;
        case "send-new-review": {
          const name = ($("#nrv-name") && $("#nrv-name").value.trim()) || "";
          if (!name) { toast("נא להזין שם", "", "✋"); break; }
          const rating = $("#modal").__rating ? $("#modal").__rating() : 5;
          const serviceName = ($("#nrv-svc") && $("#nrv-svc").value) || "";
          const text = ($("#nrv-text") && $("#nrv-text").value.trim()) || "";
          identity.name = name; saveIdentity();
          await Store.addReview({
            bookingId: "free-" + u.uid(), userId: identity.userId,
            userName: name, serviceName, rating, text,
          });
          closeModal(); toast("תודה על הביקורת! ⭐", "good", "🙏"); render(); break;
        }
        case "review-skip": {
          let skip; try { skip = JSON.parse(localStorage.getItem("ug_review_skip") || "[]"); } catch (e2) { skip = []; }
          skip.push(t.dataset.id);
          localStorage.setItem("ug_review_skip", JSON.stringify(skip.slice(-100)));
          render(); break;
        }
        case "send-review": {
          const rating = $("#modal").__rating ? $("#modal").__rating() : 5;
          const text = ($("#rv-text") && $("#rv-text").value.trim()) || "";
          const bk = Store.get().bookings.find((x) => x.id === t.dataset.id);
          await Store.addReview({
            bookingId: t.dataset.id, userId: identity.userId,
            userName: identity.name || "לקוח", serviceName: bk ? bk.serviceName : "",
            rating, text,
          });
          closeModal(); toast("תודה על הדירוג! ⭐", "good", "🙏"); render(); break;
        }

        // מחיקת רשומה מהדוח
        case "del-report": confirmDeleteBooking(t.dataset.id); break;
        case "do-del-report":
          await Store.deleteBooking(t.dataset.id); closeModal();
          toast("הרשומה נמחקה מהדוח", "", "🗑️"); render(); break;

        // דוח חודשי
        case "stat-prev": view.statMonth = ymShift(view.statMonth || ymNow(), -1); render(); break;
        case "stat-next": {
          const next = ymShift(view.statMonth || ymNow(), 1);
          if (next <= ymNow()) view.statMonth = next;
          render(); break;
        }

        // הוספת תור ידנית ע״י הבעלים (24 שעות)
        case "add-booking": ownerAddBooking(); break;
        case "save-add-booking": saveAddBooking(); break;

        // ייבוא לקוחות
        case "import-clients": openImportClients(); break;
        case "do-import-clients": doImportClients(); break;

        // הודעה קבוצתית ללקוחות
        case "broadcast": openBroadcast(); break;
        case "do-broadcast": doBroadcast("#bc-text", "do-broadcast"); break;
        case "bc-tpl": fillBroadcast(t.dataset.t); break;
        case "do-broadcast-pub": doBroadcast("#pb-text", "do-broadcast-pub"); break;
        case "wa-blast": openWaBlast(); break;
        // סימון "נשלח" — הקישור עצמו נפתח כרגיל בוואטסאפ
        case "wa-sent": waSent.add(t.dataset.p); setTimeout(renderWaList, 400); break;

        // חסימת לקוח בעייתי
        case "block-client": openBlockClient(t.dataset.key); break;
        case "do-block-client": doBlockClient(); break;
        case "unblock-client":
          await Store.unblockClient(t.dataset.key);
          closeModal();  // הפעולה זמינה גם מתוך כרטיס הלקוח
          toast("החסימה הוסרה ✓", "good", "🔓"); render(); break;

        // תצוגת לקוח (בעלים)
        case "preview-client": previewAsClient(); break;
        case "exit-preview": exitPreview(); break;

        // זיהוי לקוח (מודל חדש)
        case "ag-google": clientGoogleSignIn(); break;
        case "ag-phone-form": view.authPhoneForm = true; render(); break;
        case "ag-back": view.authPhoneForm = false; render(); break;
        case "ag-save-phone": agSavePhone(); break;

        // בדיקת קישור רשת חברתית — פותח את הפרופיל כדי שהספר יוודא שזה שלו
        case "soc-test": {
          const key = t.dataset.p || "instagram";
          const meta = socialMeta(key);
          const el = $(t.dataset.src || "");
          const h = socialHandle(el ? el.value : "", key);
          if (!h) { toast("הזינו שם משתמש " + (meta ? meta.label : ""), "", meta ? meta.emoji : "🌐"); break; }
          openExternal(socialUrl(h, key));
          break;
        }

        // תמיכה ופרטיות
        case "support-wa": openSupportWa(); break;
        case "accept-privacy": acceptPrivacy(); break;

        // מנוי
        case "show-upgrade": handleUpgrade(); break;
        case "adm-extend": admExtend(t.dataset.sid, Number(t.dataset.m)); break;
        case "adm-google": adminGoogleSignIn(); break;
        case "del-contact":
          await Store.removeContact(t.dataset.id);
          closeModal();  // הפעולה זמינה גם מתוך כרטיס הלקוח
          toast("הלקוח הוסר מהרשימה", "", "🗑️"); render(); break;

        // סימון "לא הגיע"
        // סימון הגעה ע״י הספר — מכניס את התור לדוח ההכנסות ולביקורי הלקוח.
        // מתג: לחיצה שנייה מבטלת את הסימון.
        case "owner-confirm": {
          const bk = Store.get().bookings.find((x) => x.id === t.dataset.id);
          const on = bk && bk.status === "confirmed";
          await Store.setBookingStatus(t.dataset.id, on ? "booked" : "confirmed", "owner");
          toast(on ? "בוטל סימון ההגעה" : "סומן: הלקוח הגיע ✓", on ? "" : "good", on ? "↩️" : "✓");
          render(); break;
        }
        case "owner-noshow":
          await Store.setBookingStatus(t.dataset.id, "noshow", "owner");
          toast("סומן: הלקוח לא הגיע", "", "❌"); render(); break;
        case "owner-unnoshow":
          await Store.setBookingStatus(t.dataset.id, "booked", "owner");
          toast("הסימון בוטל", "", "↩️"); render(); break;

        // חופשות / חסימת תאריכים
        case "add-vacation": {
          const from = ($("#vac-from") && $("#vac-from").value) || "";
          const to = ($("#vac-to") && $("#vac-to").value) || from;
          if (!from) { toast("בחרו תאריך", "", "✋"); break; }
          const a = from, b2 = (to && to >= from) ? to : from;
          const list = []; let cur = u.parseKey(a); const end = u.parseKey(b2);
          let guard = 0;
          while (cur <= end && guard++ < 400) { list.push(u.dateKey(cur)); cur.setDate(cur.getDate() + 1); }
          await Store.addClosedDates(list);
          toast(list.length > 1 ? `נחסמו ${list.length} תאריכים 🌴` : "התאריך נחסם 🌴", "good", "🌴");
          render(); break;
        }
        case "del-vacation":
          await Store.removeClosedDate(t.dataset.key);
          toast("התאריך נפתח מחדש", "good", "✓"); render(); break;

        // שירותים
        case "add-svc": svcModal(null); break;
        case "edit-svc": {
          const svc = Store.get().services.find((s) => s.id === t.dataset.id);
          if (svc) svcModal(svc); break;
        }
        case "save-svc": saveSvc(t.dataset.id); break;
        case "del-svc":
          await Store.removeService(t.dataset.id); closeModal();
          toast("השירות נמחק", "", "🗑️"); render(); break;

        // מוצרים — דורש מספר טלפון (בשבילו הלקוח פונה בוואטסאפ)
        case "add-product":
          if (!waIntl(Store.get().shop.phone || "")) {
            toast("קודם צריך להזין מספר טלפון בהגדרות", "", "📵"); break;
          }
          productModal(null); break;
        case "edit-product": {
          const prod = (Store.get().products || []).find((p) => p.id === t.dataset.id);
          if (prod) productModal(prod); break;
        }
        case "save-product": saveProduct(t.dataset.id); break;
        case "del-product":
          await Store.removeProduct(t.dataset.id); closeModal();
          toast("המוצר נמחק", "", "🗑️"); render(); break;
        case "product-pic": { const inp = document.querySelector("[data-productfile]"); if (inp) inp.click(); break; }
        case "product-pic-clear": {
          if ($("#modal")) $("#modal").__prodImg = "";
          const prev = $("#pm-prev"); if (prev) { prev.classList.remove("has-img"); prev.innerHTML = "🛍️"; }
          const clr = $("#pm-clear"); if (clr) clr.style.display = "none";
          break;
        }
        case "product-zoom": {
          const prod = activeProducts(Store.get()).find((p) => p.id === t.dataset.id);
          if (prod && prod.image) openImageZoom(prod.image, prod.name);
          break;
        }
        case "product-interest": {
          const stp = Store.get();
          const prod = (stp.products || []).find((p) => p.id === t.dataset.id);
          const wa = waIntl(stp.shop.phone || "");
          if (!prod || !wa) { toast("לא ניתן לפנות כרגע — חסר מספר טלפון של המספרה", "", "📵"); break; }
          const msg = `שלום! ראיתי באתר את המוצר "${prod.name}" (${u.fmtPrice(prod.price)}) ואשמח לפרטים נוספים לרכישה 🙂`;
          openExternal("https://wa.me/" + wa + "?text=" + encodeURIComponent(msg));
          break;
        }

        case "set-style":
          applyShopStyle(t.dataset.style);
          await Store.saveShop({ style: t.dataset.style });
          haptic(14); toast("סגנון העיצוב עודכן ✓", "good", "🎨"); render(); break;

        // עורך רשימת הספרים
        case "edit-staff": openStaffEditor(); break;
        case "stf-add": captureStaffEdit(); staffEdit.push(""); refreshStaffEditor();
          setTimeout(() => { const rows = document.querySelectorAll("#stf-list .st-name"); if (rows.length) rows[rows.length - 1].focus(); }, 60); break;
        case "stf-del": {
          captureStaffEdit(); staffEdit.splice(Number(t.dataset.i), 1);
          if (!staffEdit.length) staffEdit.push(""); refreshStaffEditor(); break;
        }
        case "save-staff": saveStaff(); break;

        case "save-settings": saveSettings(); break;
      }
      } catch (err) {
        console.warn("[UG] פעולה נכשלה:", (err && (err.code || err.message)) || err);
        toast("הפעולה לא הושלמה — נסו שוב.", "", "⚠️");
      }
    });

    // יומן בעלים — מתגי הפעלה ושעות
    document.addEventListener("change", async (e) => {
      const a = e.target;
      if (a.dataset.gfile !== undefined && a.type === "file") {
        if (a.files && a.files[0]) handleUpload(a.files[0]);
        a.value = "";
        return;
      }
      if (a.dataset.logofile !== undefined && a.type === "file") {
        if (a.files && a.files[0]) handleLogoUpload(a.files[0]);
        a.value = "";
        return;
      }
      // לוגו באשף ההרשמה — נשמר ב-wiz.data עד יצירת המספרה
      if (a.dataset.wizlogofile !== undefined && a.type === "file") {
        const f = a.files && a.files[0];
        a.value = "";
        if (!f) return;
        if (!f.type || f.type.indexOf("image/") !== 0) { toast("נא לבחור קובץ תמונה", "", "🖼️"); return; }
        try {
          wiz.data.logo = await compressLogo(f);
          haptic(14); wizRenderBody();
        } catch (e) { toast("לא הצלחנו לטעון את התמונה", "", "⚠️"); }
        return;
      }
      if (a.dataset.coverfile !== undefined && a.type === "file") {
        if (a.files && a.files[0]) handleCoverUpload(a.files[0]);
        a.value = "";
        return;
      }
      // קובץ גיבוי לשחזור — נפתח במודאל אישור לפני הדריסה
      if (a.dataset.backupfile !== undefined && a.type === "file") {
        const f = a.files && a.files[0];
        a.value = "";
        if (f) handleBackupFile(f);
        return;
      }
      // תמונת מוצר — נשמרת זמנית על המודאל עד לחיצת ״שמירה״
      if (a.dataset.productfile !== undefined && a.type === "file") {
        const f = a.files && a.files[0];
        a.value = "";
        if (!f) return;
        if (!f.type || f.type.indexOf("image/") !== 0) { toast("נא לבחור קובץ תמונה", "", "🖼️"); return; }
        try {
          const dataUrl = await compressImage(f, 900, 0.72);
          if ($("#modal")) $("#modal").__prodImg = dataUrl;
          const prev = $("#pm-prev");
          if (prev) { prev.classList.add("has-img"); prev.innerHTML = `<img src="${esc(dataUrl)}" alt="">`; }
          const clr = $("#pm-clear"); if (clr) clr.style.display = "";
          haptic(14);
        } catch (e) { toast("לא הצלחנו לטעון את התמונה", "", "⚠️"); }
        return;
      }
      // מתג הגדרה ישיר בשורת ההגדרות — נשמר מיד, בלי כפתור שמירה
      if (a.dataset.settoggle && a.type === "checkbox") {
        const patch = {}; patch[a.dataset.settoggle] = a.checked;
        await Store.saveShop(patch);
        haptic(12); render();
        return;
      }
      if (a.dataset.slotOpen !== undefined && a.type === "checkbox") {
        // מתג פנוי/פתוח ליד שעה (checked = זמין ללקוחות)
        const [dk, time] = a.dataset.slotOpen.split("|");
        await Store.setSlotOpen(dk, time, a.checked, a.dataset.inhours === "1");
        render();
      } else if (a.dataset.active !== undefined && a.type === "checkbox") {
        await Store.setDay(Number(a.dataset.active), { active: a.checked });
        render();
      } else if (a.dataset.time) {
        const day = Number(a.dataset.day);
        const patch = {}; patch[a.dataset.time] = a.value;
        await Store.setDay(day, patch);
        toast("השעות עודכנו", "sky", "🕑");
      }
    });

    // תצוגה מקדימה של הקישור בעת פתיחת מספרה
    document.addEventListener("input", (e) => {
      if (e.target && e.target.id === "ob-handle") {
        const h = e.target.value.trim().toLowerCase().replace(/[^a-z0-9-]/g, "");
        const prev = $("#ob-linkPrev");
        if (prev) prev.textContent = "הקישור שלך: " + shareBase() + "#" + (h || "הכתובת-שלך");
      }
      // תצוגה מקדימה חיה של קישורי הרשתות החברתיות (בהגדרות ובאשף)
      if (e.target && /^(set|wz)-(instagram|tiktok|facebook|youtube)$/.test(e.target.id)) {
        const [scope, key] = e.target.id.split("-");
        const meta = socialMeta(key);
        const prevEl = $("#prev-" + scope + "-" + key);
        if (prevEl && meta) prevEl.innerHTML = esc(meta.previewPrefix) + "<b>" + esc(socialHandle(e.target.value, key) || "השם-שלך") + "</b>";
      }
    });
  }

  // כניסת מנהל — מספרה מאובטחת דורשת התחברות עם החשבון; אחרת קוד
  function promptOwner() {
    const shop = (Store.get() && Store.get().shop) || {};
    if (shop.ownerUid) {
      if (UG.Auth && UG.Auth.currentUid && UG.Auth.currentUid() === shop.ownerUid) { go("owner"); return; }
      if (UG.Auth) {
        UG.Auth.available().then((ok) => { ok ? promptOwnerLogin(shop.ownerUid) : promptOwnerCode(); });
        return;
      }
    }
    promptOwnerCode();
  }

  // כותרת ממותגת משותפת לחלונות הכניסה
  function authHeader() {
    const shop = (Store.get() && Store.get().shop) || {};
    return `
      <div class="auth-head">
        <div class="auth-badge">🔐</div>
        <div class="m-title" style="margin:0">כניסת מנהל</div>
        <div class="m-sub" style="margin-top:3px">${esc(shop.name || "אזור הניהול")}</div>
      </div>`;
  }

  function promptOwnerLogin(ownerUid) {
    openModal(`
      ${authHeader()}
      <button class="btn btn-google" data-act2="owner-google">
        <span class="g-ico">${googleIcoSvg()}</span>המשך עם Google</button>
      <div class="auth-or"><span>או עם אימייל</span></div>
      <div class="field"><label>אימייל</label>
        <input class="input" id="au-email" type="email" inputmode="email" autocomplete="username" placeholder="name@email.com"></div>
      <div class="field"><label>סיסמה</label>
        <input class="input" id="au-pass" type="password" autocomplete="current-password" placeholder="הסיסמה שלכם"></div>
      <p class="hint" id="au-err" style="min-height:15px;margin-top:0"></p>
      <button class="btn btn-primary" data-act2="do-owner-login">התחברות לניהול</button>
      <button class="btn btn-ghost btn-sm" data-act2="do-owner-reset" style="margin-top:10px;width:100%">שכחתי סיסמה</button>
      <button class="btn btn-ghost btn-sm" data-act2="do-owner-code" style="margin-top:4px;width:100%">כניסה עם קוד סודי</button>
      <button class="btn btn-ghost btn-sm" data-act="close-modal" style="margin-top:4px;width:100%">ביטול</button>
    `);
    const err = (m, good) => { const e = $("#au-err"); if (e) { e.style.color = good ? "var(--good)" : "var(--bad)"; e.textContent = m; } };
    const verify = async () => {
      if (UG.Auth.currentUid() === ownerUid) { clearGoogleIntent(); closeModal(); go("owner"); }
      else { err("החשבון הזה אינו הבעלים של המספרה"); await UG.Auth.signOut(); }
    };
    const login = async () => {
      const email = ($("#au-email") && $("#au-email").value.trim()) || "";
      const pass = ($("#au-pass") && $("#au-pass").value) || "";
      if (!email || !pass) { err("נא למלא אימייל וסיסמה"); return; }
      err("רגע…", true);
      try { await UG.Auth.signIn(email, pass); await verify(); }
      catch (e) { err(UG.Auth.humanError(e)); }
    };
    const google = async () => {
      err("מתחבר עם Google…", true);
      try {
        rememberGoogleIntent("login");
        const user = await UG.Auth.signInWithGoogle();
        if (user) await verify();   // popup הצליח; redirect ייטופל בטעינה מחדש
      } catch (e) { clearGoogleIntent(); err(UG.Auth.humanError(e)); }
    };
    const gb = $("[data-act2='owner-google']"); if (gb) gb.addEventListener("click", google);
    const reset = async () => {
      const email = ($("#au-email") && $("#au-email").value.trim()) || "";
      if (!email) { err("הזינו אימייל לשחזור"); return; }
      try { await UG.Auth.reset(email); err("נשלח מייל לאיפוס סיסמה ✓", true); }
      catch (e) { err(UG.Auth.humanError(e)); }
    };
    const lb = $("[data-act2='do-owner-login']"); if (lb) lb.addEventListener("click", login);
    const rb = $("[data-act2='do-owner-reset']"); if (rb) rb.addEventListener("click", reset);
    const cb = $("[data-act2='do-owner-code']"); if (cb) cb.addEventListener("click", promptOwnerCode);  // מסלול גיבוי: כניסה עם הקוד הסודי
    const pw = $("#au-pass"); if (pw) pw.addEventListener("keydown", (e) => { if (e.key === "Enter") login(); });
    setTimeout(() => $("#au-email") && $("#au-email").focus(), 100);
  }

  function promptOwnerCode() {
    openModal(`
      ${authHeader()}
      <div class="field pw-field">
        <input class="input" id="own-code" type="password" autocomplete="off" placeholder="סיסמת ניהול" style="text-align:center;font-size:17px">
        <button type="button" class="pw-eye" data-act="toggle-pw" aria-label="הצג/הסתר סיסמה">👁️</button>
      </div>
      <button class="btn btn-primary" data-act2="check-code">כניסה לניהול</button>
      <button class="btn btn-ghost btn-sm" data-act="close-modal" style="margin-top:8px;width:100%">ביטול</button>
    `);
    const check = async () => {
      const v = (($("#own-code") && $("#own-code").value) || "").trim();
      const shop = (Store.get() && Store.get().shop) || {};
      // קוד-על לניהול מנויים (אתה בלבד) — פותח את פאנל האדמין במקום מצב מנהל.
      // הקוד נשמר כ-hash מוצפן; משווים לפי טביעת אצבע (או טקסט גלוי לתאימות לאחור).
      const sc = UG_CONFIG.subscription || {};
      if (await adminCodeMatches(v, sc)) { closeModal(); openAdminPanel(); return; }
      const vHash = await ownerHash(SHOP, v);
      const okHash = !!(shop.ownerPassHash && vHash && vHash === shop.ownerPassHash);
      const okLegacy = !!(shop.ownerPass && v === String(shop.ownerPass));   // מספרה ותיקה
      const okConfig = (SHOP === "main") && await ownerConfigCodeMatches(v);   // config — רק למספרה הראשית
      if (okHash || okLegacy || okConfig) {
        // מיגרציה שקטה: סיסמה גלויה ישנה → hash מלוחלח, ומחיקת הגלויה
        if ((okHash || okLegacy) && (!shop.ownerPassHash || shop.ownerPass)) {
          try { await Store.setOwnerPassHash(SHOP, vHash); } catch (e) {}
        }
        closeModal(); go("owner");
      } else { toast("סיסמה שגויה", "", "🔒"); }
    };
    $("[data-act2='check-code']").addEventListener("click", check);
    $("#own-code").addEventListener("keydown", (e) => { if (e.key === "Enter") check(); });
    setTimeout(() => $("#own-code") && $("#own-code").focus(), 100);
  }

  async function saveSvc(id) {
    const name = $("#sv-name").value.trim();
    const price = Number($("#sv-price").value);
    const durationMin = Number($("#sv-dur").value);
    const icon = ($("#modal").__icon && $("#modal").__icon()) || "";   // "" = ללא אייקון
    if (!name) { toast("נא להזין שם שירות", "", "✋"); return; }
    if (!(price >= 0) || !(durationMin >= 5)) { toast("בדקו מחיר ומשך", "", "✋"); return; }
    await Store.upsertService({ id: id || undefined, name, price, durationMin, icon });
    closeModal(); toast("השירות נשמר ✓", "good", "✂️"); render();
  }

  async function saveSettings() {
    // חשוב: במבנה המסודר כל קטגוריה בעמוד נפרד, אז רק חלק מהשדות קיימים ב-DOM.
    // מעדכנים אך ורק שדות שקיימים כרגע — כדי לא לקרוס ולא לדרוס ערכים של עמוד אחר.
    const patch = {};
    const put = (sel, key, fn) => { const el = $(sel); if (el) patch[key] = fn(el); };
    put("#set-name", "name", (el) => el.value.trim() || "המספרה");
    put("#set-tag", "tagline", (el) => el.value.trim());
    put("#set-about", "about", (el) => el.value.trim());
    put("#set-addr", "address", (el) => el.value.trim());
    put("#set-phone", "phone", (el) => el.value.trim());
    put("#set-step", "slotStep", (el) => Number(el.value));
    put("#set-remind", "reminderMinutes", (el) => Number(el.value));
    put("#set-remind-day", "remindDayBefore", (el) => !!el.checked);
    put("#set-hidefree", "hideFreeBeforeMin", (el) => Number(el.value || 0));
    put("#set-showReviews", "showReviews", (el) => !!el.checked);
    put("#set-showGallery", "showGallery", (el) => !!el.checked);
    put("#set-showProducts", "showProducts", (el) => !!el.checked);
    put("#set-showHours", "showHours", (el) => !!el.checked);
    put("#set-showShare", "showShare", (el) => !!el.checked);
    SOCIAL_PLATFORMS.forEach((p) => { const el = $("#set-" + p.key); if (el) patch[p.key] = socialHandle(el.value, p.key); });
    await Store.saveShop(patch);
    toast("ההגדרות נשמרו ✓", "good", "⚙️"); render();
  }

  /* ---------- בדיקת עדכון אוטומטית ----------
     אפליקציה מותקנת בדרך כלל *ממשיכה* מהמצב הקודם במקום לטעון מחדש, ולכן קוד
     ישן נשאר בזיכרון גם אחרי שהעלינו גרסה חדשה. כאן משווים מול version.json
     (שמוגש ללא מטמון) ומרעננים פעם אחת כשיש פער. */
  let updateChecking = false;
  async function checkForUpdate(silent) {
    if (updateChecking) return;
    updateChecking = true;
    try {
      const res = await fetch("version.json?t=" + Date.now(), { cache: "no-store" });
      if (!res.ok) return;
      const live = String(((await res.json()) || {}).version || "");
      if (!live || live === APP_VERSION) return;
      // הגנה מלולאת רענון: מרעננים פעם אחת בלבד לכל גרסה
      let tried = "";
      try { tried = sessionStorage.getItem("ug_upd_try") || ""; } catch (e) {}
      if (tried === live) {
        if (!silent) toast("יש גרסה חדשה (" + live + ") — נסו ״בדיקת עדכון״ בהגדרות", "", "⚠️");
        return;
      }
      // לא קוטעים באמצע עבודה — מודאל פתוח או הקלדה. ננסה שוב בכניסה הבאה לחזית.
      const busy = ($("#modalBack") && $("#modalBack").classList.contains("open")) || isEditingRoot();
      if (busy) return;
      try { sessionStorage.setItem("ug_upd_try", live); } catch (e) {}
      toast("מתעדכן לגרסה " + live + "…", "sky", "⬆️");
      try {
        if ("caches" in window) {
          const keys = await caches.keys();
          await Promise.all(keys.map((k) => caches.delete(k)));
        }
      } catch (e) {}
      setTimeout(() => location.reload(), 500);
    } catch (e) { /* אין רשת — ננסה בפעם הבאה */ }
    finally { updateChecking = false; }
  }

  /* כפיית עדכון: מוחק את מטמון ה-Service Worker ומרענן מהרשת.
     פותר מצב שבו הדפדפן/האפליקציה המותקנת מחזיקים גרסה ישנה. */
  async function forceUpdate() {
    toast("מוריד את הגרסה החדשה…", "sky", "🔄");
    try {
      if ("caches" in window) {
        const keys = await caches.keys();
        await Promise.all(keys.map((k) => caches.delete(k)));
      }
      if (navigator.serviceWorker) {
        const regs = await navigator.serviceWorker.getRegistrations();
        await Promise.all(regs.map((r) => r.update().catch(() => {})));
      }
    } catch (e) {}
    setTimeout(() => location.reload(true), 600);
  }

  async function handleEnableNotif() {
    closeModal();   // אם הבקשה הגיעה מחלון ההזמנה — לסגור אותו לפני בקשת ההרשאה
    if (!Notify.supported()) { toast("הדפדפן אינו תומך בהתראות", "", "⚠️"); return; }
    if (Notify.permission() === "granted") {
      Notify.show("בדיקת התראה 🔔", "מצוין! ההתראות עובדות.", { tag: "test" });
      toast("נשלחה התראת בדיקה", "sky", "🔔");
      return;
    }
    const r = await Notify.requestPermission();
    if (r === "granted") {
      toast("התראות הופעלו ✓", "good", "🔔");
      ensureFcm();
      const st = Store.get();
      if (view.route === "client") Notify.scheduleReminders(st.bookings, identity.userId, st.shop);
      render();
    } else if (r === "denied") {
      toast("ההתראות נחסמו — ניתן לאפשר בהגדרות הדפדפן", "", "🔕");
    }
  }

  /* =======================================================================
     התקנה כאפליקציה (PWA) — הודעת "הוסף למסך הבית" בכניסה
     =======================================================================*/
  let deferredPrompt = null;
  // ההודעה על המסך נשארת עד שמתקינים בפועל. ה-X רק מסתיר לסשן הנוכחי (בזיכרון,
  // לא נשמר) — לכן היא חוזרת בטעינה הבאה כל עוד האפליקציה לא הותקנה.
  let installDismissedSession = false;
  function isStandalone() {
    return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  }
  /* "האם האפליקציה מותקנת" לצורך הסתרת כפתורי ההתקנה. מסך-מלא הוא הסימן הוודאי,
     אבל אחרי התקנה מהדפדפן המשתמש נשאר בלשונית רגילה (display-mode עדיין browser),
     ולכן זוכרים את אירוע ההתקנה גם ב-localStorage כדי שהכפתור לא יחזור. */
  function markInstalled() { try { localStorage.setItem("ug_installed", "1"); } catch (e) {} }
  function appInstalled() {
    if (isStandalone()) return true;
    try { return localStorage.getItem("ug_installed") === "1"; } catch (e) { return false; }
  }
  function isIOS() { return /iphone|ipad|ipod/i.test(navigator.userAgent); }
  function installSuppressed() { return installDismissedSession; }
  function suppressInstall() { installDismissedSession = true; }
  function hideInstallBar() { const b = document.getElementById("installBar"); if (b) b.classList.remove("show"); }

  function showInstallBar(mode) {
    if (appInstalled() || installSuppressed()) return;
    let bar = document.getElementById("installBar");
    if (!bar) { bar = document.createElement("div"); bar.id = "installBar"; bar.className = "install-bar"; document.body.appendChild(bar); }
    const card = (body) => `<div class="install-card"><div class="ic-ico">📲</div>${body}<button class="ic-x" data-act="install-dismiss" aria-label="סגור">✕</button></div>`;
    if (mode === "ios") {
      bar.innerHTML = card(`<div class="ic-body"><div class="ic-title">התקן את האפליקציה בטלפון</div><div class="ic-sub">לחצו על <b>שיתוף</b> ⬆️ ואז <b>״הוסף למסך הבית״</b></div></div>`);
    } else if (mode === "generic") {
      bar.innerHTML = card(`<div class="ic-body"><div class="ic-title">התקן את האפליקציה</div><div class="ic-sub">בתפריט הדפדפן (⋮) בחרו ״התקנת אפליקציה״</div></div>`);
    } else {
      bar.innerHTML = card(`<div class="ic-body"><div class="ic-title">התקן את האפליקציה</div><div class="ic-sub">גישה מהירה ממסך הבית וקבלת תזכורות</div></div><button class="btn btn-primary btn-sm" data-act="install-app" style="width:auto">התקן</button>`);
    }
    requestAnimationFrame(() => bar.classList.add("show"));
  }

  async function doInstall() {
    if (!deferredPrompt) { showInstallBar(isIOS() ? "ios" : "generic"); return; }
    deferredPrompt.prompt();
    try { await deferredPrompt.userChoice; } catch (e) {}
    deferredPrompt = null;
    hideInstallBar();
  }

  function maybeShowInstall() {
    if (appInstalled() || installSuppressed() || !cookieAccepted()) return;  // לא מעל באנר העוגיות
    if (deferredPrompt) showInstallBar("android");
    else if (isIOS()) showInstallBar("ios");
    else if (/android/i.test(navigator.userAgent)) showInstallBar("generic");
  }
  function initInstall() {
    if (appInstalled()) return;
    window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferredPrompt = e; maybeShowInstall(); });
    window.addEventListener("appinstalled", () => {
      markInstalled();                  // לזכור שהותקן — כדי שהכפתור לא יחזור בדפדפן
      hideInstallBar(); deferredPrompt = null;
      toast("האפליקציה הותקנה 🎉", "good", "📲");
      try { render(); } catch (e) {}   // להסתיר את כרטיס ההתקנה
    });
    setTimeout(maybeShowInstall, 2200);
  }

  /* =======================================================================
     הודעת עוגיות (Cookies) — מוצגת פעם אחת עד לאישור
     =======================================================================*/
  function cookieAccepted() { return localStorage.getItem("ug_cookie_ok") === "1"; }
  function hideCookieBar() { const b = document.getElementById("cookieBar"); if (b) b.classList.remove("show"); }
  function showCookieBar() {
    if (cookieAccepted()) return;
    let bar = document.getElementById("cookieBar");
    if (!bar) { bar = document.createElement("div"); bar.id = "cookieBar"; bar.className = "install-bar"; document.body.appendChild(bar); }
    bar.innerHTML = `
      <div class="install-card">
        <div class="ic-ico">🍪</div>
        <div class="ic-body">
          <div class="ic-title">אנחנו משתמשים בעוגיות</div>
          <div class="ic-sub">כדי לשמור את התורים וההעדפות שלך במכשיר ולשפר את השירות.</div>
        </div>
        <button class="btn btn-primary btn-sm" data-act="cookie-ok" style="width:auto">אישור</button>
      </div>`;
    requestAnimationFrame(() => bar.classList.add("show"));
  }
  function initCookies() { if (!cookieAccepted()) showCookieBar(); }

  /* =======================================================================
     תגובה לשינויים מהחנות (זמן אמת)
     =======================================================================*/
  function onStoreChange(st) {
    // התראת "תור חדש" לבעלים
    if (view.route === "owner") {
      if (!ownerSeen) {
        ownerSeen = new Set(st.bookings.map((b) => b.id)); // זריעה ראשונית — ללא התראה
      } else {
        const fresh = st.bookings.filter((b) => b.status !== "cancelled" && !ownerSeen.has(b.id));
        fresh.forEach((b) => {
          ownerSeen.add(b.id);
          const warn = b.priorNoShow ? " · ⚠️ לא הגיע בעבר" : "";
          const nm = bkName(b) || "לקוח";
          toast(`תור חדש: ${nm} · ${b.serviceName} ${u.relativeDay(b.date)} ${b.start}${warn}`, "sky", "🎉");
          Notify.show("📅 תור חדש נקבע", `${nm} — ${b.serviceName}\n${u.longDate(b.date)} בשעה ${b.start}${b.priorNoShow ? "\n⚠️ הלקוח לא הגיע בפעם הקודמת" : ""}`, { tag: "newbook-" + b.id });
        });
      }
      // ביטול ע״י לקוח — התראה מיידית לספר (כשהאפליקציה פתוחה)
      const clientCancels = st.bookings.filter((b) => b.status === "cancelled" && b.cancelledBy === "client");
      if (!ownerCancelSeen) {
        ownerCancelSeen = new Set(clientCancels.map((b) => b.id));   // זריעה — בלי התראה על ישנים
      } else {
        const now2 = Date.now();
        clientCancels.forEach((b) => {
          if (ownerCancelSeen.has(b.id)) return;
          ownerCancelSeen.add(b.id);
          if (u.dateTime(b.date, b.start).getTime() <= now2) return;   // רק תורים עתידיים
          const cnm = bkName(b) || "לקוח";
          toast(`${cnm} ביטל תור · ${u.relativeDay(b.date)} ${b.start}`, "", "❌");
          Notify.show("❌ לקוח ביטל תור", `${cnm} — ${b.serviceName}\n${u.longDate(b.date)} בשעה ${b.start}`, { tag: "ccancel-" + b.id });
        });
      }
    } else if (ownerSeen) {
      st.bookings.forEach((b) => ownerSeen.add(b.id));
    }
    // התראת "התור שלך בוטל" ללקוח — כשהמנהל מבטל תור עתידי של הלקוח
    if (view.route === "client") {
      const myCancelled = st.bookings.filter((b) => b.userId === identity.userId && b.status === "cancelled");
      if (!clientCancelSeen) {
        clientCancelSeen = new Set(myCancelled.map((b) => b.id));   // זריעה ראשונית — ללא התראה על ביטולים ישנים
      } else {
        const now = Date.now();
        myCancelled.forEach((b) => {
          if (clientCancelSeen.has(b.id)) return;
          clientCancelSeen.add(b.id);
          if (b.cancelledBy !== "owner") return;   // רק ביטול ע״י המנהל — לא ביטול עצמי
          if (u.dateTime(b.date, b.start).getTime() <= now - 30 * 60000) return;   // רק תורים עתידיים
          toast(`התור שלך בוטל · ${esc(b.serviceName)} ${u.relativeDay(b.date)} ${b.start}`, "", "❌");
          Notify.show("❌ התור שלך בוטל", `${st.shop.name}\n${b.serviceName} · ${u.longDate(b.date)} בשעה ${b.start}`, { tag: "cancel-" + b.id });
        });
      }
    } else if (clientCancelSeen) {
      st.bookings.forEach((b) => { if (b.status === "cancelled") clientCancelSeen.add(b.id); });
    }
    // התראת "התפנה תור" לממתינים ברשימת ההמתנה
    notifyAlerts(st);
    // תזמון תזכורות ללקוח
    if (view.route === "client" && Notify.permission() === "granted") {
      Notify.scheduleReminders(st.bookings, identity.userId, st.shop);
    }
    // רינדור מחדש (אלא אם מקלידים כרגע)
    if (!isEditingRoot()) render();
  }

  /* =======================================================================
     אתחול
     =======================================================================*/
  async function boot() {
    if (window.__ugBooted) return;              // אתחול פעם אחת בלבד (רשת ההתאוששות עשויה לקרוא ל-boot)
    window.__ugBooted = true;
    try { if (window.__ugMark) window.__ugMark("boot"); } catch (e) {}
    setupBackGuard();   // מלכודת "אחורה" — להפעיל מיד, לפני טעינת הענן
    Notify.registerSW();
    wire();
    // מניפסט לכל מספרה — לפני initInstall, כדי שהתקנה תשתמש בכתובת הנכונה
    applyShopManifest();
    initInstall();
    initCookies();

    if (SHOP === "__new__") { view.onboarding = true; render(); wizFocus(); return; }  // שאלון פתיחת מספרה

    await Store.init(SHOP);
    if (Store.notFound) { view.notFound = true; render(); return; }        // מספרה לא קיימת

    // זכירת המספרה — כדי שפתיחת האפליקציה המותקנת (ללא כתובת) תחזיר לכאן
    try { if (SHOP !== "__new__") localStorage.setItem("ug_last_shop", SHOP); } catch (e) {}
    applyShopManifest();   // מניפסט לכל מספרה — האייקון ייפתח בעמוד הנכון

    checkPaymentReturn();   // חזרה מעמוד התשלום של ספק הסליקה
    checkForUpdate(true);   // גרסה חדשה? לרענן לבד (חשוב באפליקציה מותקנת)
    // האפליקציה חזרה לחזית — בודקים שוב, כי מותקנת ממשיכה מהמצב ולא טוענת מחדש
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) checkForUpdate(true);
    });
    // אישור מדיניות פרטיות — חוסם עד שמאשרים; רק אחריו מציעים התראות
    // במודל החדש מסך הזיהוי הוא הכניסה (עם קישור מדיניות) — לא מקפיצים מודאל נוסף מעליו
    const gateShowing = newAuthShop() && view.route === "client" && !clientIdentified();
    if (!privacyAccepted() && !gateShowing) setTimeout(() => promptPrivacy(), 600);
    else setTimeout(() => promptNotif(), 1200);   // הזמנה לאישור התראות — בכל כניסה עד שיאשר
    Store.subscribe(onStoreChange);
    // מספרה מאובטחת שמנוהלת בלי חשבון הבעלים — שמירה תיחסם ע״י חוקי האבטחה.
    // במקום כישלון שקט, מציעים לספר להתחבר עם החשבון (יש גם "שכחתי סיסמה").
    if (Store.onWriteError) Store.onWriteError((err) => {
      const shop = (Store.get() && Store.get().shop) || {};
      const msg = String((err && (err.code || err.message)) || err || "");
      const denied = /permission[_ ]denied/i.test(msg);
      const notOwner = !(UG.Auth && UG.Auth.currentUid && UG.Auth.currentUid() === shop.ownerUid);
      if (denied && view.route === "owner" && shop.ownerUid && UG.Auth && authAvail && notOwner) {
        toast("כדי לשמור שינויים התחברו עם חשבון הבעלים", "", "🔒");
        if (!$("#modalBack.open")) promptOwnerLogin(shop.ownerUid);
      }
    });
    Store.subscribeGallery(() => {
      // רענון כשמסתכלים על גלריה ולא באמצע הקלדה
      const onGalleryView = (view.route === "client" && (view.clientTab === "gallery" || view.clientTab === "home")) ||
        (view.route === "owner" && view.ownerTab === "settings");
      if (onGalleryView && !isEditingRoot()) render();
    });
    // מדיה (רקע/לוגו) הגיעה מהצומת הנפרד — מרעננים כדי שהתמונות יופיעו (טעינה מתקדמת)
    if (Store.subscribeMedia) Store.subscribeMedia(() => { if (!isEditingRoot()) render(); });
    const bootShop = (Store.get() && Store.get().shop) || {};
    const secured = !!bootShop.ownerUid;   // מספרה מאובטחת בחשבון אישי (Firebase Auth)
    // כלל יחיד וברור: נכנסים לניהול רק אם יש אישור מקומי כבעלים (התחברות עם הסיסמה/קוד).
    // כך התחברות עם כתובת+סיסמה תמיד פותחת את עמוד הספר — גם למספרה מאובטחת.
    if (view.route === "owner" && localStorage.getItem(AUTHKEY) !== "1") {
      view.route = "client";
    }
    render();
    // בדיקת זמינות התחברות מאובטחת (Firebase Auth) — לרענון מסך ההגדרות + כניסה אוטומטית לבעלים
    if (UG.Auth) UG.Auth.available().then(async (a) => {
      authAvail = a;
      // חזרה מהתחברות Google בהפניה (redirect) — משלימים לפי הכוונה שנשמרה
      if (a && readGoogleIntent()) {
        try {
          const user = await UG.Auth.completeRedirect();
          const mode = readGoogleIntent();
          if (user && mode) {
            clearGoogleIntent();
            const cur = (Store.get() && Store.get().shop) || {};
            if (mode === "client") {
              applyGoogleClientIdentity(user); render();
            } else if (mode === "secure") {
              if (!cur.ownerUid) { await Store.saveShop({ ownerUid: user.uid }); }
              if (!cur.ownerUid || cur.ownerUid === user.uid) { go("owner"); toast("המספרה מאובטחת בחשבון שלך 🔒", "good", "🔒"); }
            } else if (mode === "login") {
              if (cur.ownerUid === user.uid) go("owner");
              else { toast("החשבון הזה אינו הבעלים של המספרה", "", "🔒"); await UG.Auth.signOut(); }
            }
          }
        } catch (e) {}
      }
      if (secured && a) {
        // ריענון סשן: אם המשתמש כבר מחובר כבעלים המספרה — כניסה אוטומטית לניהול
        const promote = () => {
          if (view.route !== "owner" && UG.Auth.currentUid && UG.Auth.currentUid() === bootShop.ownerUid) {
            go("owner");
          }
        };
        promote();
        UG.Auth.onChange(promote);
      }
      // רינדור מחדש אחרי שזמינות ההתחברות נקבעה — כדי שמסכי האבטחה (מודל חדש) יופיעו
      if (!isEditingRoot()) render();
    });
    // תזמון תזכורות ורישום פוש בעת עלייה
    ensureFcm();
    if (view.route === "client" && Notify.permission() === "granted") {
      Notify.scheduleReminders(Store.get().bookings, identity.userId, Store.get().shop);
    }
  }

  // חשיפת boot לרשת הביטחון (index.html) כדי שתוכל להריץ אתחול אם משהו נתקע.
  try { window.__ugBoot = boot; } catch (e) {}
  try { if (window.__ugMark) window.__ugMark("app-ready"); } catch (e) {}
  // רישום עמיד: עם defer הסקריפט רץ אחרי שה-DOM נותח (readyState="interactive"),
  // ולכן אפשר להריץ boot ישירות. אם בכל זאת המסמך עדיין בטעינה — ממתינים לאירוע.
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
