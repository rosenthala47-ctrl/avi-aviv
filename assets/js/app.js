/* =========================================================================
   App — ניהול מסכים, תצוגה וחיווט אירועים
   =========================================================================*/
(function () {
  const u = UG.util;
  const Store = UG.Store;
  const Notify = UG.Notify;
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = u.escapeHtml;

  /* ---------- זיהוי המספרה מהקישור (רב-משתמשי) ---------- */
  function resolveShopId() {
    let h = (location.hash || "").replace(/^#/, "").trim().toLowerCase();
    if (h === "new" || h === "signup") return "__new__";   // מסך הרשמה/פתיחה
    h = h.replace(/[^a-z0-9-]/g, "");
    if (h) return h;                                        // מספרה לפי כתובת אישית (כולל #main = אורי)
    // בלי כתובת אישית: בדומיין הישן של אורי מציגים את המספרה שלו (תאימות לקישורים קיימים).
    if ((location.hostname || "").toLowerCase().indexOf("ori-grushko") !== -1) return "main";
    // בדומיין המוצר: פותחים ישר את המספרה האחרונה שנכנסו אליה במכשיר הזה
    // (כך הספר לא צריך להקליד את הכתובת שלו בכל פעם). אם אין — מסך הפתיחה של BarberTor.
    try { const last = (localStorage.getItem("ug_last_shop") || "").trim(); if (/^[a-z0-9-]+$/.test(last)) return last; } catch (e) {}
    return "__new__";
  }
  const SHOP = resolveShopId();
  const AUTHKEY = "ug_owner_auth__" + SHOP;
  const ROUTEKEY = "ug_route__" + SHOP;

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
    clientTab: "book",   // book | gallery | mine
    ownerTab: (function () { try { return localStorage.getItem("ug_otab__" + SHOP) || "cal"; } catch (e) { return "cal"; } })(),  // cal | hours | services | bookings | clients | report | publish | settings
    selService: null,
    selDate: null,       // יום נבחר בצד הלקוח
    selSlot: null,
    rescheduleId: null,  // מזהה תור בעת שינוי מועד (החלפת מועד לתור קיים)
    oDate: null,         // יום נבחר בצד הבעלים (תצוגת יומן)
    statMonth: null,     // חודש נבחר בדוח ("YYYY-MM")
    onboarding: false,   // מסך פתיחת מספרה
    notFound: false,     // מספרה לא קיימת
  };
  let ownerSeen = null;     // Set של מזהי תורים שהבעלים כבר ראה (זיהוי תור חדש)
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
     ניווט "אחורה" חכם — מחזיר למסך הקודם, ובדף הבית שואל אם לצאת
     =======================================================================*/
  const viewStack = [];
  function snapView() {
    return { route: view.route, clientTab: view.clientTab, ownerTab: view.ownerTab,
      selDate: view.selDate, oDate: view.oDate, statMonth: view.statMonth };
  }
  function restoreSnap(s) {
    view.route = s.route; view.clientTab = s.clientTab; view.ownerTab = s.ownerTab;
    if (s.selDate) view.selDate = s.selDate;
    if (s.oDate) view.oDate = s.oDate;
    if (s.statMonth) view.statMonth = s.statMonth;
    render();
  }
  function recordNav() { viewStack.push(snapView()); if (viewStack.length > 60) viewStack.shift(); }
  function modalOpen() { const m = $("#modalBack"); return m && m.classList.contains("open"); }

  function onPopState() {
    try { history.pushState(null, ""); } catch (e) {}   // מלכודת מחדש כדי לא לצאת
    if (modalOpen()) { closeModal(); return; }
    if (viewStack.length) { restoreSnap(viewStack.pop()); return; }
    showExitConfirm();
  }
  function setupBackGuard() {
    try { history.pushState(null, ""); } catch (e) {}
    window.addEventListener("popstate", onPopState);
  }
  function showExitConfirm() {
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
    try { history.go(-2); } catch (e) {}
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
    if (view.route !== route) recordNav();
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
        <div class="logo-dot" title="">${esc((st.shop.name || "מ")[0])}</div>
        <div class="titles">
          <h1>${esc(st.shop.name)}</h1>
          <p>${esc(sub)}</p>
        </div>
      </div>
      <div class="spacer"></div>
      ${(view.route === "client" && localStorage.getItem(AUTHKEY) === "1")
        ? `<button class="icon-btn" data-act="owner-login" title="כניסת מנהל" aria-label="כניסת מנהל">🔑</button>` : ""}
      <button class="icon-btn" data-act="toggle-theme" title="מצב תצוגה">${themeIco}</button>
      ${opts.switch ? `<button class="icon-btn" data-act="logout" title="חזרה לתצוגת לקוח">⇋</button>` : ""}
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
    return `
    <div class="banner sky">
      <span class="bn-ico">🔔</span>
      <div class="bn-body">
        <div class="bn-title">קבלת תזכורת לפני התור</div>
        <div class="bn-sub">אפשרו התראות ותקבלו תזכורת שעה לפני התספורת</div>
      </div>
      <button class="btn btn-primary btn-sm" data-act="enable-notif" style="width:auto">אפשר</button>
    </div>`;
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
    const days = nextDays(14);
    if (!view.selDate || !days.includes(view.selDate)) {
      view.selDate = days.find((k) => st.schedule[u.parseKey(k).getDay()].active) || days[0];
    }
    const dayChips = days.map((k) => {
      const d = u.parseKey(k);
      const off = !st.schedule[d.getDay()].active;
      return `
      <button class="day-chip ${view.selDate === k ? "selected" : ""} ${off ? "off" : ""}"
              data-day="${k}" ${off ? "disabled" : ""}>
        <div class="dc-dow">${off ? "סגור" : u.DOW_SHORT[d.getDay()]}</div>
        <div class="dc-num">${d.getDate()}</div>
        <div class="dc-mon">${u.MON[d.getMonth()]}</div>
      </button>`;
    }).join("");

    // שעות — רשת אחידה; מסתירים משבצות שעברו/חסומות, מסמנים תפוסות
    let slotsHtml;
    const allSlots = gridSlots(view.selDate).filter((s) => !s.past && !s.blocked);
    const hasFree = allSlots.some((s) => !s.booking);
    if (!st.schedule[u.parseKey(view.selDate).getDay()].active) {
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

      ${installCard()}
      ${mapsCard(st)}
      ${shareCard()}
      <p class="hint" style="text-align:center;margin-top:22px">
        מנהלים מספרה? <a href="#new" data-act="open-signup" style="color:var(--sky)">פתחו מערכת תורים משלכם ›</a>
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
      .filter((x) => x.userId === identity.userId && x.status !== "cancelled")
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
      const actions = isPast ? "" : `
        <div class="btn-row btn-row-wrap" style="margin-top:12px">
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
    }).then((sent) => { if (sent) toast("אישור נשלח למייל 📧", "sky", "📧"); });
  }

  async function doBook() {
    const contact = readContact();
    if (!contact) return;
    const bookedDate = view.selDate, bookedStart = view.selSlot;
    const reschedId = view.rescheduleId;
    const btn = $("[data-act='do-book']"); if (btn) { btn.disabled = true; btn.textContent = reschedId ? "מעדכן…" : "קובע תור…"; }
    const res = await Store.createBooking({
      serviceId: view.selService, date: bookedDate, start: bookedStart,
      userId: identity.userId, userName: contact.name, phone: contact.phone, email: contact.email,
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
    if (reschedId) { await Store.setBookingStatus(reschedId, "cancelled"); view.rescheduleId = null; }
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
    let body;
    if (view.ownerTab === "cal") body = ownerCal(st);
    else if (view.ownerTab === "hours") body = ownerHours(st);
    else if (view.ownerTab === "services") body = ownerServices(st);
    else if (view.ownerTab === "bookings") body = ownerBookings(st);
    else if (view.ownerTab === "clients") body = ownerClients(st);
    else if (view.ownerTab === "report") body = ownerReport(st);
    else if (view.ownerTab === "publish") body = ownerPublish(st);
    else body = ownerSettings(st);

    const upcomingCount = st.bookings.filter((b) =>
      b.status !== "cancelled" && u.dateTime(b.date, b.start).getTime() > now).length;

    return `
    <div class="screen active">
      ${topbar("ניהול העסק", { switch: true })}
      <div class="content" id="oscroll">${body}</div>
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
    return `
      <div class="section-title">ימי הפעילות ושעות העבודה</div>
      <div class="card">${rows.join("")}</div>
      <p class="hint">כל שינוי נשמר מיד ומתעדכן אצל הלקוחות בזמן אמת. שעות העבודה קובעות אילו שעות מוצגות בלשונית ״יומן״.</p>
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
      const stg = b.status === "confirmed"
        ? `<span class="status-tag status-confirmed">✓ אישר הגעה</span>`
        : `<span class="status-tag status-booked">ממתין</span>`;
      return `
      <div class="booking" style="${isPast ? "opacity:.55" : ""}">
        <div class="bk-time">
          <div class="bt-h">${esc(b.start)}</div>
          <div class="bt-d">${esc(u.relativeDay(b.date))}</div>
        </div>
        <div class="bk-body">
          <div class="bk-title">${esc(b.userName || "לקוח")}</div>
          <div class="bk-sub">${esc(b.serviceName)} · ${b.phone ? `<a href="tel:${esc(b.phone)}">${esc(b.phone)}</a>` : "ללא טלפון"}</div>
          <div class="bk-sub">${esc(u.longDate(b.date))}</div>
        </div>
        <div style="display:flex;flex-direction:column;gap:8px;align-items:flex-end">
          ${stg}
          ${!isPast ? `<button class="btn btn-sm btn-danger" data-act="owner-cancel" data-id="${b.id}">בטל</button>` : ""}
        </div>
      </div>`;
    };
    let html = addBtn;
    if (upcoming.length) html += `<div class="section-title">תורים קרובים (${upcoming.length})</div>` + upcoming.map((x) => row(x, false)).join("");
    if (past.length) html += `<div class="section-title">היסטוריה</div>` + past.map((x) => row(x, true)).join("");
    return html;
  }

  /* מודאל הוספת תור ידנית ע״י הבעלים — בכל שעה מ-00:00 עד 23:45 (גם מחוץ לשעות הפעילות) */
  function ownerAddBooking() {
    const st = Store.get();
    if (!st.services.length) { toast("צריך להגדיר שירות אחד לפחות", "", "✂️"); return; }
    const today = u.dateKey(new Date());
    const svcOptions = st.services.map((s) => `<option value="${s.id}">${esc(s.name)} · ${u.fmtDuration(s.durationMin)}</option>`).join("");
    const hourOptions = Array.from({ length: 24 }, (_, h) => { const v = String(h).padStart(2, "0"); return `<option value="${v}">${v}</option>`; }).join("");
    const minOptions = ["00", "15", "30", "45"].map((m) => `<option value="${m}">${m}</option>`).join("");
    openModal(`
      <div class="m-title">הוספת תור ידני</div>
      <div class="m-sub">אפשר לקבוע בכל שעה — גם מחוץ לשעות הפעילות</div>
      <div class="field"><label>שירות</label>
        <select class="input" id="ab-svc">${svcOptions}</select></div>
      <div class="field"><label>תאריך</label>
        <input class="input" id="ab-date" type="date" value="${today}"></div>
      <div class="field"><label>שעה</label>
        <div style="display:flex;gap:8px;align-items:center;direction:ltr;justify-content:flex-start">
          <select class="input" id="ab-hour" style="flex:1">${hourOptions}</select>
          <span style="font-weight:800">:</span>
          <select class="input" id="ab-min" style="flex:1">${minOptions}</select>
        </div></div>
      <div class="field"><label>שם הלקוח</label>
        <input class="input" id="ab-name" placeholder="שם הלקוח"></div>
      <div class="field"><label>טלפון (לא חובה)</label>
        <input class="input" id="ab-phone" type="tel" inputmode="tel" placeholder="050-0000000"></div>
      ${(UG.Email && UG.Email.configured()) ? `
      <div class="field"><label>אימייל לאישור (לא חובה)</label>
        <input class="input" id="ab-email" type="email" inputmode="email" placeholder="name@email.com"></div>` : ""}
      <button class="btn btn-primary" data-act="save-add-booking">קביעת התור</button>
      <button class="btn btn-ghost" data-act="close-modal" style="margin-top:8px">ביטול</button>
    `);
    const hs = $("#ab-hour"); if (hs) hs.value = String(new Date().getHours()).padStart(2, "0");
    setTimeout(() => $("#ab-name") && $("#ab-name").focus(), 100);
  }

  async function saveAddBooking() {
    const svcId = ($("#ab-svc") && $("#ab-svc").value) || "";
    const date = ($("#ab-date") && $("#ab-date").value) || "";
    const hh = ($("#ab-hour") && $("#ab-hour").value) || "00";
    const mm = ($("#ab-min") && $("#ab-min").value) || "00";
    const name = ($("#ab-name") && $("#ab-name").value.trim()) || "";
    const phoneRaw = ($("#ab-phone") && $("#ab-phone").value.trim()) || "";
    if (!svcId) { toast("בחרו שירות", "", "✋"); return; }
    if (!date) { toast("בחרו תאריך", "", "✋"); return; }
    if (!name) { toast("הזינו שם לקוח", "", "✋"); return; }
    const start = hh + ":" + mm;
    const phone = phoneRaw ? u.fmtPhone(phoneRaw) : "";
    const email = ($("#ab-email") && $("#ab-email").value.trim()) || "";
    if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { toast("כתובת אימייל לא תקינה", "", "📧"); return; }
    const res = await Store.createBooking({
      serviceId: svcId, date, start,
      userId: "owner:" + (phone ? u.normalizePhone(phone) : u.uid()),
      userName: name, phone, email,
    });
    if (!res.ok) { toast(res.reason || "לא ניתן לקבוע את התור", "", "⚠️"); return; }
    closeModal(); toast("התור נוסף ✓", "good", "➕");
    sendBookingEmail(res.booking);   // מייל אישור ללקוח (אם מוגדר והוזן אימייל)
    render();
  }

  /* ---------- רשימת לקוחות (CRM) ---------- */
  function clientKey(b) { return (b.phone && u.normalizePhone(b.phone)) || b.userId || b.userName || "לקוח"; }

  function ownerClients(st) {
    const map = new Map();
    st.bookings.filter((b) => b.status !== "cancelled").forEach((b) => {
      const key = clientKey(b);
      let c = map.get(key);
      if (!c) { c = { key, name: b.userName || "לקוח", phone: b.phone || "", visits: 0, spent: 0, lastTs: 0, lastDate: b.date }; map.set(key, c); }
      if (b.userName) c.name = b.userName;
      if (b.phone) c.phone = b.phone;
      if (b.status === "confirmed") { c.visits++; c.spent += Number(b.price || 0); }
      const ts = u.dateTime(b.date, b.start).getTime();
      if (ts > c.lastTs) { c.lastTs = ts; c.lastDate = b.date; }
    });
    const clients = [...map.values()].sort((a, z) => z.lastTs - a.lastTs);
    if (!clients.length) return emptyState("👥", "אין עדיין לקוחות", "לקוחות יופיעו כאן אחרי שיזמינו תור");
    const totalSpent = clients.reduce((s, c) => s + c.spent, 0);
    const totalVisits = clients.reduce((s, c) => s + c.visits, 0);
    return `
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
              <div class="bk-title">${esc(c.name)}</div>
              <div class="bk-sub">${c.phone ? `<a href="tel:${esc(c.phone)}">${esc(c.phone)}</a>` : "ללא טלפון"}</div>
              <div class="bk-sub">${c.visits} ביקורים · <b>${u.fmtPrice(c.spent)}</b> · אחרון ${esc(u.relativeDay(c.lastDate))}</div>
            </div>
            <button class="btn btn-sm" data-act="client-detail" data-key="${esc(c.key)}">פרטים</button>
          </div>
        </div>`).join("")}
    `;
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
        <div class="pub-ico">📣</div>
        <h2>${owner ? esc(owner) + ", המספרה שלך מוכנה!" : "המספרה שלך מוכנה!"}</h2>
        <p>שלח/י את הקישור הזה ללקוחות — הם יזמינו תור לבד, ישירות מהטלפון.</p>
      </div>

      <div class="section-title">🔗 הקישור האישי שלך</div>
      <div class="card">
        <div class="pub-link">${esc(link)}</div>
        <div class="btn-row btn-row-wrap" style="margin-top:13px">
          <button class="btn btn-primary btn-sm" data-act="copy-link">📋 העתקה</button>
          <button class="btn btn-sm" data-act="share-app">🔗 שיתוף</button>
          <button class="btn btn-sm" data-act="share-wa">💬 וואטסאפ</button>
        </div>
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
    return `
      <div class="section-title">🔗 הקישור שלך ללקוחות</div>
      <div class="card">
        <div class="hint" style="margin-bottom:10px">שלחו את הקישור הזה ללקוחות — הוא פותח את המספרה שלכם:</div>
        <div style="word-break:break-all;font-weight:700;font-size:13.5px;color:var(--sky-2)">${esc(clientLink())}</div>
        <div class="btn-row" style="margin-top:12px">
          <button class="btn btn-sm" data-act="copy-link">📋 העתקה</button>
          <button class="btn btn-sm" data-act="share-app">🔗 שיתוף</button>
        </div>
      </div>
      ${ownerSecuritySection(st)}
      ${ownerGallerySection()}
      <div class="section-title">פרטי העסק</div>
      <div class="card">
        <div class="field"><label>שם העסק</label>
          <input class="input" id="set-name" value="${esc(st.shop.name)}"></div>
        <div class="field"><label>תיאור קצר</label>
          <input class="input" id="set-tag" value="${esc(st.shop.tagline || "")}"></div>
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
      </div>
    `;
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
    if (view.onboarding) { document.title = "BarberTor — תורים לספרים"; $("#root").innerHTML = renderOnboarding(); return; }
    if (view.notFound) { document.title = "BarberTor"; $("#root").innerHTML = renderNotFound(); return; }
    if (!Store.get()) return;
    // כותרת לשונית דינמית — שם המספרה של הספר, עם מיתוג BarberTor
    const shopName = (Store.get().shop && Store.get().shop.name) || "BarberTor";
    document.title = shopName + " · BarberTor";
    $("#root").innerHTML = view.route === "owner" ? renderOwner() : renderClient();
  }

  /* =======================================================================
     פתיחת מספרה חדשה (רישום ספר) + "מספרה לא נמצאה"
     =======================================================================*/
  /* =======================================================================
     שאלון פתיחת מספרה — חוויית Onboarding (עמוד אחרי עמוד)
     =======================================================================*/
  const wiz = { step: 0, busy: false, data: { owner: "", name: "", handle: "", phone: "", address: "", pass: "" } };
  const WIZ_QUESTIONS = 5;   // שלבים 1..5 הם שאלות

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
           <input class="input wiz-input" id="wz-address" placeholder="כתובת · רחוב, עיר" value="${esc(d.address)}" style="margin-top:10px">`);
      case 5:
        return wizQ("🔒", "סיסמת ניהול", "רק איתה נכנסים לנהל את המספרה. שמור/י אותה במקום בטוח!",
          `<div class="pw-field">
             <input class="input wiz-input" id="wz-pass" type="password" placeholder="בחר/י סיסמה" value="${esc(d.pass)}">
             <button type="button" class="pw-eye" data-act="toggle-pw" aria-label="הצג סיסמה">👁️</button>
           </div>`);
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

  function wizFocus() {
    const el = $("#wizBody") && $("#wizBody").querySelector("input");
    if (el) setTimeout(() => el.focus(), 120);
    // תצוגה מקדימה חיה של הקישור + Enter להמשך
    const h = $("#wz-handle");
    if (h) h.addEventListener("input", () => {
      const v = h.value.trim().toLowerCase().replace(/[^a-z0-9-]/g, "");
      const p = $("#wz-linkPrev");
      if (p) p.innerHTML = esc(shareBase() + "#") + "<b>" + esc(v || "הכתובת-שלך") + "</b>";
    });
    const body = $("#wizBody");
    if (body) body.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); wizNext(); }
    });
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
    if (wiz.step === 4) {
      d.phone = ($("#wz-phone") && $("#wz-phone").value.trim()) || "";
      d.address = ($("#wz-address") && $("#wz-address").value.trim()) || "";
      wizGo(5); return;
    }
    if (wiz.step === 5) {
      d.pass = ($("#wz-pass") && $("#wz-pass").value.trim()) || "";
      if (d.pass.length < 4) { toast("סיסמה קצרה מדי (לפחות 4 תווים)", "", "✋"); haptic(40); return; }
      wizBuild(); return;
    }
  }

  function wizBack() {
    if (wiz.step <= 0) return;
    // שמירת מה שהוקלד לפני חזרה
    const map = { 1: ["owner", "#wz-owner"], 2: ["name", "#wz-name"], 3: ["handle", "#wz-handle"], 5: ["pass", "#wz-pass"] };
    const m = map[wiz.step];
    if (m && $(m[1])) wiz.data[m[0]] = $(m[1]).value.trim();
    if (wiz.step === 4) {
      if ($("#wz-phone")) wiz.data.phone = $("#wz-phone").value.trim();
      if ($("#wz-address")) wiz.data.address = $("#wz-address").value.trim();
    }
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
    try { localStorage.setItem("ug_last_shop", d.handle); } catch (e) {}
    setTimeout(() => { location.hash = d.handle; location.reload(); }, 800);
  }

  function wizExisting() {
    openModal(`
      <div class="m-title">כניסה למערכת שלי</div>
      <div class="m-sub">הזינו את הכתובת האישית שלכם</div>
      <div class="field"><input class="input" id="ob-existing" placeholder="dani" autocapitalize="off" autocomplete="off" spellcheck="false" style="text-align:center"></div>
      <button class="btn btn-primary" data-act="goto-shop">כניסה ›</button>
      <button class="btn btn-ghost btn-sm" data-act="close-modal" style="margin-top:8px;width:100%">ביטול</button>
    `);
    setTimeout(() => $("#ob-existing") && $("#ob-existing").focus(), 100);
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

      const t = e.target.closest("[data-act],[data-svc],[data-day],[data-oday],[data-slot],[data-wait],[data-photo],[data-delphoto],[data-tab],[data-otab],[data-active]");
      if (!t) return;

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
      if (t.dataset.tab) { if (view.clientTab !== t.dataset.tab) recordNav(); view.clientTab = t.dataset.tab; render(); return; }
      if (t.dataset.otab) {
        if (view.ownerTab !== t.dataset.otab) recordNav();
        view.ownerTab = t.dataset.otab;
        try { localStorage.setItem("ug_otab__" + SHOP, view.ownerTab); } catch (e2) {}
        render(); return;
      }

      const act = t.dataset.act;
      if (!act) return;

      switch (act) {
        case "logout": go("client"); break;
        case "close-modal": closeModal(); break;

        case "open-confirm": openConfirm(); break;
        case "do-book": doBook(); break;

        case "confirm-arrival":
          await Store.setBookingStatus(t.dataset.id, "confirmed");
          toast("הגעתך אושרה ✓", "good", "📍"); render(); break;

        case "owner-cancel":
          await Store.setBookingStatus(t.dataset.id, "cancelled");
          toast("התור בוטל", "", "🗑️"); render(); break;

        // ביטול תור ע״י הלקוח — עם אישור למניעת ביטול בטעות
        case "cancel-booking": confirmCancelBooking(t.dataset.id); break;
        case "do-cancel-booking":
          await Store.setBookingStatus(t.dataset.id, "cancelled");
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

        case "enable-notif": handleEnableNotif(); break;
        case "owner-login": promptOwner(); break;   // כניסת מנהל ייעודית (במקום 3 לחיצות על הלוגו)
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
        case "wiz-skip": wizGo(5); break;
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
    const check = () => {
      const v = (($("#own-code") && $("#own-code").value) || "").trim();
      const shop = (Store.get() && Store.get().shop) || {};
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
      address: $("#set-addr").value.trim(),
      phone: $("#set-phone").value.trim(),
      slotStep: Number($("#set-step").value),
      reminderMinutes: Number($("#set-remind").value),
    });
    toast("ההגדרות נשמרו ✓", "good", "⚙️"); render();
  }

  async function handleEnableNotif() {
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
          toast(`תור חדש: ${b.userName} · ${b.serviceName} ${u.relativeDay(b.date)} ${b.start}`, "sky", "🎉");
          Notify.show("📅 תור חדש נקבע", `${b.userName} — ${b.serviceName}\n${u.longDate(b.date)} בשעה ${b.start}`, { tag: "newbook-" + b.id });
        });
      }
    } else if (ownerSeen) {
      st.bookings.forEach((b) => ownerSeen.add(b.id));
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

    // זכירת המספרה הזו כברירת מחדל לפתיחה הבאה (הספר לא יצטרך להקליד שוב)
    try { localStorage.setItem("ug_last_shop", SHOP); } catch (e) {}

    Store.subscribe(onStoreChange);
    Store.subscribeGallery(() => {
      // רענון כשמסתכלים על גלריה ולא באמצע הקלדה
      const onGalleryView = (view.route === "client" && view.clientTab === "gallery") ||
        (view.route === "owner" && view.ownerTab === "settings");
      if (onGalleryView && !isEditingRoot()) render();
    });
    const bootShop = (Store.get() && Store.get().shop) || {};
    const secured = !!bootShop.ownerUid;   // מספרה מאובטחת בחשבון אישי (Firebase Auth)
    if (secured) {
      // כניסה לניהול רק לאחר אימות מול החשבון — לא סומכים על סימון מקומי
      view.route = "client";
    } else if (view.route === "owner" && localStorage.getItem(AUTHKEY) !== "1") {
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
