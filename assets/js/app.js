/* =========================================================================
   App — ניהול מסכים, תצוגה וחיווט אירועים
   =========================================================================*/
(function () {
  const u = UG.util;
  const Store = UG.Store;
  const Notify = UG.Notify;
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = u.escapeHtml;

  /* גרסת האפליקציה — מוצגת בהגדרות כדי לוודא שקיבלתם את העדכון האחרון.
     יש לעדכן יחד עם CACHE ב-sw.js. */
  const APP_VERSION = "73";

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
  // פתיחת כתובת חיצונית בצורה שתעבוד בדפדפן ובכל מעטפת (מפה/יומן/וואטסאפ)
  function openExternal(url) {
    try { if (isCordovaOnly()) { window.open(url, "_system"); return; } } catch (e) {}
    window.open(url, "_blank", "noopener");
  }

  /* ---------- מצב תצוגה מקומי (לא נשמר בשרת) ---------- */
  const view = {
    route: (function () { const r = localStorage.getItem(ROUTEKEY); return r === "owner" || r === "client" ? r : "client"; })(), // client | owner
    clientTab: (function () { try { const t = localStorage.getItem("ug_ctab__" + SHOP); return ["book", "gallery", "reviews", "mine"].includes(t) ? t : "book"; } catch (e) { return "book"; } })(),   // נשמר כדי לא לאבד מיקום ברענון
    ownerTab: (function () { try { return localStorage.getItem("ug_otab__" + SHOP) || "cal"; } catch (e) { return "cal"; } })(),  // cal | hours | services | bookings | clients | report | publish | settings
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
  let identity = loadIdentity();

  // כניסת מנהל נסתרת — 3 הקשות רצופות על הלוגו
  let logoTaps = 0, logoTapTimer = null;
  function onLogoTap() {
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
  function gridSlots(dateKey) {
    const st = Store.get();
    if ((st.closedDates || []).includes(dateKey)) return [];   // יום חופשה/סגירה
    const dow = u.parseKey(dateKey).getDay();
    const sched = st.schedule[dow];
    if (!sched || !sched.active) return [];
    const open = u.toMin(sched.open), close = u.toMin(sched.close);
    const step = st.shop.slotStep || 45;
    const now = new Date();
    const isToday = u.isSameDay(u.parseKey(dateKey), now);
    const nowMin = now.getHours() * 60 + now.getMinutes();
    const blocks = new Set(st.blocks || []);
    const dayBookings = st.bookings.filter((b) => b.status !== "cancelled" && b.date === dateKey);
    const slots = [];
    for (let t = open; t + step <= close; t += step) {
      const start = u.toHHMM(t), end = t + step;
      const booking = dayBookings.find((b) => {
        const bs = u.toMin(b.start), be = u.toMin(b.end);
        return t < be && end > bs;
      }) || null;
      slots.push({
        start,
        booking,
        blocked: blocks.has(dateKey + "|" + start),
        past: isToday && t <= nowMin,
      });
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
        <div class="logo-dot${st.shop.logo ? " has-img" : ""}" title="">${st.shop.logo
          ? `<img class="logo-img" src="${esc(st.shop.logo)}" alt="">`
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
  function renderClient() {
    const st = Store.get();
    const activeServices = st.services.filter((s) => s.active !== false);
    if (!view.selService || !activeServices.find((s) => s.id === view.selService)) {
      view.selService = activeServices[0] ? activeServices[0].id : null;
    }
    let body;
    if (view.clientTab === "gallery") body = clientGallery();
    else if (view.clientTab === "reviews") body = clientReviews();
    else if (view.clientTab === "mine") body = clientMine(st);
    else body = clientBook(st, activeServices);
    return `
    <div class="screen active">
      ${topbar("קביעת תור", {})}
      <div class="content" id="cscroll">${body}</div>
      <div class="tabbar">
        <button data-tab="book" class="${view.clientTab === "book" ? "active" : ""}">
          <span class="tb-ico">🗓️</span>קביעת תור</button>
        <button data-tab="gallery" class="${view.clientTab === "gallery" ? "active" : ""}">
          <span class="tb-ico">🖼️</span>גלריה</button>
        <button data-tab="reviews" class="${view.clientTab === "reviews" ? "active" : ""}">
          <span class="tb-ico">⭐</span>ביקורות</button>
        <button data-tab="mine" class="${view.clientTab === "mine" ? "active" : ""}">
          <span class="tb-ico">🎟️</span>התורים שלי</button>
      </div>
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
            <img src="${p.dataUrl}" alt="${esc(p.caption || "תספורת")}" loading="lazy">
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
    let html = `
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
  function openPhoto(id) {
    const p = Store.getGallery().find((x) => x.id === id);
    if (!p) return;
    openModal(`
      <div class="lightbox">
        <div class="lb-stage" id="lbStage">
          <img src="${p.dataUrl}" alt="${esc(p.caption || "")}" id="lbImg" draggable="false">
        </div>
        ${p.caption ? `<div class="lb-cap">${esc(p.caption)}</div>` : ""}
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
    const created = Number(st.shop.createdAt) || 0;
    if (!created) return { state: "grandfathered" };   // מספרות ותיקות — לא ננעלות
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
  // כרטיסי בחירת מסלול — אם הוגדר payUrl הכפתור מוביל לתשלום, אחרת להוראות
  function planCards() {
    const plans = subPlans();
    if (!plans.length) return "";
    return `<div class="pw-plans">` + plans.map((p) => `
      <div class="pw-plan${p.badge ? " best" : ""}">
        ${p.badge ? `<span class="pw-plan-badge">${esc(p.badge)}</span>` : ""}
        <div class="pw-plan-name">${esc(p.name || "")}</div>
        <div class="pw-plan-price">${esc(String(p.price))} <span>₪</span></div>
        <div class="pw-plan-per">${esc(p.per || "")}${p.note ? ` · ${esc(p.note)}` : ""}</div>
        ${p.payUrl
          ? `<a class="btn btn-primary btn-sm" href="${esc(p.payUrl)}" target="_blank" rel="noopener">בחירה ותשלום</a>`
          : `<button class="btn btn-primary btn-sm" data-act="show-upgrade">בחירה</button>`}
      </div>`).join("") + `</div>`;
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

  // באנר עדין במסך הניהול — ספירת ימי ניסיון / התראה על מנוי שמסתיים
  function subBanner() {
    const s = subStatus();
    if (s.state === "trial") {
      const soon = s.daysLeft <= 7;
      return `
      <div class="banner ${soon ? "warn" : "sky"}">
        <span class="bn-ico">${soon ? "⏳" : "🎁"}</span>
        <div class="bn-body">
          <div class="bn-title">תקופת ניסיון — נותרו ${s.daysLeft} ימים</div>
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
  async function adminCodeMatches(v, sc) {
    if (!v) return false;
    if (sc.adminPasscodeHash) {
      const h = await sha256Hex(v);
      return !!h && h === String(sc.adminPasscodeHash).toLowerCase();
    }
    return !!sc.adminPasscode && v === String(sc.adminPasscode);
  }

  let adminShops = [];
  async function openAdminPanel() {
    openModal(`
      <div class="m-title">🛠️ ניהול מנויים</div>
      <div class="m-sub">הפעל או חדש מנוי למספרות ששילמו</div>
      <div id="adm-list" style="max-height:60vh;overflow-y:auto;margin-top:12px">טוען…</div>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:12px">סגירה</button>
    `);
    try {
      adminShops = await Store.adminListShops();
      renderAdminList();
    } catch (e) {
      const el = $("#adm-list"); if (el) el.textContent = "שגיאה בטעינת המספרות";
    }
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

  function renderAdminList() {
    const el = $("#adm-list"); if (!el) return;
    if (!adminShops.length) { el.innerHTML = `<p class="hint">אין עדיין מספרות</p>`; return; }
    el.innerHTML = adminShops.map((s) => {
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
    const now = Date.now();
    let until = 0;
    if (months > 0) {
      const base = (s.paidUntil && s.paidUntil > now) ? s.paidUntil : now;
      const d = new Date(base);
      d.setMonth(d.getMonth() + months);
      until = d.getTime();
    }
    const ok = await Store.adminSetPaid(sid, until);
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
        <div class="svc-ico">${esc(s.icon || "✂️")}</div>
        <div class="svc-body">
          <div class="svc-name">${esc(s.name)}</div>
          <div class="svc-sub">${u.fmtDuration(s.durationMin)}</div>
        </div>
        <div class="svc-price">${u.fmtPrice(s.price)}</div>
      </button>`).join("");

    const service = services.find((s) => s.id === view.selService);

    // בורר ימים (14 יום)
    const closed = new Set(st.closedDates || []);
    const isOpen = (k) => st.schedule[u.parseKey(k).getDay()].active && !closed.has(k);
    const days = nextDays(14);
    if (!view.selDate || !days.includes(view.selDate)) {
      view.selDate = days.find(isOpen) || days[0];
    }
    const dayChips = days.map((k) => {
      const d = u.parseKey(k);
      const vac = closed.has(k);
      const off = !st.schedule[d.getDay()].active || vac;
      return `
      <button class="day-chip ${view.selDate === k ? "selected" : ""} ${off ? "off" : ""}"
              data-day="${k}" ${off ? "disabled" : ""}>
        <div class="dc-dow">${vac ? "חופשה" : off ? "סגור" : u.DOW_SHORT[d.getDay()]}</div>
        <div class="dc-num">${d.getDate()}</div>
        <div class="dc-mon">${u.MON[d.getMonth()]}</div>
      </button>`;
    }).join("");

    // שעות — רשת אחידה; מסתירים משבצות שעברו/חסומות, מסמנים תפוסות
    let slotsHtml;
    const allSlots = gridSlots(view.selDate).filter((s) => !s.past && !s.blocked);
    const hasFree = allSlots.some((s) => !s.booking);
    if (closed.has(view.selDate)) {
      slotsHtml = emptyState("🌴", "המספרה בחופשה ביום זה", "בחרו יום אחר מהיומן");
    } else if (!st.schedule[u.parseKey(view.selDate).getDay()].active) {
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
      ${st.shop.cover ? `<div class="client-cover"><img src="${esc(st.shop.cover)}" alt=""></div>` : ""}
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

      ${aboutCard(st)}
      ${hoursCard(st)}
      ${installCard()}
      ${mapsCard(st)}
      ${shareCard()}
      <p class="hint" style="text-align:center;margin-top:22px">
        מנהלים מספרה? <a href="#new" data-act="open-signup" style="color:var(--sky)">פתחו מערכת תורים משלכם ›</a>
      </p>
      <p class="hint" style="text-align:center;margin-top:8px">
        <a href="privacy.html" target="_blank" rel="noopener" style="color:var(--muted)">מדיניות פרטיות</a>
      </p>
    `;
  }

  /* כרטיס "התקן אפליקציה" בולט ללקוח — מוסתר אם כבר מותקן (מסך מלא) */
  function installCard() {
    if (isStandalone()) return "";
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

  /* ---------- כרטיס "קצת עלינו" ---------- */
  function aboutCard(st) {
    const about = (st.shop.about || "").trim();
    if (!about) return "";
    return `
      <div class="section-title">✨ קצת עלינו</div>
      <div class="card"><p style="margin:0;line-height:1.65;font-size:14.5px;white-space:pre-line">${esc(about)}</p></div>`;
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
        emptyState("🎟️", "אין לך תורים", "עברו ל״קביעת תור״ כדי לקבוע את התור הראשון");
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
        <div class="field"><label>שם פרטי</label>
          <input class="input" id="cf-first" placeholder="שם פרטי" value="${esc(identity.firstName || "")}"></div>
        <div class="field"><label>שם משפחה</label>
          <input class="input" id="cf-last" placeholder="שם משפחה" value="${esc(identity.lastName || "")}"></div>
      </div>
      <div class="field"><label>טלפון נייד</label>
        <input class="input" id="cf-phone" type="tel" inputmode="tel" placeholder="050-0000000" value="${esc(identity.phone)}"></div>
      ${(UG.Email && UG.Email.configured()) ? `
      <div class="field"><label>אימייל (לקבלת אישור למייל)</label>
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
    if (emailEl) {   // כשהמייל פעיל — הוא חובה כדי שנוכל לשלוח אישור ללקוח
      if (!email) { toast("נא להזין כתובת אימייל", "", "📧"); return null; }
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { toast("כתובת אימייל לא תקינה", "", "📧"); return null; }
    }
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
    const res = await Store.createBooking({
      serviceId: view.selService, date: bookedDate, start: bookedStart,
      userId: identity.userId, userName: contact.name, phone: contact.phone, email: contact.email,
      staff: staff,
      excludeBookingId: reschedId || undefined,   // אל תתנגש עם התור המקורי בעת שינוי מועד
    });
    if (!res.ok) {
      closeModal();
      toast(res.reason || "לא ניתן לקבוע את התור", "", "⚠️");
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
    sendBookingEmail(res.booking);   // מייל אישור ללקוח (אם מוגדר והוזן אימייל)
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

  async function handleLogoUpload(file) {
    if (!file || !file.type || file.type.indexOf("image/") !== 0) { toast("נא לבחור קובץ תמונה", "", "🖼️"); return; }
    toast("מעלה לוגו…", "sky", "⏳");
    try {
      const dataUrl = await compressLogo(file);
      await Store.saveShop({ logo: dataUrl });
      toast("הלוגו עודכן ✓ — כך יראו אותו הלקוחות", "good", "🎨");
      render();
    } catch (e) {
      toast("לא הצלחנו להעלות את הלוגו", "", "⚠️");
    }
  }

  async function handleCoverUpload(file) {
    if (!file || !file.type || file.type.indexOf("image/") !== 0) { toast("נא לבחור קובץ תמונה", "", "🖼️"); return; }
    toast("מעלה תמונת נושא…", "sky", "⏳");
    try {
      const dataUrl = await compressImage(file, 1200, 0.7);   // תמונה רחבה
      await Store.saveShop({ cover: dataUrl });
      toast("תמונת הנושא עודכנה ✓", "good", "🌄");
      render();
    } catch (e) {
      toast("לא הצלחנו להעלות את התמונה", "", "⚠️");
    }
  }

  /* ---------- קוד QR לקישור הלקוחות (מקומי, ללא רשת) ---------- */
  function qrDataUrl(text, cell, margin) {
    try {
      if (typeof qrcode === "undefined") return "";
      const qr = qrcode(0, "M");   // 0 = בחירת גרסה אוטומטית · M = תיקון שגיאות בינוני
      qr.addData(text || "");
      qr.make();
      return qr.createDataURL(cell || 6, margin == null ? 4 : margin);
    } catch (e) { return ""; }
  }
  function downloadQr() {
    const url = qrDataUrl(clientLink(), 16, 4);
    if (!url) { toast("לא ניתן ליצור קוד QR", "", "⚠️"); return; }
    try {
      const a = document.createElement("a");
      a.href = url; a.download = "barbertor-" + SHOP + "-qr.gif";
      document.body.appendChild(a); a.click(); a.remove();
      toast("קוד ה-QR הורד — אפשר להדפיס ולתלות 📷", "good", "⬇️");
    } catch (e) { toast("ההורדה נכשלה", "", "⚠️"); }
  }
  // כרטיס QR משותף (מוצג בדף הפרסום ובהגדרות)
  function qrShareCard() {
    const url = qrDataUrl(clientLink(), 6, 4);
    if (!url) return "";
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
        <div class="field"><label>שם פרטי</label>
          <input class="input" id="cf-first" placeholder="שם פרטי" value="${esc(identity.firstName || "")}"></div>
        <div class="field"><label>שם משפחה</label>
          <input class="input" id="cf-last" placeholder="שם משפחה" value="${esc(identity.lastName || "")}"></div>
      </div>
      <div class="field"><label>טלפון נייד</label>
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
    const freeNow = gridSlots(dateKey).some((s) => s.start === start && !s.booking && !s.blocked && !s.past);
    if (freeNow) {
      closeModal();
      view.selDate = dateKey; view.selSlot = start; view.clientTab = "book";
      toast("השעה התפנתה הרגע — אפשר להזמין!", "good", "🎉");
      render(); openConfirm();
      return;
    }
    await Store.joinWaitlist({
      date: dateKey, start,
      userId: identity.userId, userName: contact.name, phone: contact.phone,
    });
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
    const todayKey = u.dateKey(new Date());
    const now = Date.now();
    const todayCount = st.bookings.filter((b) => b.status !== "cancelled" && b.date === todayKey).length;
    // מנוי הסתיים — נועלים את הניהול ומציגים מסך הפעלה
    const locked = subStatus().state === "expired";
    let body;
    if (locked) body = paywallBody();
    else if (view.ownerTab === "cal") body = ownerCal(st);
    else if (view.ownerTab === "hours") body = ownerHours(st);
    else if (view.ownerTab === "services") body = ownerServices(st);
    else if (view.ownerTab === "bookings") body = ownerBookings(st);
    else if (view.ownerTab === "clients") body = ownerClients(st);
    else if (view.ownerTab === "report") body = ownerReport(st);
    else if (view.ownerTab === "publish") body = ownerPublish(st);
    else body = ownerSettings(st);

    const upcomingCount = st.bookings.filter((b) =>
      b.status !== "cancelled" && u.dateTime(b.date, b.start).getTime() > now).length;

    const banners = locked ? "" :
      subBanner() + (view.ownerTab !== "settings" ? ownerNotifBanner() : "");

    return `
    <div class="screen active">
      ${topbar("ניהול העסק", {})}
      <div class="content" id="oscroll">${banners}${body}</div>
      <div class="tabbar scroll">
        <button data-otab="cal" class="${view.ownerTab === "cal" ? "active" : ""}"><span class="tb-ico">🗓️</span>יומן</button>
        <button data-otab="hours" class="${view.ownerTab === "hours" ? "active" : ""}"><span class="tb-ico">🕐</span>שעות</button>
        <button data-otab="services" class="${view.ownerTab === "services" ? "active" : ""}"><span class="tb-ico">✂️</span>שירותים</button>
        <button data-otab="bookings" class="${view.ownerTab === "bookings" ? "active" : ""}">
          <span class="tb-ico" style="position:relative">🎟️${upcomingCount ? `<span class="badge-count" style="inset-inline-start:auto;inset-inline-end:-10px;top:-6px">${upcomingCount}</span>` : ""}</span>תורים</button>
        <button data-otab="clients" class="${view.ownerTab === "clients" ? "active" : ""}"><span class="tb-ico">👥</span>לקוחות</button>
        <button data-otab="report" class="${view.ownerTab === "report" ? "active" : ""}"><span class="tb-ico">📊</span>דוח</button>
        <button data-otab="publish" class="${view.ownerTab === "publish" ? "active" : ""}"><span class="tb-ico">📣</span>פרסום</button>
        <button data-otab="settings" class="${view.ownerTab === "settings" ? "active" : ""}"><span class="tb-ico">⚙️</span>הגדרות</button>
      </div>
    </div>`;
  }

  function timeOptions(selected) {
    let html = "";
    for (let m = 6 * 60; m <= 24 * 60; m += 15) {
      const v = u.toHHMM(m % (24 * 60) === 0 && m !== 0 ? 24 * 60 - 0 : m);
      const label = m === 24 * 60 ? "24:00" : v;
      const val = m === 24 * 60 ? "23:59" : v;
      html += `<option value="${val}" ${val === selected ? "selected" : ""}>${label}</option>`;
    }
    return html;
  }

  // תצוגת יומן יומית — כל השעות של היום הנבחר, עם אפשרות לסמן פנוי/לא-פנוי
  function ownerCal(st) {
    const days = nextDays(14);
    if (!view.oDate || !days.includes(view.oDate)) {
      view.oDate = days.find((k) => st.schedule[u.parseKey(k).getDay()].active) || days[0];
    }
    const dayChips = days.map((k) => {
      const d = u.parseKey(k);
      const off = !st.schedule[d.getDay()].active;
      return `
      <button class="day-chip ${view.oDate === k ? "selected" : ""} ${off ? "off" : ""}"
              data-oday="${k}" ${off ? "disabled" : ""}>
        <div class="dc-dow">${off ? "סגור" : u.DOW_SHORT[d.getDay()]}</div>
        <div class="dc-num">${d.getDate()}</div>
        <div class="dc-mon">${u.MON[d.getMonth()]}</div>
      </button>`;
    }).join("");

    const dow = u.parseKey(view.oDate).getDay();
    const sched = st.schedule[dow];
    let body;
    if (!sched.active) {
      body = emptyState("🚫", "היום סגור", "אפשר לפתוח את היום בלשונית ״שעות״");
    } else {
      const slots = gridSlots(view.oDate).filter((s) => !s.past);
      if (!slots.length) {
        body = emptyState("⌛", "אין שעות ליום זה", "בדקו את שעות הפעילות בלשונית ״שעות״");
      } else {
        body = `<div class="card" style="padding:6px 14px">` + slots.map((s) => {
          if (s.booking) {
            return `
            <div class="slot-line booked">
              <span class="sl-time">${s.start}</span>
              <div class="sl-mid">
                <span class="sl-name">${esc(s.booking.userName || "לקוח")}</span>
                <span class="sl-sub">${esc(s.booking.serviceName)}</span>
              </div>
              <span class="status-tag status-booked">תפוס</span>
            </div>`;
          }
          const available = !s.blocked;
          return `
          <div class="slot-line ${s.blocked ? "off" : ""}">
            <span class="sl-time">${s.start}</span>
            <div class="sl-mid"><span class="sl-state ${available ? "free" : "blocked"}">${available ? "פנוי" : "לא פנוי"}</span></div>
            <label class="switch">
              <input type="checkbox" data-block="${view.oDate}|${s.start}" ${available ? "checked" : ""}>
              <span class="track"></span><span class="thumb"></span>
            </label>
          </div>`;
        }).join("") + `</div>`;
      }
    }

    return `
      <div class="section-title">בחירת יום</div>
      <div class="days-scroll">${dayChips}</div>
      <div class="section-title">${esc(u.longDate(view.oDate))} · סימון זמינות</div>
      ${body}
      <p class="hint">כבו את המתג ליד שעה כדי לסמן אותה כ״לא פנוי״ — היא תיעלם מיד אצל הלקוחות. שעה שכבר נקבעה מסומנת ״תפוס״.</p>
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
    return `
      <div class="section-title">ימי הפעילות ושעות העבודה</div>
      <div class="card">${rows.join("")}</div>
      <p class="hint">כל שינוי נשמר מיד ומתעדכן אצל הלקוחות בזמן אמת. שעות העבודה קובעות אילו שעות מוצגות בלשונית ״יומן״.</p>

      <div class="section-title">🌴 חופשות וסגירת תאריכים</div>
      <div class="card">
        <p class="hint" style="margin-top:0;margin-bottom:11px">חסמו יום בודד או טווח (חופשה) — הלקוחות לא יוכלו להזמין בתאריכים אלה.</p>
        <div class="field-row">
          <div class="field"><label>מתאריך</label><input class="input" id="vac-from" type="date" value="${todayKey}"></div>
          <div class="field"><label>עד תאריך</label><input class="input" id="vac-to" type="date" value="${todayKey}"></div>
        </div>
        <button class="btn btn-primary btn-sm" data-act="add-vacation" style="width:100%">חסימת התאריכים</button>
        ${closedList}
      </div>
    `;
  }

  function ownerServices(st) {
    const items = st.services.map((s) => `
      <div class="card">
        <div class="service-item">
          <div class="svc-ico" style="width:44px;height:44px;border-radius:12px;display:grid;place-items:center;background:var(--surface-3);font-size:20px">${esc(s.icon || "✂️")}</div>
          <div class="si-main">
            <div class="si-name">${esc(s.name)}</div>
            <div class="si-meta"><span class="chip-price">${u.fmtPrice(s.price)}</span><span class="pill">⏱ ${u.fmtDuration(s.durationMin)}</span></div>
          </div>
          <button class="icon-btn" data-act="edit-svc" data-id="${s.id}">✏️</button>
        </div>
      </div>`).join("");
    return `
      <div class="section-title">השירותים שאתה מציע</div>
      ${items || emptyState("✂️", "אין שירותים", "הוסיפו את השירות הראשון")}
      <div style="height:14px"></div>
      <button class="btn btn-primary" data-act="add-svc">＋ הוספת שירות</button>
      <p class="hint">שם השירות, המחיר והמשך מתעדכנים אצל כל הלקוחות מיד.</p>
    `;
  }

  function svcModal(existing) {
    const s = existing || { name: "", price: "", durationMin: 30, icon: "✂️" };
    const icons = ["✂️", "🧔", "💈", "🪒", "👦", "💇‍♂️", "💇‍♀️", "✨"];
    openModal(`
      <div class="m-title">${existing ? "עריכת שירות" : "שירות חדש"}</div>
      <div class="m-sub">הפרטים יופיעו אצל הלקוחות</div>
      <div class="field"><label>סוג התספורת / השירות</label>
        <input class="input" id="sv-name" placeholder="לדוגמה: תספורת גבר" value="${esc(s.name)}"></div>
      <div class="field-row">
        <div class="field"><label>מחיר (₪)</label>
          <input class="input" id="sv-price" type="number" inputmode="numeric" min="0" placeholder="60" value="${esc(s.price)}"></div>
        <div class="field"><label>משך (דקות)</label>
          <input class="input" id="sv-dur" type="number" inputmode="numeric" min="5" step="5" placeholder="30" value="${esc(s.durationMin)}"></div>
      </div>
      <div class="field"><label>אייקון</label>
        <div style="display:flex;gap:8px;flex-wrap:wrap" id="sv-icons">
          ${icons.map((ic) => `<button class="btn btn-sm" data-ic="${ic}" style="width:46px;font-size:20px;${ic === s.icon ? "border-color:var(--sky);box-shadow:0 0 0 2px var(--sky-glow)" : ""}">${ic}</button>`).join("")}
        </div>
      </div>
      <button class="btn btn-primary" data-act="save-svc" data-id="${existing ? existing.id : ""}">שמירה</button>
      ${existing ? `<button class="btn btn-danger" data-act="del-svc" data-id="${existing.id}" style="margin-top:8px">מחיקת שירות</button>` : `<button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>`}
    `);
    // בחירת אייקון
    let chosen = s.icon;
    $("#sv-icons").addEventListener("click", (e) => {
      const b = e.target.closest("[data-ic]"); if (!b) return;
      chosen = b.dataset.ic;
      [...$("#sv-icons").children].forEach((c) => (c.style.cssText = "width:46px;font-size:20px;"));
      b.style.cssText = "width:46px;font-size:20px;border-color:var(--sky);box-shadow:0 0 0 2px var(--sky-glow)";
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
      const stg = b.status === "noshow"
        ? `<span class="status-tag status-noshow">❌ לא הגיע</span>`
        : b.status === "confirmed"
        ? `<span class="status-tag status-confirmed">✓ אישר הגעה</span>`
        : `<span class="status-tag status-booked">ממתין</span>`;
      const action = !isPast
        ? `<button class="btn btn-sm btn-danger" data-act="owner-cancel" data-id="${b.id}">בטל</button>`
        : (b.status === "noshow"
            ? `<button class="btn btn-sm" data-act="owner-unnoshow" data-id="${b.id}">בטל סימון</button>`
            : `<div class="bk-acts">
                 ${b.status !== "confirmed" ? `<button class="btn btn-sm" data-act="owner-confirm" data-id="${b.id}">✓ הגיע</button>` : ""}
                 <button class="btn btn-sm" data-act="owner-noshow" data-id="${b.id}">לא הגיע</button>
               </div>`);
      return `
      <div class="booking" style="${isPast ? "opacity:.6" : ""}">
        <div class="bk-time">
          <div class="bt-h">${esc(b.start)}</div>
          <div class="bt-d">${esc(u.relativeDay(b.date))}</div>
        </div>
        <div class="bk-body">
          <div class="bk-title">${esc(b.userName || "לקוח")}</div>
          <div class="bk-sub">${esc(b.serviceName)} · ${b.phone ? `<a href="tel:${esc(b.phone)}">${esc(b.phone)}</a>` : "ללא טלפון"}</div>
          <div class="bk-sub">${esc(u.longDate(b.date))}${b.staff ? ` · <span class="staff-req">🧑‍🔧 ביקש: ${esc(b.staff)}</span>` : ""}</div>
          ${b.priorNoShow ? `<div class="noshow-warn">⚠️ הלקוח לא הגיע בעבר${b.priorNoShow > 1 ? ` (${b.priorNoShow} פעמים)` : ""}</div>` : ""}
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
    $("#modal").innerHTML = `<div class="m-handle"></div>` + addBookingHtml();
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
          return `<button class="slot taken" disabled>${s.start}<span class="slot-tag">${esc(s.booking.userName || "תפוס")}</span></button>`;
        }
        const tag = s.blocked ? `<span class="slot-tag show">חסום</span>` : (s.past ? `<span class="slot-tag show">עבר</span>` : "");
        return `<button class="slot ${(!a.custom && a.start === s.start) ? "selected" : ""} ${s.past ? "slot-past" : ""}" data-abslot="${s.start}">${s.start}${tag}</button>`;
      }).join("") + `</div>`;
    }

    const svcOptions = st.services.map((s) =>
      `<option value="${s.id}" ${a.svcId === s.id ? "selected" : ""}>${esc(s.name)} · ${u.fmtDuration(s.durationMin)}</option>`).join("");
    const hourOptions = Array.from({ length: 24 }, (_, h) => { const v = String(h).padStart(2, "0"); return `<option value="${v}" ${a.hour === v ? "selected" : ""}>${v}</option>`; }).join("");
    const minOptions = ["00", "15", "30", "45"].map((m) => `<option value="${m}" ${a.min === m ? "selected" : ""}>${m}</option>`).join("");
    const contacts = (st.contacts || []).slice().sort((x, y) => (x.name || "").localeCompare(y.name || "", "he"));
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
    sendBookingEmail(res.booking);   // מייל אישור ללקוח (אם מוגדר והוזן אימייל)
    addBk = null;
    render();
  }

  /* ---------- רשימת לקוחות (CRM) ---------- */
  function clientKey(b) { return (b.phone && u.normalizePhone(b.phone)) || b.userId || b.userName || "לקוח"; }

  function ownerClients(st) {
    const map = new Map();
    // אנשי הקשר שיובאו — מופיעים גם אם עדיין לא הזמינו תור
    (st.contacts || []).forEach((c) => {
      const key = (c.phone && u.normalizePhone(c.phone)) || c.name || c.id;
      if (!map.has(key)) map.set(key, {
        key, name: c.name || "לקוח", phone: c.phone || "", visits: 0, spent: 0,
        lastTs: 0, lastDate: null, contactId: c.id, imported: true,
      });
    });
    st.bookings.filter((b) => b.status !== "cancelled").forEach((b) => {
      const key = clientKey(b);
      let c = map.get(key);
      if (!c) { c = { key, name: b.userName || "לקוח", phone: b.phone || "", visits: 0, spent: 0, lastTs: 0, lastDate: b.date }; map.set(key, c); }
      if (b.userName) c.name = b.userName;
      if (b.phone) c.phone = b.phone;
      if (b.status === "confirmed") { c.visits++; c.spent += Number(b.price || 0); }
      if (b.status === "noshow") c.noShows = (c.noShows || 0) + 1;
      const ts = u.dateTime(b.date, b.start).getTime();
      if (ts > c.lastTs) { c.lastTs = ts; c.lastDate = b.date; c.imported = false; }
    });
    const clients = [...map.values()].sort((a, z) => z.lastTs - a.lastTs);
    const importBtn = `<button class="btn btn-primary" data-act="import-clients" style="margin-bottom:14px">📥 ייבוא רשימת לקוחות</button>`;
    if (!clients.length) return importBtn + emptyState("👥", "אין עדיין לקוחות", "ייבאו את רשימת הלקוחות שלכם, או שהם יופיעו כאן אחרי שיזמינו תור");
    const actionsRow = `
      <div class="btn-row" style="margin-bottom:14px">
        <button class="btn btn-primary" data-act="broadcast">📢 הודעה לכולם</button>
        <button class="btn" data-act="import-clients">📥 ייבוא רשימה</button>
      </div>`;
    const totalSpent = clients.reduce((s, c) => s + c.spent, 0);
    const totalVisits = clients.reduce((s, c) => s + c.visits, 0);
    return `
      ${actionsRow}
      <div class="stat-chips">
        <div class="stat-chip"><div class="sc-num">${clients.length}</div><div class="sc-lbl">לקוחות</div></div>
        <div class="stat-chip"><div class="sc-num">${totalVisits}</div><div class="sc-lbl">ביקורים</div></div>
        <div class="stat-chip"><div class="sc-num">${u.fmtPrice(totalSpent)}</div><div class="sc-lbl">סה״כ</div></div>
      </div>
      <div class="section-title">כל הלקוחות</div>
      ${clients.map((c) => `
        <div class="card" style="padding:13px 15px">
          <div style="display:flex;align-items:center;gap:12px">
            <div class="cli-ava">${esc((String(c.name).trim()[0]) || "?")}</div>
            <div style="flex:1;min-width:0">
              <div class="bk-title">${esc(c.name)}${c.imported ? ` <span class="cli-badge">מיובא</span>` : ""}</div>
              <div class="bk-sub">${c.phone ? `<a href="tel:${esc(c.phone)}">${esc(c.phone)}</a>` : "ללא טלפון"}</div>
              <div class="bk-sub">${c.imported ? "עדיין לא הזמין תור" : `${c.visits} ביקורים · <b>${u.fmtPrice(c.spent)}</b> · אחרון ${esc(u.relativeDay(c.lastDate))}`}${c.noShows ? ` · <span class="noshow-cnt">❌ ${c.noShows} לא הגיע</span>` : ""}</div>
            </div>
            ${c.imported
              ? `<button class="btn btn-sm" data-act="del-contact" data-id="${esc(c.contactId)}">הסר</button>`
              : `<button class="btn btn-sm" data-act="client-detail" data-key="${esc(c.key)}">פרטים</button>`}
          </div>
        </div>`).join("")}
      ${clients.some((c) => c.imported) ? `
      <div class="info-note">
        <b>ℹ️ מה זה "לקוח מיובא"?</b>
        <p>לקוח שהוספת מרשימת הלקוחות שלך אך עדיין לא הזמין תור. ברגע שיזמין תור (או שתקבע לו תור ידני) — הוא יהפוך ללקוח מלא עם היסטוריית ביקורים והכנסות, והתג ייעלם.</p>
      </div>` : ""}
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
    (st.contacts || []).forEach((c) => {
      const p = u.normalizePhone(c.phone || "");
      if (p && !map.has(p)) map.set(p, { name: c.name || "לקוח", phone: p });
    });
    (st.bookings || []).filter((b) => b.status !== "cancelled").forEach((b) => {
      const p = u.normalizePhone(b.phone || "");
      if (!p) return;
      const cur = map.get(p);
      if (cur) { if (b.userName) cur.name = b.userName; }
      else map.set(p, { name: b.userName || "לקוח", phone: p });
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
    const bks = st.bookings.filter((b) => b.status !== "cancelled" && clientKey(b) === key)
      .sort((a, z) => u.dateTime(z.date, z.start) - u.dateTime(a.date, a.start));
    if (!bks.length) return;
    const name = bks[0].userName || "לקוח";
    const phone = (bks.find((b) => b.phone) || {}).phone || "";
    const spent = bks.filter((b) => b.status === "confirmed").reduce((s, b) => s + Number(b.price || 0), 0);
    openModal(`
      <div class="m-title">${esc(name)}</div>
      <div class="m-sub">${bks.length} תורים · ${u.fmtPrice(spent)} סה״כ${phone ? ` · <a href="tel:${esc(phone)}">${esc(phone)}</a>` : ""}</div>
      <div style="max-height:52vh;overflow-y:auto;margin-top:8px">
        ${bks.map((b) => `
          <div class="summary-row">
            <span class="sr-k">${esc(u.longDate(b.date))} · ${esc(b.start)}</span>
            <span class="sr-v">${esc(b.serviceName)} · ${u.fmtPrice(b.price)} ${b.status === "confirmed" ? "✓" : ""}</span>
          </div>`).join("")}
      </div>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:14px">סגירה</button>
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
              <td class="st-name">${esc(x.b.userName || "לקוח")}</td>
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

    return `
      <div class="month-nav">
        <button class="icon-btn" data-act="stat-prev" title="חודש קודם">‹</button>
        <div class="mn-label">${ymLabel(ym)}</div>
        <button class="icon-btn" data-act="stat-next" title="חודש הבא" ${isCur ? "disabled" : ""}>›</button>
      </div>
      <div class="stat-chips">
        <div class="stat-chip"><div class="sc-num">${rows.length}</div><div class="sc-lbl">תספורות שאושרו</div></div>
        <div class="stat-chip"><div class="sc-num">${u.fmtPrice(total)}</div><div class="sc-lbl">הכנסות החודש</div></div>
      </div>
      ${table}
      <p class="hint">הדוח מציג תורים שהלקוח אישר בהם הגעה. בתחילת כל חודש הטבלה מתחילה מאפס — אפשר לדפדף לחודשים קודמים עם החצים.</p>
      <div class="section-title">ביקורות לקוחות${avg ? ` · ממוצע ${avg} ★` : ""}</div>
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
      return `
        <div class="section-title">🔒 אבטחה</div>
        <div class="card">
          <div class="conn-line" style="margin-bottom:10px"><span class="conn-dot"></span>
            המספרה מאובטחת בחשבון אישי${email ? ` · ${esc(email)}` : ""}</div>
          <p class="hint" style="margin-top:0">מעכשיו הכניסה לניהול היא עם החשבון הזה. (בשלב הבא נבטל את הקוד הישן ונחזק את חוקי מסד הנתונים.)</p>
          <button class="btn btn-sm btn-danger" data-act="auth-signout" style="margin-top:10px">התנתקות מהחשבון</button>
        </div>`;
    }
    return `
      <div class="section-title">🔒 אבטחה</div>
      <div class="card">
        <p class="hint" style="margin-top:0">הגנו על המספרה עם חשבון אישי (אימייל+סיסמה) במקום קוד משותף.</p>
        <button class="btn btn-primary" data-act="secure-shop" style="margin-top:10px">🔒 הגנה על המספרה שלי</button>
      </div>`;
  }

  function openSecureShop() {
    openModal(`
      <div class="m-title">🔒 הגנה על המספרה</div>
      <div class="m-sub">התחברו או הירשמו עם אימייל וסיסמה</div>
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
        const uid = UG.Auth.currentUid();
        const shop = Store.get().shop;
        if (shop.ownerUid && shop.ownerUid !== uid) {
          if (errEl) errEl.textContent = "המספרה כבר מאובטחת בחשבון אחר";
          await UG.Auth.signOut(); return;
        }
        if (!shop.ownerUid) await Store.saveShop({ ownerUid: uid });
        closeModal(); toast("המספרה מאובטחת בחשבון שלך 🔒", "good", "🔒"); render();
      } catch (e) { if (errEl) errEl.textContent = UG.Auth.humanError(e); }
    };
    const lb = $("[data-act2='do-secure-login']"); if (lb) lb.addEventListener("click", () => run("login"));
    const sb = $("[data-act2='do-secure-signup']"); if (sb) sb.addEventListener("click", () => run("signup"));
    setTimeout(() => $("#au-email") && $("#au-email").focus(), 100);
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
              <img src="${p.dataUrl}" loading="lazy">
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
      <div class="m-sub">${esc(b.userName || "לקוח")} · ${esc(b.serviceName)} · ${esc(u.longDate(b.date))}</div>
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
    return `
      <div class="pub-hero">
        <div class="pub-ico${st.shop.logo ? " has-img" : ""}">${st.shop.logo
          ? `<img class="pub-logo" src="${esc(st.shop.logo)}" alt="">` : "📣"}</div>
        <h2>${owner ? esc(owner) + ", המספרה שלך מוכנה!" : "המספרה שלך מוכנה!"}</h2>
        <p>שלח/י את הקישור הזה ללקוחות — הם יזמינו תור לבד, ישירות מהטלפון.</p>
      </div>

      <div class="section-title">🔗 הקישור האישי שלך</div>
      <div class="card">
        <div class="pub-link">${esc(link)}</div>
        <div class="btn-row btn-row-wrap" style="margin-top:13px">
          <button class="btn btn-primary btn-sm" data-act="copy-link">📋 העתקה</button>
          <button class="btn btn-sm" data-act="share-app">🔗 שיתוף</button>
          <button class="btn btn-wa btn-sm" data-act="share-wa"><svg class="wa-ico" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M.057 24l1.687-6.163a11.867 11.867 0 01-1.587-5.945C.16 5.335 5.495 0 12.05 0a11.82 11.82 0 018.413 3.488 11.824 11.824 0 013.48 8.414c-.003 6.557-5.338 11.892-11.893 11.892a11.9 11.9 0 01-5.688-1.448L.057 24zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884-.001 2.225.651 3.891 1.746 5.634l-.999 3.648 3.742-.981zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.767.967-.94 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372s-1.04 1.016-1.04 2.479 1.065 2.876 1.213 3.074c.149.198 2.095 3.2 5.076 4.487.709.306 1.263.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.695.248-1.29.173-1.414z"/></svg>וואטסאפ</button>
        </div>
      </div>

      ${qrShareCard()}

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
      </div>

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
      </div>

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

  function ownerSettings(st) {
    const logo = st.shop.logo || "";
    const cover = st.shop.cover || "";
    return `
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
      </div>
      <div class="section-title">🌄 תמונת נושא (קאבר)</div>
      <div class="card">
        <div class="cover-preview${cover ? " has-img" : ""}">${cover ? `<img src="${esc(cover)}" alt="קאבר">` : "🌄 אין תמונת נושא"}</div>
        <div class="hint" style="margin:11px 0">תמונה רחבה שתופיע בראש עמוד ההזמנה של הלקוחות — נותנת מראה מקצועי.</div>
        <div class="btn-row">
          <button class="btn btn-primary btn-sm" data-act="cover-pick">${cover ? "החלפת תמונה" : "העלאת תמונה"}</button>
          ${cover ? `<button class="btn btn-danger btn-sm" data-act="cover-remove">הסרה</button>` : ""}
        </div>
        <input type="file" accept="image/*" data-coverfile style="display:none">
      </div>
      <div class="section-title">🔗 הקישור שלך ללקוחות</div>
      <div class="card">
        <div class="hint" style="margin-bottom:10px">שלחו את הקישור הזה ללקוחות — הוא פותח את המספרה שלכם:</div>
        <div style="word-break:break-all;font-weight:700;font-size:13.5px;color:var(--sky-2)">${esc(clientLink())}</div>
        <div class="btn-row" style="margin-top:12px">
          <button class="btn btn-sm" data-act="copy-link">📋 העתקה</button>
          <button class="btn btn-sm" data-act="share-app">🔗 שיתוף</button>
          <button class="btn btn-wa btn-sm" data-act="share-wa"><svg class="wa-ico" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M.057 24l1.687-6.163a11.867 11.867 0 01-1.587-5.945C.16 5.335 5.495 0 12.05 0a11.82 11.82 0 018.413 3.488 11.824 11.824 0 013.48 8.414c-.003 6.557-5.338 11.892-11.893 11.892a11.9 11.9 0 01-5.688-1.448L.057 24zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884-.001 2.225.651 3.891 1.746 5.634l-.999 3.648 3.742-.981zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.767.967-.94 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372s-1.04 1.016-1.04 2.479 1.065 2.876 1.213 3.074c.149.198 2.095 3.2 5.076 4.487.709.306 1.263.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.695.248-1.29.173-1.414z"/></svg>וואטסאפ</button>
        </div>
      </div>
      ${qrShareCard()}
      <div class="section-title">🎨 סגנון העיצוב</div>
      <div class="card">
        <p class="hint" style="margin-top:0;margin-bottom:12px">כך ייראה האתר — אצלכם ואצל הלקוחות.</p>
        <div class="style-picker">${WIZ_STYLES.map((s) => `
          <button type="button" class="style-opt ${((st.shop.style || "sky") === s.id) ? "selected" : ""}" data-act="set-style" data-style="${s.id}">
            <span class="style-swatch" style="background:linear-gradient(145deg, ${s.c1}, ${s.c2})">${s.emoji}</span>
            <span class="so-body"><span class="so-name">${esc(s.name)}</span><span class="hint" style="display:block">${esc(s.desc)}</span></span>
            <span class="so-check">✓</span>
          </button>`).join("")}</div>
      </div>
      <div class="section-title">🧑‍🔧 ספרים במספרה</div>
      <div class="card">
        <p class="hint" style="margin-top:0;margin-bottom:${(st.shop.staff || []).length ? "10px" : "12px"}">${(st.shop.staff || []).length
          ? "הלקוחות יכולים לבקש ספר מסוים בעת ההזמנה (בקשה בלבד — לא התחייבות)."
          : "אין ספרים מוגדרים. הוסיפו שמות כדי לאפשר ללקוח לבחור ספר מועדף."}</p>
        ${(st.shop.staff || []).length ? `<div class="staff-chips">${st.shop.staff.map((n) => `<span class="staff-chip">🧑 ${esc(n)}</span>`).join("")}</div>` : ""}
        <button class="btn btn-sm" data-act="edit-staff" style="margin-top:12px">${(st.shop.staff || []).length ? "עריכת רשימת הספרים" : "＋ הוספת ספרים"}</button>
      </div>
      ${ownerSecuritySection(st)}
      ${ownerGallerySection()}
      <div class="section-title">פרטי העסק</div>
      <div class="card">
        <div class="field"><label>שם העסק</label>
          <input class="input" id="set-name" value="${esc(st.shop.name)}"></div>
        <div class="field"><label>תיאור קצר</label>
          <input class="input" id="set-tag" value="${esc(st.shop.tagline || "")}"></div>
        <div class="field"><label>קצת עלינו (יוצג ללקוחות בעמוד ההזמנה)</label>
          <textarea class="input" id="set-about" rows="3" placeholder="ספרו על המספרה — ותק, התמחות, אווירה…" style="resize:vertical;line-height:1.6">${esc(st.shop.about || "")}</textarea></div>
        <div class="field"><label>כתובת המספרה (לכפתור ״איך מגיעים״)</label>
          <input class="input" id="set-addr" value="${esc(st.shop.address || "")}" placeholder="רבי טרפון 12, ירושלים"></div>
        <div class="field-row">
          <div class="field"><label>טלפון</label>
            <input class="input" id="set-phone" type="tel" value="${esc(st.shop.phone || "")}"></div>
          <div class="field"><label>מרווח בין תורים</label>
            <select class="input" id="set-step">
              ${[30, 45, 60].map((n) => `<option value="${n}" ${st.shop.slotStep === n ? "selected" : ""}>${n} דקות</option>`).join("")}
            </select>
          </div>
        </div>
        <div class="field"><label>שליחת תזכורת ללקוח — כמה זמן לפני התור</label>
          <select class="input" id="set-remind">
            ${[30, 60, 90, 120].map((n) => `<option value="${n}" ${st.shop.reminderMinutes === n ? "selected" : ""}>${n} דקות לפני</option>`).join("")}
          </select>
        </div>
        <button class="btn btn-primary" data-act="save-settings">שמירת הגדרות</button>
      </div>

      <div class="section-title">התראות</div>
      <div class="card">
        <div class="conn-line" style="margin-bottom:12px">
          <span class="conn-dot ${Notify.permission() === "granted" ? "" : "local"}"></span>
          ${Notify.permission() === "granted" ? "התראות פעילות — תקבל הודעה על כל תור חדש" : "התראות כבויות"}
        </div>
        <button class="btn" data-act="enable-notif">${Notify.permission() === "granted" ? "בדיקת התראה" : "אפשר קבלת התראות על תורים חדשים"}</button>
      </div>

      <div class="section-title">חיבור</div>
      <div class="card">
        <div class="conn-line">
          <span class="conn-dot ${Store.mode === "cloud" ? "" : "local"}"></span>
          ${Store.mode === "cloud" ? "מחובר לענן (Firebase) — סנכרון מלא בין כל המכשירים" : "מצב מקומי — לסנכרון בין מכשירים ראו את קובץ README"}
        </div>
        <div class="conn-line" style="margin-top:10px">
          <span class="conn-dot"></span>גרסה ${APP_VERSION}
        </div>
        <button class="btn btn-sm" data-act="force-update" style="margin-top:12px">🔄 בדיקת עדכון</button>
      </div>

      <div class="section-title">יציאה</div>
      <div class="card">
        <p class="hint" style="margin-top:0">יציאה מהניהול במכשיר הזה — שימושי במכשיר משותף או להחלפת מספרה. הנתונים נשמרים; כדי להיכנס שוב צריך את הכתובת והסיסמה.</p>
        <button class="btn btn-danger" data-act="owner-logout" style="margin-top:12px">🚪 יציאה / החלפת מספרה</button>
      </div>

      <p class="hint" style="text-align:center;margin-top:20px">
        <a href="privacy.html" target="_blank" rel="noopener" style="color:var(--muted)">מדיניות פרטיות</a>
        · BarberTor
      </p>
    `;
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

  /* =======================================================================
     מצב ריק
     =======================================================================*/
  function emptyState(ico, title, sub) {
    return `<div class="empty"><div class="em-ico">${ico}</div><b>${esc(title)}</b><p>${esc(sub)}</p></div>`;
  }

  /* =======================================================================
     רינדור ראשי
     =======================================================================*/
  function render() {
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
    $("#root").innerHTML = view.route === "owner" ? renderOwner() : renderClient();
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
    step: 0, busy: false,
    data: {
      owner: "", name: "", handle: "", phone: "", city: "", street: "", houseNo: "", address: "",
      services: [{ name: "תספורת גבר", price: 60, durationMin: 30 }],
      multiStaff: false, staff: [""],
      style: "sky", pass: "", pass2: "",
    },
  };
  // הרכבת כתובת מלאה מהשדות הנפרדים (רחוב + מספר, עיר)
  function wizComposeAddress(d) {
    const line1 = [d.street, d.houseNo].filter(Boolean).join(" ").trim();
    return [line1, d.city].filter(Boolean).join(", ").trim();
  }
  const WIZ_QUESTIONS = 8;   // שלבים 1..8 הם שאלות

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
        return wizQ("🎨", "בחרו סגנון עיצוב", "ככה ייראה האתר שלכם — גם אצלכם וגם אצל הלקוחות. אפשר לשנות בכל רגע מההגדרות.",
          `<div class="style-picker">${WIZ_STYLES.map((s) => `
             <button type="button" class="style-opt ${d.style === s.id ? "selected" : ""}" data-act="wiz-style" data-style="${s.id}">
               <span class="style-swatch" style="background:linear-gradient(145deg, ${s.c1}, ${s.c2})">${s.emoji}</span>
               <span class="so-body"><span class="so-name">${esc(s.name)}</span><span class="hint" style="display:block">${esc(s.desc)}</span></span>
               <span class="so-check">✓</span>
             </button>`).join("")}</div>`);
      case 8:
        return wizQ("🔒", "סיסמת ניהול", "רק איתה נכנסים לנהל את המספרה. שמור/י אותה במקום בטוח!",
          `<div class="pw-field">
             <input class="input wiz-input" id="wz-pass" type="password" placeholder="בחר/י סיסמה" value="${esc(d.pass)}">
             <button type="button" class="pw-eye" data-act="toggle-pw" aria-label="הצג סיסמה">👁️</button>
           </div>
           <input class="input wiz-input" id="wz-pass2" type="password" placeholder="הקלד/י שוב לאימות" value="${esc(d.pass2 || "")}" style="margin-top:10px">`);
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
      : `<button class="btn btn-primary btn-lg" data-act="wiz-next">${isLast ? "סיום ובניית המספרה ✨" : "המשך ›"}</button>
         ${wiz.step === 4 ? `<button class="btn btn-ghost btn-sm" data-act="wiz-skip" style="margin-top:8px;width:100%">דלג/י על זה</button>` : ""}
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
    // Enter = המשך (למעט שלב השירותים, שם יש כמה שדות בשורה)
    const body = $("#wizBody");
    if (body && !body.__bound) {
      body.__bound = true;
      body.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && wiz.step !== 5) { e.preventDefault(); wizNext(); }
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
    if (wiz.step === 7) { wizGo(8); return; }
    if (wiz.step === 8) {
      d.pass = ($("#wz-pass") && $("#wz-pass").value.trim()) || "";
      d.pass2 = ($("#wz-pass2") && $("#wz-pass2").value.trim()) || "";
      if (d.pass.length < 4) { toast("סיסמה קצרה מדי (לפחות 4 תווים)", "", "✋"); haptic(40); return; }
      if (d.pass !== d.pass2) { toast("הסיסמאות אינן תואמות", "", "🔁"); haptic(40); return; }
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
    // שמירת מה שהוקלד לפני חזרה
    const map = { 1: ["owner", "#wz-owner"], 2: ["name", "#wz-name"], 3: ["handle", "#wz-handle"], 8: ["pass", "#wz-pass"] };
    const m = map[wiz.step];
    if (m && $(m[1])) wiz.data[m[0]] = $(m[1]).value.trim();
    if (wiz.step === 8 && $("#wz-pass2")) wiz.data.pass2 = $("#wz-pass2").value.trim();
    if (wiz.step === 4) wizCaptureStep4();
    if (wiz.step === 5) wizCaptureServices();
    if (wiz.step === 6) wizCaptureStaff();
    wizGo(wiz.step - 1);
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
    const res = await Store.createShop(d.handle, {
      name: d.name, ownerPass: d.pass, phone: d.phone, address: d.address, ownerName: d.owner,
      style: d.style, services: d.services, staff: d.multiStaff ? d.staff : [],
    });
    clearInterval(timer);
    if (!res.ok) {
      toast(res.reason || "שגיאה ביצירת המספרה", "", "⚠️");
      wizGo(3);
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
    try { localStorage.setItem("ug_my_shop", d.handle); } catch (e) {}
    setTimeout(() => { location.hash = d.handle; location.reload(); }, 800);
  }

  function wizExisting() {
    openModal(`
      ${authHeader()}
      <div class="field"><label>הכתובת האישית שלך</label>
        <input class="input" id="lg-handle" placeholder="dani" autocapitalize="off" autocomplete="off" spellcheck="false"></div>
      <div class="field pw-field"><label>סיסמת ניהול</label>
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
      const configCodes = [UG_CONFIG.ownerPasscode].concat(UG_CONFIG.ownerPasscodesExtra || []).map(String);
      const ok = (data.shop.ownerPass && pass === String(data.shop.ownerPass)) ||
        (handle === "main" && configCodes.includes(pass));   // סיסמאות אורי (config) עובדות למספרה הראשית
      if (!ok) { err("סיסמה שגויה"); haptic(40); return; }
      // כניסה מוצלחת — ישר לניהול
      localStorage.setItem("ug_owner_auth__" + handle, "1");
      localStorage.setItem("ug_route__" + handle, "owner");
      // המספרה שבבעלות המכשיר הזה — פתיחת האפליקציה תוביל ישר לניהול שלה (כניסת הספר)
      try { localStorage.setItem("ug_my_shop", handle); } catch (e) {}
      haptic(16);
      location.hash = handle; location.reload();
    };
    const lb = $("[data-act2='do-existing-login']"); if (lb) lb.addEventListener("click", login);
    const pw = $("#lg-pass"); if (pw) pw.addEventListener("keydown", (e) => { if (e.key === "Enter") login(); });
    setTimeout(() => $("#lg-handle") && $("#lg-handle").focus(), 100);
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
    const res = await Store.createShop(handle, { name: name, ownerPass: pass });
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

      const t = e.target.closest("[data-act],[data-svc],[data-day],[data-oday],[data-slot],[data-wait],[data-photo],[data-delphoto],[data-tab],[data-otab],[data-active],[data-abday],[data-abslot]");
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
      if (t.dataset.tab) { view.clientTab = t.dataset.tab; render(); return; }
      if (t.dataset.otab) {
        view.ownerTab = t.dataset.otab;
        try { localStorage.setItem("ug_otab__" + SHOP, view.ownerTab); } catch (e2) {}
        render(); return;
      }

      const act = t.dataset.act;
      if (!act) return;

      switch (act) {
        case "close-modal": closeModal(); break;

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
        case "notif-help": notifHelp(); break;
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
        case "auth-signout":
          if (UG.Auth) UG.Auth.signOut().finally(() => { toast("התנתקת מהחשבון", "", "🔓"); render(); });
          break;
        case "install-app": doInstall(); break;
        case "install-dismiss": suppressInstall(3); hideInstallBar(); break;
        case "cookie-ok":
          localStorage.setItem("ug_cookie_ok", "1"); hideCookieBar();
          setTimeout(maybeShowInstall, 400); break;
        case "add-cal": addToCalendar(t.dataset.id); break;
        case "qr-download": downloadQr(); break;
        case "logo-pick": { const inp = $("[data-logofile]"); if (inp) inp.click(); break; }
        case "logo-remove":
          await Store.saveShop({ logo: "" });
          toast("הלוגו הוסר", "", "🗑️"); render(); break;
        case "cover-pick": { const inp = $("[data-coverfile]"); if (inp) inp.click(); break; }
        case "cover-remove":
          await Store.saveShop({ cover: "" });
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
        case "wiz-skip": wizCaptureStep4(); wizGo(5); break;
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
          const free = gridSlots(date).some((s) => s.start === start && !s.booking && !s.blocked && !s.past);
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

        // מנוי
        case "show-upgrade": handleUpgrade(); break;
        case "adm-extend": admExtend(t.dataset.sid, Number(t.dataset.m)); break;
        case "del-contact":
          await Store.removeContact(t.dataset.id);
          toast("הלקוח הוסר מהרשימה", "", "🗑️"); render(); break;

        // סימון "לא הגיע"
        // סימון הגעה ע״י הספר — מכניס את התור לדוח ההכנסות ולביקורי הלקוח
        case "owner-confirm":
          await Store.setBookingStatus(t.dataset.id, "confirmed", "owner");
          toast("סומן: הלקוח הגיע ✓", "good", "✓"); render(); break;
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
      if (a.dataset.coverfile !== undefined && a.type === "file") {
        if (a.files && a.files[0]) handleCoverUpload(a.files[0]);
        a.value = "";
        return;
      }
      if (a.dataset.block !== undefined && a.type === "checkbox") {
        // מתג פנוי/לא-פנוי ליד שעה (checked = פנוי)
        const [dk, time] = a.dataset.block.split("|");
        await Store.setBlock(dk, time, !a.checked);
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
    const login = async () => {
      const email = ($("#au-email") && $("#au-email").value.trim()) || "";
      const pass = ($("#au-pass") && $("#au-pass").value) || "";
      if (!email || !pass) { err("נא למלא אימייל וסיסמה"); return; }
      err("רגע…", true);
      try {
        await UG.Auth.signIn(email, pass);
        if (UG.Auth.currentUid() === ownerUid) { closeModal(); go("owner"); }
        else { err("החשבון הזה אינו הבעלים של המספרה"); await UG.Auth.signOut(); }
      } catch (e) { err(UG.Auth.humanError(e)); }
    };
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
      const configCodes = [UG_CONFIG.ownerPasscode].concat(UG_CONFIG.ownerPasscodesExtra || []).map(String);
      const ok = (shop.ownerPass && v === String(shop.ownerPass)) ||
        (SHOP === "main" && configCodes.includes(v));   // סיסמאות ה-config עובדות רק למספרה הראשית
      if (ok) { closeModal(); go("owner"); }
      else { toast("סיסמה שגויה", "", "🔒"); }
    };
    $("[data-act2='check-code']").addEventListener("click", check);
    $("#own-code").addEventListener("keydown", (e) => { if (e.key === "Enter") check(); });
    setTimeout(() => $("#own-code") && $("#own-code").focus(), 100);
  }

  async function saveSvc(id) {
    const name = $("#sv-name").value.trim();
    const price = Number($("#sv-price").value);
    const durationMin = Number($("#sv-dur").value);
    const icon = $("#modal").__icon ? $("#modal").__icon() : "✂️";
    if (!name) { toast("נא להזין שם שירות", "", "✋"); return; }
    if (!(price >= 0) || !(durationMin >= 5)) { toast("בדקו מחיר ומשך", "", "✋"); return; }
    await Store.upsertService({ id: id || undefined, name, price, durationMin, icon });
    closeModal(); toast("השירות נשמר ✓", "good", "✂️"); render();
  }

  async function saveSettings() {
    await Store.saveShop({
      name: $("#set-name").value.trim() || "המספרה",
      tagline: $("#set-tag").value.trim(),
      about: ($("#set-about") && $("#set-about").value.trim()) || "",
      address: $("#set-addr").value.trim(),
      phone: $("#set-phone").value.trim(),
      slotStep: Number($("#set-step").value),
      reminderMinutes: Number($("#set-remind").value),
    });
    toast("ההגדרות נשמרו ✓", "good", "⚙️"); render();
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
  function isStandalone() {
    return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  }
  function isIOS() { return /iphone|ipad|ipod/i.test(navigator.userAgent); }
  function installSuppressed() { return Date.now() < Number(localStorage.getItem("ug_install_dismiss") || 0); }
  function suppressInstall(days) { localStorage.setItem("ug_install_dismiss", String(Date.now() + days * 86400000)); }
  function hideInstallBar() { const b = document.getElementById("installBar"); if (b) b.classList.remove("show"); }

  function showInstallBar(mode) {
    if (isStandalone() || installSuppressed()) return;
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
    if (isStandalone() || installSuppressed() || !cookieAccepted()) return;  // לא מעל באנר העוגיות
    if (deferredPrompt) showInstallBar("android");
    else if (isIOS()) showInstallBar("ios");
    else if (/android/i.test(navigator.userAgent)) showInstallBar("generic");
  }
  function initInstall() {
    if (isStandalone()) return;
    window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferredPrompt = e; maybeShowInstall(); });
    window.addEventListener("appinstalled", () => {
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
          toast(`תור חדש: ${b.userName} · ${b.serviceName} ${u.relativeDay(b.date)} ${b.start}${warn}`, "sky", "🎉");
          Notify.show("📅 תור חדש נקבע", `${b.userName} — ${b.serviceName}\n${u.longDate(b.date)} בשעה ${b.start}${b.priorNoShow ? "\n⚠️ הלקוח לא הגיע בפעם הקודמת" : ""}`, { tag: "newbook-" + b.id });
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
          toast(`${b.userName || "לקוח"} ביטל תור · ${u.relativeDay(b.date)} ${b.start}`, "", "❌");
          Notify.show("❌ לקוח ביטל תור", `${b.userName || "לקוח"} — ${b.serviceName}\n${u.longDate(b.date)} בשעה ${b.start}`, { tag: "ccancel-" + b.id });
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
    setupBackGuard();   // מלכודת "אחורה" — להפעיל מיד, לפני טעינת הענן
    Notify.registerSW();
    wire();
    initInstall();
    initCookies();

    if (SHOP === "__new__") { view.onboarding = true; render(); wizFocus(); return; }  // שאלון פתיחת מספרה

    await Store.init(SHOP);
    if (Store.notFound) { view.notFound = true; render(); return; }        // מספרה לא קיימת

    // זכירת המספרה — כדי שפתיחת האפליקציה המותקנת (ללא כתובת) תחזיר לכאן
    try { if (SHOP !== "__new__") localStorage.setItem("ug_last_shop", SHOP); } catch (e) {}

    checkPaymentReturn();   // חזרה מעמוד התשלום של ספק הסליקה
    setTimeout(() => promptNotif(), 1200);   // הזמנה לאישור התראות — בכל כניסה עד שיאשר
    Store.subscribe(onStoreChange);
    Store.subscribeGallery(() => {
      // רענון כשמסתכלים על גלריה ולא באמצע הקלדה
      const onGalleryView = (view.route === "client" && view.clientTab === "gallery") ||
        (view.route === "owner" && view.ownerTab === "settings");
      if (onGalleryView && !isEditingRoot()) render();
    });
    const bootShop = (Store.get() && Store.get().shop) || {};
    const secured = !!bootShop.ownerUid;   // מספרה מאובטחת בחשבון אישי (Firebase Auth)
    // כלל יחיד וברור: נכנסים לניהול רק אם יש אישור מקומי כבעלים (התחברות עם הסיסמה/קוד).
    // כך התחברות עם כתובת+סיסמה תמיד פותחת את עמוד הספר — גם למספרה מאובטחת.
    if (view.route === "owner" && localStorage.getItem(AUTHKEY) !== "1") {
      view.route = "client";
    }
    render();
    // בדיקת זמינות התחברות מאובטחת (Firebase Auth) — לרענון מסך ההגדרות + כניסה אוטומטית לבעלים
    if (UG.Auth) UG.Auth.available().then((a) => {
      authAvail = a;
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
      if (view.route === "owner" && view.ownerTab === "settings" && !isEditingRoot()) render();
    });
    // תזמון תזכורות ורישום פוש בעת עלייה
    ensureFcm();
    if (view.route === "client" && Notify.permission() === "granted") {
      Notify.scheduleReminders(Store.get().bookings, identity.userId, Store.get().shop);
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
