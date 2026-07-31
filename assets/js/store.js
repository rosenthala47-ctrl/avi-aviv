/* =========================================================================
   Store — שכבת הנתונים המשותפת (מקור אמת אחד)
   - כל שינוי אצל הבעלים או הזמנה של לקוח משתקפים בזמן אמת אצל כולם.
   - מצב מקומי:  localStorage + BroadcastChannel  (סנכרון בין כרטיסיות/משתמשים
     על אותו מכשיר, ללא הגדרות).
   - מצב מרוחק:  Firebase Firestore (סנכרון בין כל המכשירים) — נטען אוטומטית
     אם מולאו הפרטים ב-config.js.
   =========================================================================*/
window.UG = window.UG || {};
UG.Store = (function () {
  const u = UG.util;
  const KEY = (id) => "ug_barber_state_v1__" + id;   // מפתח מקומי לכל מספרה
  const GKEY = (id) => "ug_gallery_v1__" + id;

  let shopId = "main";
  let state = null;
  let backend = null;
  let notFound = false;
  let subState = null;   // מידע המנוי של המספרה הפעילה (subs/<id>) — לקריאה בלבד
  const subs = new Set();
  let galleryCache = [];
  const gallerySubs = new Set();

  /* ---------- מצב ברירת מחדל ---------- */
  function defaultState() {
    const d = UG_CONFIG.defaults;
    const schedule = {};
    for (let i = 0; i < 7; i++) {
      let active = true, open = "09:00", close = "19:00";
      if (i === 5) { close = "14:00"; }          // שישי — עד הצהריים
      if (i === 6) { active = false; }            // שבת — סגור
      schedule[i] = { active, open, close };
    }
    return {
      version: 1,
      shop: {
        name: d.shopName, tagline: d.tagline, phone: d.phone, address: d.address,
        slotStep: d.slotStep, reminderMinutes: d.reminderMinutes,
        staff: [],           // שמות הספרים (אם יש כמה) — לבחירת ספר מועדף ע״י הלקוח
      },
      schedule,
      services: [
        { id: u.uid(), name: "תספורת גבר", price: 60, durationMin: 30, icon: "✂️", active: true },
        { id: u.uid(), name: "זקן ועיצוב", price: 40, durationMin: 20, icon: "🧔", active: true },
      ],
      bookings: [],
      contacts: [],          // ספר הלקוחות שהספר ייבא (שם + טלפון)
      closedDates: [],       // ימי סגירה/חופשה מלאים: "YYYY-MM-DD"
      blocks: [],            // שעות שהבעלים סימן כלא-פנויות: "YYYY-MM-DD|HH:MM"
      waitlist: [],          // רשימת המתנה לשעות תפוסות
      alerts: [],            // "התפנה תור" — התראות ממתינות למשתמשים
      reviews: [],           // דירוגים וביקורות של לקוחות
      updatedAt: Date.now(),
    };
  }

  function clone(o) { return JSON.parse(JSON.stringify(o)); }

  /* מיזוג בטוח מול ברירת המחדל (למקרה של גרסאות ישנות בזיכרון) */
  function normalize(s) {
    const base = defaultState();
    if (!s || typeof s !== "object") return base;
    s.shop = Object.assign({}, base.shop, s.shop);
    if (!s.schedule) s.schedule = base.schedule;
    for (let i = 0; i < 7; i++) s.schedule[i] = Object.assign({}, base.schedule[i], s.schedule[i]);
    if (!Array.isArray(s.services)) s.services = base.services;
    if (!Array.isArray(s.bookings)) s.bookings = [];
    if (!Array.isArray(s.contacts)) s.contacts = [];
    if (!Array.isArray(s.closedDates)) s.closedDates = [];
    if (!Array.isArray(s.blocks)) s.blocks = [];
    if (!Array.isArray(s.waitlist)) s.waitlist = [];
    if (!Array.isArray(s.alerts)) s.alerts = [];
    if (!Array.isArray(s.reviews)) s.reviews = [];
    if (!Array.isArray(s.shop.staff)) s.shop.staff = [];
    if (!s.shop.address) s.shop.address = base.shop.address;
    // ניקוי רשומות שפג תוקפן (שעת התור כבר עברה)
    const nowTs = Date.now();
    s.waitlist = s.waitlist.filter((w) => u.dateTime(w.date, w.start).getTime() > nowTs);
    s.alerts = s.alerts.filter((a) => u.dateTime(a.date, a.start).getTime() > nowTs);
    // מעבר למודל של מרווחי 45 דק׳ — נרמול ערכים ישנים
    if (![30, 45, 60].includes(Number(s.shop.slotStep))) s.shop.slotStep = 45;
    s.version = 1;
    return s;
  }

  /* =======================================================================
     Backend מקומי
     =======================================================================*/
  function LocalBackend(id) {
    const skey = KEY(id), gkey = GKEY(id);
    let bc = null;
    try { bc = new BroadcastChannel("ug_barber_" + id); } catch (e) {}
    const listeners = { state: [], gallery: [] };
    if (bc) bc.onmessage = (ev) => {
      const t = ev && ev.data && ev.data.t;
      if (t === "gallery") listeners.gallery.forEach((fn) => fn());
      else listeners.state.forEach((fn) => fn());
    };
    window.addEventListener("storage", (e) => {
      if (e.key === skey) listeners.state.forEach((fn) => fn());
      if (e.key === gkey) listeners.gallery.forEach((fn) => fn());
    });
    function readG() { try { return JSON.parse(localStorage.getItem(gkey) || "[]"); } catch (e) { return []; } }
    function writeG(list) {
      try { localStorage.setItem(gkey, JSON.stringify(list)); } catch (e) {}
      try { if (bc) bc.postMessage({ t: "gallery" }); } catch (e) {}
    }
    return {
      mode: "local",
      read() {
        try {
          const raw = localStorage.getItem(skey);
          return raw ? normalize(JSON.parse(raw)) : null;
        } catch (e) { return null; }
      },
      write(s) {
        s.updatedAt = Date.now();
        try { localStorage.setItem(skey, JSON.stringify(s)); } catch (e) {}
        try { if (bc) bc.postMessage({ t: "sync", at: s.updatedAt }); } catch (e) {}
        return Promise.resolve();
      },
      onRemote(cb) { listeners.state.push(cb); },
      // גלריה (לכל מספרה בנפרד)
      readGallery() { return readG(); },
      onGallery(cb) { listeners.gallery.push(cb); },
      addPhoto(p) { const l = readG(); l.unshift(Object.assign({ id: u.uid() }, p)); writeG(l); return Promise.resolve(); },
      removePhoto(pid) { writeG(readG().filter((x) => x.id !== pid)); return Promise.resolve(); },
      exists() { return Promise.resolve(localStorage.getItem(skey) != null); },
    };
  }

  /* =======================================================================
     Backend של Firebase — Firestore (ברירת מחדל) או Realtime Database.
     בוחרים אוטומטית: אם ב-config יש databaseURL → Realtime Database, אחרת Firestore.
     =======================================================================*/
  function FirestoreBackend(db, id) {
    const ref = db.collection("shops").doc(id);
    return {
      mode: "cloud",
      _db: db, _ref: ref,
      read() {
        return ref.get().then((snap) => (snap.exists ? normalize(snap.data()) : null));
      },
      write(s) {
        s.updatedAt = Date.now();
        return ref.set(s);
      },
      onRemote(cb) {
        ref.onSnapshot((snap) => {
          if (snap.exists && !snap.metadata.hasPendingWrites) cb(snap.data());
        });
      },
      // גלריה — קולקציה נפרדת מסוננת לפי מזהה המספרה,
      // + קריאת תמונות ישנות שנשמרו בעבר תחת shops (type:'photo') כדי שלא ייעלמו.
      onGallery(cb) {
        let neu = [], legacy = [];
        const push = () => cb(neu.concat(legacy));
        db.collection("gallery").where("shopId", "==", id).onSnapshot(
          (snap) => { neu = snap.docs.map((d) => Object.assign({ id: d.id }, d.data())); push(); },
          (err) => console.warn("[UG] gallery listen:", err && err.message)
        );
        if (id === "main") {
          db.collection("shops").where("type", "==", "photo").onSnapshot(
            (snap) => { legacy = snap.docs.map((d) => Object.assign({ id: d.id, __legacy: true }, d.data())); push(); },
            (err) => {}
          );
        }
      },
      addPhoto(p) { return db.collection("gallery").add(Object.assign({ shopId: id }, p)); },
      removePhoto(pid, legacy) {
        return legacy ? db.collection("shops").doc(pid).delete() : db.collection("gallery").doc(pid).delete();
      },
      // שמירת טוקן פוש (FCM) של מכשיר — לשליחת התראות גם כשהאפליקציה סגורה
      saveToken(uid, token) {
        return db.collection("pushTokens").doc(uid).set({
          tokens: firebase.firestore.FieldValue.arrayUnion(token),
          platform: (navigator.userAgent || "").slice(0, 120),
          updatedAt: Date.now(),
        }, { merge: true });
      },
      // הזמנה בטוחה מפני התנגשות (טרנזקציה אטומית)
      transactBooking(build) {
        return db.runTransaction((tx) =>
          tx.get(ref).then((snap) => {
            if (!snap.exists) return { ok: false, reason: "המספרה לא נמצאה" };
            const cur = normalize(snap.data());
            const res = build(cur);
            if (!res.ok) return res;
            cur.updatedAt = Date.now();
            tx.set(ref, cur);
            return res;
          })
        );
      },
      exists() { return ref.get().then((snap) => snap.exists); },
    };
  }

  /* Realtime Database — חינמי, ללא כרטיס אשראי (תוכנית Spark) */
  function RtdbBackend(db, id) {
    const ref = db.ref("shops/" + id);
    const galRef = db.ref("gallery/" + id);
    return {
      mode: "cloud",
      _db: db, _ref: ref,
      read() {
        return ref.once("value").then((snap) => (snap.exists() ? normalize(snap.val()) : null));
      },
      write(s) {
        s.updatedAt = Date.now();
        return ref.set(clone(s));   // clone → JSON נקי (RTDB לא מקבל undefined)
      },
      onRemote(cb) {
        ref.on("value", (snap) => { if (snap.exists()) cb(snap.val()); });
      },
      onGallery(cb) {
        galRef.on("value", (snap) => {
          const val = snap.val() || {};
          cb(Object.keys(val).map((k) => Object.assign({ id: k }, val[k])));
        });
      },
      addPhoto(p) { return galRef.push(clone(p)); },
      removePhoto(pid) { return galRef.child(pid).remove(); },
      // טוקני פוש — נשמרים כמערך; טרנזקציה כדי לא לדרוס טוקנים של מכשירים אחרים
      saveToken(uid, token) {
        const tRef = db.ref("pushTokens/" + uid);
        return tRef.child("tokens").transaction((arr) => {
          arr = Array.isArray(arr) ? arr : [];
          if (arr.indexOf(token) === -1) arr.push(token);
          return arr;
        }).then(() => tRef.update({
          platform: (navigator.userAgent || "").slice(0, 120), updatedAt: Date.now(),
        }));
      },
      // הזמנה בטוחה מפני התנגשות — קריאה טרייה מהשרת, בדיקת חפיפה, ואז כתיבה
      transactBooking(build) {
        return ref.once("value").then((snap) => {
          if (!snap.exists()) return { ok: false, reason: "המספרה לא נמצאה" };
          const cur = normalize(snap.val());
          const res = build(cur);
          if (!res.ok) return res;
          cur.updatedAt = Date.now();
          return ref.set(clone(cur)).then(() => res);
        });
      },
      exists() { return ref.once("value").then((snap) => snap.exists()); },
      // מנוי — נשמר בצומת נפרד (subs/<id>) שהספר קורא אך לא כותב, כדי שלא ידרוס
      readSub() { return db.ref("subs/" + id).once("value").then((s) => s.val() || null); },
      onSub(cb) { db.ref("subs/" + id).on("value", (s) => cb(s.val() || null)); },
    };
  }

  let _conn = null, _connected = false;   // _conn = { db, kind }
  function loadFirebaseDb() {
    return new Promise((resolve, reject) => {
      const cfg = UG_CONFIG.firebase;
      if (!cfg || !cfg.apiKey || !cfg.projectId) return reject("no-config");
      const useRtdb = !!cfg.databaseURL;
      const load = (src) => new Promise((res, rej) => {
        const sc = document.createElement("script");
        sc.src = src; sc.onload = res; sc.onerror = rej; document.head.appendChild(sc);
      });
      const V = "10.12.2";
      let chain = load(`https://www.gstatic.com/firebasejs/${V}/firebase-app-compat.js`)
        .then(() => load(`https://www.gstatic.com/firebasejs/${V}/firebase-${useRtdb ? "database" : "firestore"}-compat.js`));
      if (useRtdb) {
        // התחברות אנונימית — כדי שחוקי האבטחה יוכלו לדרוש משתמש מחובר
        chain = chain.then(() => load(`https://www.gstatic.com/firebasejs/${V}/firebase-auth-compat.js`));
      }
      chain.then(() => {
        firebase.initializeApp(cfg);
        if (useRtdb) { try { firebase.auth().signInAnonymously().catch(() => {}); } catch (e) {} }
        resolve({ db: useRtdb ? firebase.database() : firebase.firestore(), kind: useRtdb ? "rtdb" : "firestore" });
      }).catch(reject);
    });
  }
  async function connect() {
    if (_connected) return;
    _connected = true;
    try { _conn = await loadFirebaseDb(); }
    catch (e) {
      _conn = null;
      if (UG_CONFIG.firebase && UG_CONFIG.firebase.apiKey) console.warn("[UG] Firebase לא זמין — מצב מקומי.", e && e.message ? e.message : e);
    }
  }
  function makeBackend(id) {
    if (!_conn) return LocalBackend(id);
    return _conn.kind === "rtdb" ? RtdbBackend(_conn.db, id) : FirestoreBackend(_conn.db, id);
  }

  /* =======================================================================
     API
     =======================================================================*/
  function emit() { subs.forEach((fn) => { try { fn(state); } catch (e) { console.error(e); } }); }

  function persist() { return backend.write(state).then(emit); }

  async function reloadFromRemote(remoteData) {
    if (remoteData) { state = normalize(remoteData); emit(); return; }
    const s = await backend.read();
    if (s) { state = s; emit(); }
  }

  async function init(id) {
    shopId = (id || "main");
    notFound = false;
    await connect();
    backend = makeBackend(shopId);
    let s = await backend.read();
    if (!s) {
      if (shopId === "main") { s = defaultState(); await backend.write(s); } // תאימות לאחור / הדגמה
      else { state = null; notFound = true; emit(); return null; }
    }
    state = s;
    backend.onRemote((remote) => reloadFromRemote(remote));
    if (backend.onGallery) backend.onGallery((list) => reloadGallery(list));
    // מנוי — קריאה ראשונית + מעקב בזמן אמת (אם אתה מפעיל את המספרה, זה יתעדכן מיד)
    if (backend.readSub) {
      try { subState = await backend.readSub(); } catch (e) { subState = null; }
      if (backend.onSub) backend.onSub((v) => { subState = v; emit(); });
    }
    // אינדקס קליל לפאנל ניהול המנויים — שם/טלפון עסקי/תאריך יצירה בלבד (ללא נתוני לקוחות)
    if (_conn && _conn.kind === "rtdb" && state && state.shop) {
      try {
        _conn.db.ref("shopIndex/" + shopId).set({
          name: state.shop.name || shopId,
          phone: state.shop.phone || "",
          createdAt: state.shop.createdAt || 0,
        });
      } catch (e) {}
    }
    reloadGallery();
    emit();
    return state;
  }

  function getSub() { return subState; }

  /* ---------- פאנל-על: ניהול מנויים (אתה בלבד) — עובד רק במצב Realtime DB ---------- */
  async function adminListShops() {
    await connect();
    if (!_conn || _conn.kind !== "rtdb") return [];
    const db = _conn.db;
    const [idxSnap, subsSnap] = await Promise.all([
      db.ref("shopIndex").once("value"),
      db.ref("subs").once("value"),
    ]);
    const idx = idxSnap.val() || {};
    const subsV = subsSnap.val() || {};
    return Object.keys(idx).map((k) => ({
      id: k,
      name: (idx[k] && idx[k].name) || k,
      phone: (idx[k] && idx[k].phone) || "",
      createdAt: (idx[k] && idx[k].createdAt) || 0,
      paidUntil: (subsV[k] && subsV[k].paidUntil) || 0,
    })).sort((a, z) => (z.createdAt || 0) - (a.createdAt || 0));
  }
  async function adminSetPaid(sid, paidUntil, note) {
    await connect();
    if (!_conn || _conn.kind !== "rtdb") return false;
    await _conn.db.ref("subs/" + sid).set({
      paidUntil: paidUntil || 0, note: note || "", updatedAt: Date.now(),
    });
    return true;
  }

  // יצירת מספרה חדשה (רישום ספר)
  async function createShop(id, data) {
    await connect();
    const b = makeBackend(id);
    const exists = await b.exists();
    if (exists) return { ok: false, reason: "הכתובת הזו כבר תפוסה — בחרו אחרת" };
    const s = defaultState();
    s.shop.createdAt = Date.now();   // תחילת תקופת הניסיון של המנוי
    s.shop.name = (data && data.name) || s.shop.name;
    s.shop.tagline = (data && data.tagline) || "מספרה";
    s.shop.ownerPass = String((data && data.ownerPass) || "");
    s.shop.phone = (data && data.phone) || "";
    s.shop.address = (data && data.address) || "";
    s.shop.ownerName = (data && data.ownerName) || "";
    s.shop.style = (data && data.style) || "sky";
    // שמות הספרים (אם המספרה בחרה כמה ספרים בשאלון)
    if (data && Array.isArray(data.staff)) {
      s.shop.staff = data.staff.map((n) => String(n || "").trim()).filter(Boolean);
    }
    // שירותים שנבחרו בשאלון (אם יש) — אחרת ברירת המחדל
    if (data && Array.isArray(data.services) && data.services.length) {
      s.services = data.services
        .filter((x) => x && x.name)
        .map((x) => ({
          id: u.uid(), name: String(x.name).trim(),
          price: Number(x.price) || 0,
          durationMin: Number(x.durationMin) || 30,
          icon: x.icon || "✂️", active: true,
        }));
    }
    await b.write(s);
    return { ok: true, id: id };
  }
  async function shopExists(id) { await connect(); return makeBackend(id).exists(); }
  // קריאת נתוני מספרה לפי כתובת (לאימות סיסמה בהתחברות) — בלי לשנות את המספרה הפעילה
  async function peekShop(id) { await connect(); try { return await makeBackend(id).read(); } catch (e) { return null; } }

  /* ---------- גלריה ---------- */
  function emitGallery() { gallerySubs.forEach((fn) => { try { fn(galleryCache); } catch (e) {} }); }
  function reloadGallery(list) {
    if (list) galleryCache = list;
    else if (backend && backend.readGallery) galleryCache = backend.readGallery();
    galleryCache = (galleryCache || []).slice().sort((a, z) => (z.createdAt || 0) - (a.createdAt || 0));
    emitGallery();
  }
  function subscribeGallery(fn) { gallerySubs.add(fn); fn(galleryCache); return () => gallerySubs.delete(fn); }
  function getGallery() { return galleryCache; }
  async function addPhoto(dataUrl, caption) {
    if (backend.addPhoto) { await backend.addPhoto({ dataUrl: dataUrl, caption: caption || "", createdAt: Date.now() }); reloadGallery(); }
  }
  async function removePhoto(id) {
    if (!backend.removePhoto) return;
    const item = (galleryCache || []).find((x) => x.id === id);
    await backend.removePhoto(id, !!(item && item.__legacy));
    reloadGallery();
  }

  function subscribe(fn) { subs.add(fn); if (state) fn(state); return () => subs.delete(fn); }
  function get() { return state; }

  /* ---------- מוטציות (בעלים) ---------- */
  function setDay(day, patch) {
    Object.assign(state.schedule[day], patch);
    return persist();
  }
  function saveShop(patch) { Object.assign(state.shop, patch); return persist(); }

  function upsertService(svc) {
    if (svc.id) {
      const i = state.services.findIndex((s) => s.id === svc.id);
      if (i >= 0) state.services[i] = Object.assign({}, state.services[i], svc);
    } else {
      svc.id = u.uid(); svc.active = true; state.services.push(svc);
    }
    return persist();
  }
  function removeService(id) {
    state.services = state.services.filter((s) => s.id !== id);
    return persist();
  }

  /* ---------- ספר לקוחות (ייבוא) ---------- */
  // מוסיף רשימת אנשי קשר {name, phone}; מדלג על כפילויות. מחזיר כמה נוספו בפועל.
  function addContacts(list) {
    state.contacts = state.contacts || [];
    const key = (n, p) => (String(p || "").replace(/\D/g, "")) + "|" + String(n || "").trim();
    const seen = new Set(state.contacts.map((c) => key(c.name, c.phone)));
    let added = 0;
    (list || []).forEach((c) => {
      const name = String((c && c.name) || "").trim();
      const phone = String((c && c.phone) || "").trim();
      if (!name && !phone) return;
      const k = key(name, phone);
      if (seen.has(k)) return;
      seen.add(k);
      state.contacts.push({ id: u.uid(), name, phone });
      added++;
    });
    return persist().then(() => added);
  }
  function removeContact(id) {
    state.contacts = (state.contacts || []).filter((c) => c.id !== id);
    return persist();
  }

  /* ---------- הודעה קבוצתית ללקוחות (נשלחת כפוש ע״י ה-worker כל כמה דקות) ---------- */
  function addBroadcast(text) {
    const msg = String(text || "").trim();
    if (!msg) return Promise.resolve(null);
    state.broadcasts = state.broadcasts || [];
    const entry = { id: u.uid(), text: msg, createdAt: Date.now() };
    state.broadcasts.push(entry);
    if (state.broadcasts.length > 50) state.broadcasts = state.broadcasts.slice(-50);
    return persist().then(() => entry);
  }

  /* ---------- ימי סגירה / חופשה ---------- */
  function addClosedDates(list) {
    state.closedDates = state.closedDates || [];
    const set = new Set(state.closedDates);
    (list || []).forEach((k) => { if (/^\d{4}-\d{2}-\d{2}$/.test(k)) set.add(k); });
    state.closedDates = [...set].sort();
    return persist();
  }
  function removeClosedDate(key) {
    state.closedDates = (state.closedDates || []).filter((k) => k !== key);
    return persist();
  }

  /* ---------- הזמנת תור (עם הגנה מפני כפילויות) ---------- */
  function buildBooking(cur, data) {
    const svc = cur.services.find((s) => s.id === data.serviceId);
    if (!svc) return { ok: false, reason: "השירות אינו קיים יותר" };
    const startMin = u.toMin(data.start);
    const endMin = startMin + svc.durationMin;
    // האם הבעלים חסם את השעה הזו?
    if ((cur.blocks || []).includes(data.date + "|" + data.start)) {
      return { ok: false, reason: "השעה כבר אינה זמינה" };
    }
    // בדיקת חפיפה מול תורים קיימים (מדלגים על התור המקורי בעת שינוי מועד)
    const clash = cur.bookings.some((b) => {
      if (b.status === "cancelled" || b.date !== data.date) return false;
      if (data.excludeBookingId && b.id === data.excludeBookingId) return false;
      const bs = u.toMin(b.start), be = u.toMin(b.end);
      return startMin < be && endMin > bs;
    });
    if (clash) return { ok: false, reason: "התור נתפס הרגע — נסו שעה אחרת" };

    // כמה פעמים הלקוח הזה כבר לא הגיע בעבר (לפי מזהה או טלפון) — כדי להתריע למנהל
    const dphone = data.phone ? u.normalizePhone(data.phone) : "";
    const priorNoShow = cur.bookings.filter((b) =>
      b.status === "noshow" &&
      (b.userId === data.userId || (dphone && b.phone && u.normalizePhone(b.phone) === dphone))
    ).length;

    const booking = {
      id: u.uid(),
      serviceId: svc.id, serviceName: svc.name, price: svc.price, durationMin: svc.durationMin,
      date: data.date, start: data.start, end: u.toHHMM(endMin),
      userId: data.userId, userName: data.userName, phone: data.phone || "", email: data.email || "",
      staff: data.staff || "",   // ספר מועדף שהלקוח ביקש (בקשה בלבד — לא מובטח)
      priorNoShow: priorNoShow,  // מספר פעמים שהלקוח לא הגיע בעבר
      status: "booked", createdAt: Date.now(),
    };
    cur.bookings.push(booking);
    return { ok: true, booking };
  }

  async function createBooking(data) {
    if (backend.transactBooking) {
      const res = await backend.transactBooking((cur) => buildBooking(cur, data));
      // Firestore יעדכן דרך onSnapshot; נטען מיידית ליתר ביטחון
      await reloadFromRemote();
      return res;
    }
    // מקומי: קרא מחדש את המצב העדכני לפני כתיבה כדי לצמצם התנגשויות
    const latest = backend.read();
    if (latest) state = latest;
    const res = buildBooking(state, data);
    if (res.ok) await persist();
    return res;
  }

  /* האם משבצת ברשת השעות פנויה כרגע להזמנה */
  function isSlotFree(cur, dateKey, start) {
    const dow = u.parseKey(dateKey).getDay();
    const sched = cur.schedule[dow];
    if (!sched || !sched.active) return false;
    const step = cur.shop.slotStep || 45;
    const t = u.toMin(start), end = t + step;
    if (t < u.toMin(sched.open) || end > u.toMin(sched.close)) return false;
    if ((cur.blocks || []).includes(dateKey + "|" + start)) return false;
    if (u.dateTime(dateKey, start).getTime() <= Date.now()) return false;
    return !cur.bookings.some((b) =>
      b.status !== "cancelled" && b.date === dateKey &&
      t < u.toMin(b.end) && end > u.toMin(b.start));
  }

  /* תור בוטל → אם יש ממתינים לשעה שהתפנתה, יוצרים להם התראת "התפנה תור" */
  function processFreed(cur, booking) {
    const created = [];
    (cur.waitlist || []).forEach((w) => {
      if (w.date !== booking.date) return;
      if (isSlotFree(cur, w.date, w.start)) {
        created.push({
          id: u.uid(), userId: w.userId, userName: w.userName,
          date: w.date, start: w.start, createdAt: Date.now(),
        });
      }
    });
    if (created.length) {
      const freed = new Set(created.map((a) => a.userId + "|" + a.date + "|" + a.start));
      cur.waitlist = cur.waitlist.filter((w) => !freed.has(w.userId + "|" + w.date + "|" + w.start));
      cur.alerts = (cur.alerts || []).concat(created);
    }
  }

  function refreshLocal() {
    if (backend.mode === "local") { const latest = backend.read(); if (latest) state = latest; }
  }

  async function setBookingStatus(id, status, by) {
    refreshLocal();
    const b = state.bookings.find((x) => x.id === id);
    if (b) {
      b.status = status;
      if (status === "cancelled") {
        if (by) b.cancelledBy = by;
        processFreed(state, b);
      }
      await persist();
    }
    return b;
  }

  /* ---------- רשימת המתנה ---------- */
  async function joinWaitlist(data) {
    refreshLocal();
    state.waitlist = state.waitlist || [];
    const dup = state.waitlist.some((w) =>
      w.userId === data.userId && w.date === data.date && w.start === data.start);
    if (!dup) {
      state.waitlist.push({
        id: u.uid(), date: data.date, start: data.start,
        userId: data.userId, userName: data.userName, phone: data.phone || "",
        createdAt: Date.now(),
      });
      await persist();
    }
  }
  async function leaveWaitlist(id) {
    refreshLocal();
    state.waitlist = (state.waitlist || []).filter((w) => w.id !== id);
    await persist();
  }
  async function consumeAlert(ids) {
    refreshLocal();
    const set = new Set(Array.isArray(ids) ? ids : [ids]);
    state.alerts = (state.alerts || []).filter((a) => !set.has(a.id));
    await persist();
  }

  /* ---------- טוקן פוש (FCM) ---------- */
  async function savePushToken(userId, token, isOwner) {
    if (!backend || backend.mode !== "cloud" || !backend.saveToken) return;
    try {
      await backend.saveToken(userId, token);
      if (isOwner) await backend.saveToken("owner_" + shopId, token);
    } catch (e) { console.warn("[UG] saveToken failed", e && e.message); }
  }

  /* ---------- ביקורות ---------- */
  async function addReview(r) {
    refreshLocal();
    state.reviews = state.reviews || [];
    const i = state.reviews.findIndex((x) => x.bookingId === r.bookingId && x.userId === r.userId);
    if (i >= 0) state.reviews[i] = Object.assign({}, state.reviews[i], r, { updatedAt: Date.now() });
    else state.reviews.push(Object.assign({ id: u.uid(), createdAt: Date.now() }, r));
    await persist();
  }

  // מחיקת רשומת תור לצמיתות (לניקוי הדוח)
  async function deleteBooking(id) {
    refreshLocal();
    state.bookings = state.bookings.filter((b) => b.id !== id);
    await persist();
  }

  // סימון/ביטול חסימה של שעה (בעלים)
  async function setBlock(dateKey, time, blocked) {
    if (backend.mode === "local") { const latest = backend.read(); if (latest) state = latest; }
    const key = dateKey + "|" + time;
    state.blocks = state.blocks || [];
    const has = state.blocks.includes(key);
    if (blocked && !has) state.blocks.push(key);
    else if (!blocked && has) state.blocks = state.blocks.filter((k) => k !== key);
    await persist();
  }

  return {
    init, subscribe, get,
    setDay, saveShop, upsertService, removeService,
    addContacts, removeContact, addBroadcast, addClosedDates, removeClosedDate,
    createBooking, setBookingStatus, setBlock, deleteBooking,
    joinWaitlist, leaveWaitlist, consumeAlert, addReview, savePushToken,
    subscribeGallery, getGallery, addPhoto, removePhoto,
    createShop, shopExists, peekShop,
    getSub, adminListShops, adminSetPaid,
    get mode() { return backend ? backend.mode : "local"; },
    get shopId() { return shopId; },
    get notFound() { return notFound; },
  };
})();
