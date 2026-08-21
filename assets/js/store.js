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
  const MKEY = (id) => "ug_media_v1__" + id;         // רקע/לוגו — צומת נפרד

  let shopId = "main";
  let state = null;
  let backend = null;
  let notFound = false;
  let subState = null;   // מידע המנוי של המספרה הפעילה (subs/<id>) — לקריאה בלבד
  const subs = new Set();
  let galleryCache = [];
  const gallerySubs = new Set();
  let mediaCache = {};   // רקע/לוגו של המספרה הפעילה (shopMedia/<id>) — נטען ברקע
  const mediaSubs = new Set();
  // פרטי לקוח (שם/טלפון/מייל) של תורים — נשמרים בצומת נפרד ומוגן (private/<id>/bk)
  // שרק בעל המספרה (המחובר עם חשבון) יכול לקרוא. הצומת הציבורי של המספרה נשאר
  // עם זמינות בלבד (תאריך/שעה), בלי פרטים מזהים. ראו piiPrivate למטה.
  let privCache = {};    // { bookingId: { name, phone, email } } — ריק ללא-בעלים
  const PKEY = (id) => "ug_priv_v1__" + id;   // מפתח מקומי לצומת הפרטי
  // ספר הלקוחות (contacts) — מידע של הבעלים בלבד. במספרה מאובטחת הוא יוצא מהצומת
  // הציבורי ונשמר בצומת פרטי (private/<id>/contacts) שרק הבעלים קורא/כותב.
  let contactsCache = [];
  const contactsSubs = new Set();
  const CKEY = (id) => "ug_contacts_v1__" + id;   // מפתח מקומי לספר הלקוחות

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
      products: [],          // מוצרים למכירה (קרמים וכו') — שם, מחיר, תמונה, תיאור
      contacts: [],          // ספר הלקוחות שהספר ייבא (שם + טלפון)
      closedDates: [],       // ימי סגירה/חופשה מלאים: "YYYY-MM-DD"
      blockedClients: [],    // לקוחות חסומים (לא יכולים לקבוע תור אונליין)
      blocks: [],            // שעות בתוך הפעילות שהבעלים סימן כלא-פנויות: "YYYY-MM-DD|HH:MM"
      opens: [],             // שעות מחוץ לפעילות שהבעלים פתח ליום ספציפי: "YYYY-MM-DD|HH:MM"
      waitlist: [],          // רשימת המתנה לשעות תפוסות
      alerts: [],            // "התפנה תור" — התראות ממתינות למשתמשים
      reviews: [],           // דירוגים וביקורות של לקוחות
      updatedAt: Date.now(),
    };
  }

  function clone(o) { return JSON.parse(JSON.stringify(o)); }

  /* RTDB לא שומר מערכים ממש: מערך עם "חורים" חוזר כאובייקט עם מפתחות מספריים,
     ומערך ריק לא נשמר כלל וחוזר null. כאן מחזירים תמיד מערך אמיתי. */
  function asArray(v) {
    if (Array.isArray(v)) return v.filter((x) => x != null);
    if (v && typeof v === "object") return Object.keys(v).map((k) => v[k]).filter((x) => x != null);
    return [];
  }

  /* מיזוג בטוח מול ברירת המחדל (למקרה של גרסאות ישנות בזיכרון) */
  function normalize(s) {
    const base = defaultState();
    if (!s || typeof s !== "object") return base;
    s.shop = Object.assign({}, base.shop, s.shop);
    if (!s.schedule) s.schedule = base.schedule;
    for (let i = 0; i < 7; i++) s.schedule[i] = Object.assign({}, base.schedule[i], s.schedule[i]);
    if (!Array.isArray(s.services)) s.services = base.services;
    if (!Array.isArray(s.bookings)) s.bookings = [];
    if (!Array.isArray(s.products)) s.products = [];
    if (!Array.isArray(s.contacts)) s.contacts = [];
    if (!Array.isArray(s.closedDates)) s.closedDates = [];
    if (!Array.isArray(s.blockedClients)) s.blockedClients = [];
    if (!Array.isArray(s.blocks)) s.blocks = [];
    if (!Array.isArray(s.opens)) s.opens = [];
    if (!Array.isArray(s.waitlist)) s.waitlist = [];
    if (!Array.isArray(s.alerts)) s.alerts = [];
    if (!Array.isArray(s.reviews)) s.reviews = [];
    if (!Array.isArray(s.shop.staff)) s.shop.staff = [];
    if (!s.shop.address) s.shop.address = base.shop.address;
    // ניקוי שרידים של תשלום בביט (הוסר) — מהגדרות המספרה ומהזמנות ישנות
    delete s.shop.bitEnabled; delete s.shop.bitPhone; delete s.shop.tipEnabled;
    s.bookings.forEach((b) => {
      if (!b) return;
      delete b.paidClaimed; delete b.paidConfirmed; delete b.paidAmount;
      delete b.tipAmount; delete b.paidAt;
    });
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
    const skey = KEY(id), gkey = GKEY(id), mkey = MKEY(id), pkey = PKEY(id), ckey = CKEY(id);
    let bc = null;
    try { bc = new BroadcastChannel("ug_barber_" + id); } catch (e) {}
    const listeners = { state: [], gallery: [], media: [], priv: [], contacts: [] };
    if (bc) bc.onmessage = (ev) => {
      const t = ev && ev.data && ev.data.t;
      if (t === "gallery") listeners.gallery.forEach((fn) => fn());
      else if (t === "media") listeners.media.forEach((fn) => fn(readM()));
      else if (t === "priv") listeners.priv.forEach((fn) => fn(readP()));
      else if (t === "contacts") listeners.contacts.forEach((fn) => fn(readC()));
      else listeners.state.forEach((fn) => fn());
    };
    window.addEventListener("storage", (e) => {
      if (e.key === skey) listeners.state.forEach((fn) => fn());
      if (e.key === gkey) listeners.gallery.forEach((fn) => fn());
      if (e.key === mkey) listeners.media.forEach((fn) => fn(readM()));
      if (e.key === pkey) listeners.priv.forEach((fn) => fn(readP()));
      if (e.key === ckey) listeners.contacts.forEach((fn) => fn(readC()));
    });
    function readC() { try { return JSON.parse(localStorage.getItem(ckey) || "[]"); } catch (e) { return []; } }
    function readG() { try { return JSON.parse(localStorage.getItem(gkey) || "[]"); } catch (e) { return []; } }
    function writeG(list) {
      try { localStorage.setItem(gkey, JSON.stringify(list)); } catch (e) {}
      try { if (bc) bc.postMessage({ t: "gallery" }); } catch (e) {}
    }
    function readM() { try { return JSON.parse(localStorage.getItem(mkey) || "{}"); } catch (e) { return {}; } }
    function readP() { try { return JSON.parse(localStorage.getItem(pkey) || "{}"); } catch (e) { return {}; } }
    function writeP(m) {
      try { localStorage.setItem(pkey, JSON.stringify(m)); } catch (e) {}
      try { if (bc) bc.postMessage({ t: "priv" }); } catch (e) {}
      listeners.priv.forEach((fn) => fn(m));
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
      // מדיה (רקע/לוגו) — צומת נפרד כדי שלא יחסום את טעינת המספרה
      onMedia(cb) { listeners.media.push(cb); cb(readM()); },
      saveMedia(patch) {
        const m = Object.assign(readM(), patch);
        try { localStorage.setItem(mkey, JSON.stringify(m)); } catch (e) {}
        try { if (bc) bc.postMessage({ t: "media" }); } catch (e) {}
        listeners.media.forEach((fn) => fn(m));
        return Promise.resolve();
      },
      // פרטי לקוח של תורים (צומת פרטי, מוגן לבעלים בענן; מקומית — אחסון נפרד)
      onBookingPriv(cb) { listeners.priv.push(cb); cb(readP()); },
      savePrivate(bkId, priv) { const m = readP(); m[bkId] = priv; writeP(m); return Promise.resolve(); },
      removePrivate(bkId) { const m = readP(); delete m[bkId]; writeP(m); return Promise.resolve(); },
      // ספר הלקוחות (צומת פרטי, מוגן לבעלים בענן; מקומית — אחסון נפרד)
      onContacts(cb) { listeners.contacts.push(cb); cb(readC()); },
      saveContacts(arr) {
        try { localStorage.setItem(ckey, JSON.stringify(arr || [])); } catch (e) {}
        try { if (bc) bc.postMessage({ t: "contacts" }); } catch (e) {}
        listeners.contacts.forEach((fn) => fn(arr || []));
        return Promise.resolve();
      },
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
      // כתיבת שדות בודדים (מקבילה ל-updateChildren של RTDB)
      updateChildren(patch) { return ref.update(patch); },
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
    const mediaRef = db.ref("shopMedia/" + id);   // רקע/לוגו — צומת נפרד, נטען ברקע
    const privRef = db.ref("private/" + id + "/bk");   // פרטי לקוח — צומת מוגן לבעלים
    const contactsRef = db.ref("private/" + id + "/contacts");   // ספר הלקוחות — מוגן לבעלים
    return {
      mode: "cloud",
      _db: db, _ref: ref,
      // מדיה (רקע/לוגו) — צומת נפרד כדי שלא יחסום את טעינת המספרה. נטען ומאזין ברקע.
      onMedia(cb) { mediaRef.on("value", (snap) => cb(snap.val() || {})); },
      saveMedia(patch) { return mediaRef.update(clone(patch)); },
      // פרטי לקוח של תורים — צומת מוגן: רק הבעלים (לפי ownerUid) יכול לקרוא. ללקוח
      // הקריאה נחסמת בחוקי האבטחה, ואז callback השגיאה משאיר את המטמון ריק,
      // והתצוגה נופלת-לאחור לצומת הציבורי (שאין בו פרטים). כתיבה: כל אחד יכול
      // ליצור רשומה חדשה (התור שלו), אך לא לשנות/לקרוא של אחרים.
      onBookingPriv(cb) {
        privRef.on("value", (snap) => cb(snap.val() || {}), () => { /* לא-בעלים — נחסם */ });
      },
      savePrivate(bkId, priv) { return privRef.child(bkId).set(clone(priv)); },
      removePrivate(bkId) { return privRef.child(bkId).remove(); },
      // ספר הלקוחות — צומת מוגן לבעלים בלבד (קריאה+כתיבה לפי ownerUid). ללקוח
      // הקריאה נחסמת → מטמון ריק (ממילא רק הבעלים משתמש בספר הלקוחות).
      onContacts(cb) {
        contactsRef.on("value", (snap) => {
          const v = snap.val(); cb(Array.isArray(v) ? v : (v ? Object.keys(v).map((k) => v[k]) : []));
        }, () => { /* לא-בעלים — נחסם */ });
      },
      saveContacts(arr) { return contactsRef.set(clone(arr || [])); },
      read() {
        return ref.once("value").then((snap) => (snap.exists() ? normalize(snap.val()) : null));
      },
      write(s) {
        s.updatedAt = Date.now();
        return ref.set(clone(s));   // clone → JSON נקי (RTDB לא מקבל undefined)
      },
      // כתיבת חלק מצמתי המספרה בלבד (למשל רק bookings) — כתיבה אטומית לכמה נתיבים.
      // מאפשר ללקוח לכתוב תור/המתנה/ביקורת גם כשההגדרות נעולות לבעלים בלבד.
      updateChildren(patch) { return ref.update(clone(patch)); },
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
      /* הזמנה בטוחה מפני התנגשות.
         חשוב: כאן חייבת להיות טרנזקציה אמיתית של RTDB, ולא "קרא ואז כתוב".
         שני לקוחות שמזמינים באותו רגע (למשל אחרי התראת "התפנה תור", שמעירה
         את כל הממתינים בבת אחת) היו קוראים את אותו מצב, וכל אחד היה כותב את
         כל צומת המספרה מחדש — כך שההזמנה של הראשון נמחקת בשקט, למרות שהוא
         קיבל אישור. הטרנזקציה מריצה את הפונקציה שוב מול נתוני השרת בכל
         התנגשות, ולכן ההזמנה השנייה רואה את הראשונה ומזהה חפיפה.
         הטרנזקציה מוגבלת לצומת bookings בלבד, כדי שהזמנה של לקוח לא תדרוס
         שינוי הגדרות שהספר עשה באותו רגע. */
      transactBooking(build) {
        const bkRef = ref.child("bookings");
        return ref.once("value").then((snap) => {
          if (!snap.exists()) return { ok: false, reason: "המספרה לא נמצאה" };
          const base = normalize(snap.val());
          let out = null;
          return bkRef.transaction((curBookings) => {
            // RTDB עשוי להריץ את הפונקציה כמה פעמים; רק הריצה האחרונה נכתבת
            const cur = Object.assign({}, base, { bookings: asArray(curBookings) });
            out = build(cur);
            if (!out.ok) return;           // undefined = ביטול הטרנזקציה ללא כתיבה
            // clone → מסיר ערכי undefined (RTDB דוחה אותם ומפיל את כל ההזמנה)
            return clone(cur.bookings);
          }, undefined, false).then((r) => {
            if (out && !out.ok) return out;
            if (!r || !r.committed) return { ok: false, reason: "התור נתפס הרגע — נסו שעה אחרת" };
            return out || { ok: false, reason: "ההזמנה נכשלה — נסו שוב" };
          });
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
      const M = (m) => { try { if (window.__ugMark) window.__ugMark(m); } catch (e) {} };
      M("sdk-load-start");
      const V = "10.12.2";
      let chain = load(`https://www.gstatic.com/firebasejs/${V}/firebase-app-compat.js`)
        .then(() => { M("sdk-app-ok"); return load(`https://www.gstatic.com/firebasejs/${V}/firebase-${useRtdb ? "database" : "firestore"}-compat.js`); });
      if (useRtdb) {
        // התחברות אנונימית — כדי שחוקי האבטחה יוכלו לדרוש משתמש מחובר
        chain = chain.then(() => { M("sdk-db-ok"); return load(`https://www.gstatic.com/firebasejs/${V}/firebase-auth-compat.js`); });
      }
      chain.then(() => {
        M("sdk-all-ok");
        firebase.initializeApp(cfg);
        if (useRtdb) {
          try {
            // מתחברים אנונימית רק אם אין משתמש מחובר (למשל ספר שנכנס עם Google).
            // אחרת signInAnonymously היה דורס את חשבון הגוגל בכל טעינה מחדש.
            firebase.auth().onAuthStateChanged((user) => {
              if (!user) firebase.auth().signInAnonymously().catch(() => {});
            });
          } catch (e) {}
        }
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

  // דיווח על כשל כתיבה (למשל חוקי האבטחה חסמו) — האפליקציה נרשמת כדי להציע
  // התחברות מחדש כשמספרה מאובטחת מנוהלת בלי חשבון הבעלים.
  let writeErrorCb = null;
  function onWriteError(fn) { writeErrorCb = fn; }
  function reportWriteError(err) {
    if (writeErrorCb) { try { writeErrorCb(err); } catch (e) {} }
    return err;
  }

  function persist() {
    return backend.write(state).then(emit, (err) => { throw reportWriteError(err); });
  }

  /* כתיבת חלק מצמתי המספרה בלבד (bookings/waitlist/alerts/reviews).
     פעולות של לקוח עוברות דרך כאן, כדי שיוכלו לכתוב את הצומת שלהן גם כששאר
     ההגדרות נעולות לבעלים. במצב מקומי (בלי ענן) — כתיבה מלאה רגילה. */
  function persistChildren(keys) {
    if (backend.updateChildren) {
      const patch = {};
      keys.forEach((k) => { patch[k] = state[k] === undefined ? null : state[k]; });
      state.updatedAt = Date.now();
      patch.updatedAt = state.updatedAt;
      return backend.updateChildren(patch).then(emit, (err) => { throw reportWriteError(err); });
    }
    return persist();
  }

  async function reloadFromRemote(remoteData) {
    if (remoteData) { state = normalize(remoteData); emit(); return; }
    const s = await backend.read();
    if (s) { state = s; emit(); }
  }

  const mark = (m) => { try { if (window.__ugMark) window.__ugMark(m); } catch (e) {} };

  async function init(id) {
    shopId = (id || "main");
    notFound = false;
    mark("init");
    await connect();
    mark("connected(" + (_conn ? _conn.kind : "local") + ")");
    backend = makeBackend(shopId);
    let s = await backend.read();
    mark("read-done(" + (s ? "ok" : "null") + ")");
    if (!s) {
      if (shopId === "main") { s = defaultState(); await backend.write(s); } // תאימות לאחור / הדגמה
      else { state = null; notFound = true; emit(); return null; }
    }
    state = s;
    backend.onRemote((remote) => reloadFromRemote(remote));
    if (backend.onGallery) backend.onGallery((list) => reloadGallery(list));
    // מדיה (רקע/לוגו) — נטענת ברקע ואינה חוסמת את הרינדור הראשון של המספרה.
    // כשהיא מגיעה, מרעננים כדי שהתמונות יופיעו (טעינה מתקדמת).
    if (backend.onMedia) backend.onMedia((m) => { mediaCache = m || {}; emitMedia(); });
    // פרטי לקוח של תורים — נטענים לבעלים בלבד (הקריאה נחסמת ללקוח). כשהם מגיעים,
    // מרעננים כדי שהשמות/הטלפונים יופיעו אצל הבעלים.
    if (backend.onBookingPriv) backend.onBookingPriv((m) => { privCache = m || {}; emit(); });
    // ספר הלקוחות — נטען לבעלים בלבד (הקריאה נחסמת ללקוח). כשהוא מגיע, מרעננים.
    if (backend.onContacts) backend.onContacts((arr) => { contactsCache = Array.isArray(arr) ? arr : []; emitContacts(); emit(); });
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
      pending: (subsV[k] && subsV[k].pending) || null,
    })).sort((a, z) =>
      // ממתינים לאישור תשלום קודם, ואז לפי תאריך יצירה
      (z.pending ? 1 : 0) - (a.pending ? 1 : 0) || (z.createdAt || 0) - (a.createdAt || 0));
  }
  async function adminSetPaid(sid, paidUntil, note) {
    await connect();
    if (!_conn || _conn.kind !== "rtdb") return false;
    await _conn.db.ref("subs/" + sid).set({
      paidUntil: paidUntil || 0, note: note || "", updatedAt: Date.now(),
      pending: null,   // ההפעלה מבטלת את סימון "ממתין לאישור תשלום"
    });
    return true;
  }

  /* הספר חזר מעמוד התשלום — מסמנים "ממתין לאישור" בלי לגעת ב-paidUntil */
  async function markPaymentPending(planId) {
    await connect();
    if (!_conn || _conn.kind !== "rtdb") return false;
    await _conn.db.ref("subs/" + shopId + "/pending").set({
      at: Date.now(), plan: String(planId || ""),
    });
    return true;
  }

  /* ---------- גיבוי ושחזור ----------
     מייצא את כל נתוני המספרה לקובץ JSON יחיד (כולל גלריה), כדי שהספר
     יוכל לשמור עותק אצלו. השחזור דורס את המצב הנוכחי לחלוטין. */
  function exportData(withGallery) {
    const snap = JSON.parse(JSON.stringify(state || {}));
    // במספרה מאובטחת ספר הלקוחות נמצא בצומת הפרטי — משלימים אותו לגיבוי כדי שלא יאבד
    if (extraPrivate() && (contactsCache || []).length) snap.contacts = JSON.parse(JSON.stringify(contactsCache));
    return {
      format: "barbertor-backup",
      version: 1,
      shopId: shopId,
      exportedAt: Date.now(),
      state: snap,
      // הגלריה כוללת תמונות מלאות (data URLs) ומנפחת מאוד את הקובץ — לפי בחירה
      gallery: withGallery ? JSON.parse(JSON.stringify(galleryCache || [])) : [],
    };
  }

  async function importData(dump) {
    if (!dump || dump.format !== "barbertor-backup" || !dump.state) {
      return { ok: false, reason: "הקובץ אינו גיבוי תקין של BarberTor" };
    }
    const incoming = normalize(JSON.parse(JSON.stringify(dump.state)));
    // הכתובת והבעלות נשארות של המספרה הנוכחית — גיבוי לא מעביר בעלות
    const keep = (state && state.shop) || {};
    incoming.shop = Object.assign({}, incoming.shop, {
      ownerUid: keep.ownerUid || incoming.shop.ownerUid || "",
      createdAt: keep.createdAt || incoming.shop.createdAt || Date.now(),
    });
    // במספרה מאובטחת — ספר הלקוחות עובר לצומת הפרטי (ולא לצומת הציבורי)
    if (extraPrivate() && Array.isArray(incoming.contacts) && backend.saveContacts) {
      contactsCache = incoming.contacts.slice();
      incoming.contacts = [];
      try { await backend.saveContacts(contactsCache); emitContacts(); } catch (e) {}
    }
    state = incoming;
    await persist();
    // תמונות גלריה — מתווספות לקיימות (לא דורסות), כי אין מחיקה קבוצתית
    if (Array.isArray(dump.gallery) && dump.gallery.length && backend.addPhoto) {
      const have = new Set((galleryCache || []).map((p) => p.dataUrl));
      for (const p of dump.gallery) {
        if (!p || !p.dataUrl || have.has(p.dataUrl)) continue;
        try { await backend.addPhoto({ dataUrl: p.dataUrl, caption: p.caption || "", createdAt: p.createdAt || Date.now() }); } catch (e) {}
      }
      reloadGallery();
    }
    emit();
    return { ok: true };
  }

  /* ---------- מחיקת מספרה לצמיתות ----------
     מוחק את כל הצמתים ששייכים למספרה. פעולה בלתי הפיכה. */
  async function deleteShop(passHash) {
    await connect();
    if (!_conn || _conn.kind !== "rtdb") {
      // מצב מקומי — מנקים את האחסון של המספרה הזו
      try {
        Object.keys(localStorage).forEach((k) => {
          if (k.indexOf(shopId) !== -1 && k.indexOf("ug_") === 0) localStorage.removeItem(k);
        });
      } catch (e) {}
      return { ok: true };
    }
    const db = _conn.db;
    // הנתונים עצמם — אם זה נכשל, המחיקה נכשלה
    try {
      await db.ref("shops/" + shopId).remove();
    } catch (e) {
      return { ok: false, reason: (e && e.message) || "המחיקה נכשלה" };
    }
    // שאר הצמתים — מיטב המאמץ; כשלון בודד לא הופך את המחיקה לכושלת.
    // system/pushState אינו נכתב מהלקוח (כללי האבטחה) — הקרון מנקה אותו לבד.
    const rest = [
      "shopIndex/" + shopId,
      "subs/" + shopId,
      "gallery/" + shopId,
      "private/" + shopId,        // כספת: פרטי לקוח + ספר הלקוחות
      "pushTokens/owner_" + shopId,
    ];
    if (passHash) rest.push("passHashes/" + passHash);
    await Promise.allSettled(rest.map((p) => db.ref(p).remove()));
    return { ok: true };
  }

  /* ---------- ייחודיות קוד הכניסה בין מספרות ----------
     שומרים רק hash של הקוד (passHashes/<hash> = true), כך שאפשר לזהות התנגשות
     בלי לחשוף את הקודים עצמם. עובד רק במצב ענן. */
  async function passcodeTaken(passHash) {
    await connect();
    if (!_conn || _conn.kind !== "rtdb" || !passHash) return false;
    try {
      const snap = await _conn.db.ref("passHashes/" + passHash).once("value");
      return snap.exists();
    } catch (e) { return false; }
  }
  async function registerPasscode(passHash, id) {
    if (!_conn || _conn.kind !== "rtdb" || !passHash) return;
    try { await _conn.db.ref("passHashes/" + passHash).set({ at: Date.now() }); } catch (e) {}
  }
  // שריון קוד של מספרה ותיקה (backfill) — כותב רק אם עוד לא רשום, בלי לדרוס
  async function registerPasscodeIfFree(passHash) {
    await connect();
    if (!_conn || _conn.kind !== "rtdb" || !passHash) return;
    try {
      const ref = _conn.db.ref("passHashes/" + passHash);
      const snap = await ref.once("value");
      if (!snap.exists()) await ref.set({ at: Date.now() });
    } catch (e) {}
  }

  // יצירת מספרה חדשה (רישום ספר). passHash — hash של קוד הכניסה לבדיקת ייחודיות.
  async function createShop(id, data, passHash) {
    await connect();
    const b = makeBackend(id);
    const exists = await b.exists();
    if (exists) return { ok: false, reason: "הכתובת הזו כבר תפוסה — בחרו אחרת" };
    // קוד כניסה שכבר בשימוש במספרה אחרת — דורשים קוד ייחודי
    if (passHash && await passcodeTaken(passHash)) {
      return { ok: false, reason: "קוד הכניסה הזה כבר בשימוש — בחרו קוד אחר" };
    }
    const s = defaultState();
    s.shop.createdAt = Date.now();   // תחילת תקופת הניסיון של המנוי
    s.shop.name = (data && data.name) || s.shop.name;
    s.shop.tagline = (data && data.tagline) || "מספרה";
    // סיסמת הניהול נשמרת רק כ-hash מלוחלח (ownerPassHash) — לעולם לא כטקסט גלוי,
    // כדי שלא ניתן יהיה לקרוא אותה מנתוני המספרה.
    s.shop.ownerPassHash = String((data && data.ownerPassHash) || "");
    s.shop.phone = (data && data.phone) || "";
    s.shop.address = (data && data.address) || "";
    s.shop.ownerName = (data && data.ownerName) || "";
    s.shop.about = (data && data.about) || "";
    s.shop.instagram = (data && data.instagram) || "";
    s.shop.tiktok = (data && data.tiktok) || "";
    s.shop.facebook = (data && data.facebook) || "";
    s.shop.youtube = (data && data.youtube) || "";
    // הלוגו נשמר בצומת המדיה הנפרד (shopMedia), לא בתוך המספרה — ראו saveMedia למטה.
    s.shop.heardFrom = (data && data.heardFrom) || "";   // מאיפה הספר הגיע אלינו
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
    // לוגו למספרה חדשה — נכתב לצומת המדיה הנפרד (לא לתוך המספרה)
    if (data && data.logo && b.saveMedia) { try { await b.saveMedia({ logo: data.logo }); } catch (e) {} }
    if (passHash) await registerPasscode(passHash, id);   // שריון הקוד למניעת כפילות
    return { ok: true, id: id };
  }
  /* מיגרציה בטוחה של סיסמת ניהול: קובע hash מלוחלח ומוחק את הסיסמה הגלויה הישנה.
     נקרא אחרי התחברות מוצלחת של הבעלים. best-effort — אם הכתיבה נחסמת (מספרה
     מאובטחת שאינה בבעלות המשתמש), לא קורה כלום וההשוואה הישנה עדיין עובדת. */
  async function setOwnerPassHash(handle, hash) {
    if (!handle || !hash) return;
    await connect();
    const b = makeBackend(handle);
    try {
      if (b.updateChildren) {
        await b.updateChildren({ "shop/ownerPassHash": String(hash), "shop/ownerPass": null });
      } else if (b.read && b.write) {
        const s = b.read();
        if (s && s.shop) { s.shop.ownerPassHash = String(hash); delete s.shop.ownerPass; await b.write(s); }
      }
    } catch (e) { /* best-effort — לא חוסם התחברות */ }
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

  /* ---------- מדיה: רקע (cover) ולוגו (logo) ----------
     נשמרים בצומת נפרד (shopMedia/<id>) כדי שלא יחסמו את טעינת המספרה. התצוגה
     מעדיפה את המדיה, ונופלת-לאחור לערך הישן שבתוך המספרה (עד למיגרציה). */
  function emitMedia() { mediaSubs.forEach((fn) => { try { fn(mediaCache); } catch (e) {} }); }
  function subscribeMedia(fn) { mediaSubs.add(fn); fn(mediaCache); return () => mediaSubs.delete(fn); }
  function getMedia() { return mediaCache || {}; }
  // פרטי הלקוח (שם/טלפון/מייל) של תור לפי מזההו — זמין לבעלים בלבד (אחרת ריק).
  function getBookingPriv(id) { return (privCache && privCache[id]) || null; }
  // ספר הלקוחות. במספרה מאובטחת (extraPrivate) — מהצומת הפרטי; אחרת מתוך המספרה.
  function emitContacts() { contactsSubs.forEach((fn) => { try { fn(getContacts()); } catch (e) {} }); }
  function subscribeContacts(fn) { contactsSubs.add(fn); fn(getContacts()); return () => contactsSubs.delete(fn); }
  function getContacts() { return extraPrivate() ? (contactsCache || []) : ((state && state.contacts) || []); }
  // קביעת רקע/לוגו: kind = "cover" | "logo". dataUrl ריק ("") = הסרה.
  async function setShopMedia(kind, dataUrl) {
    if (kind !== "cover" && kind !== "logo") return;
    mediaCache = Object.assign({}, mediaCache, { [kind]: dataUrl || "" });
    emitMedia();   // עדכון מיידי בתצוגה
    if (backend && backend.saveMedia) { try { await backend.saveMedia({ [kind]: dataUrl || "" }); } catch (e) { throw reportWriteError(e); } }
    // ניקוי הערך הישן מצומת המספרה (אם היה שם) — כדי שהצומת יישאר קטן ומהיר
    try {
      if (state && state.shop && state.shop[kind] !== undefined && state.shop[kind] !== "") {
        delete state.shop[kind];
        if (backend && backend.updateChildren) await backend.updateChildren({ ["shop/" + kind]: null });
      }
    } catch (e) { /* best-effort */ }
  }
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

  /* ---------- מוצרים למכירה ---------- */
  function upsertProduct(prod) {
    state.products = state.products || [];
    if (prod.id) {
      const i = state.products.findIndex((p) => p.id === prod.id);
      if (i >= 0) state.products[i] = Object.assign({}, state.products[i], prod);
    } else {
      prod.id = u.uid(); prod.active = true; prod.createdAt = Date.now();
      state.products.push(prod);
    }
    return persist();
  }
  function removeProduct(id) {
    state.products = (state.products || []).filter((p) => p.id !== id);
    return persist();
  }

  /* ---------- ספר לקוחות (ייבוא) ---------- */
  // מוסיף רשימת אנשי קשר {name, phone}; מדלג על כפילויות. מחזיר כמה נוספו בפועל.
  // במספרה מאובטחת (extraPrivate) הספר נשמר בצומת הפרטי; אחרת בתוך המספרה.
  function addContacts(list) {
    const priv = extraPrivate();
    const base = priv ? (contactsCache || []).slice() : (state.contacts = state.contacts || []);
    const key = (n, p) => (String(p || "").replace(/\D/g, "")) + "|" + String(n || "").trim();
    const seen = new Set(base.map((c) => key(c.name, c.phone)));
    let added = 0;
    (list || []).forEach((c) => {
      const name = String((c && c.name) || "").trim();
      const phone = String((c && c.phone) || "").trim();
      if (!name && !phone) return;
      const k = key(name, phone);
      if (seen.has(k)) return;
      seen.add(k);
      base.push({ id: u.uid(), name, phone });
      added++;
    });
    if (priv) { contactsCache = base; emitContacts(); return backend.saveContacts(base).then(emit).then(() => added); }
    return persist().then(() => added);
  }
  function removeContact(id) {
    if (extraPrivate()) {
      contactsCache = (contactsCache || []).filter((c) => c.id !== id);
      emitContacts();
      return backend.saveContacts(contactsCache).then(emit);
    }
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

  /* ---------- פתיחה/סגירה של שעה ביום ספציפי ----------
     available=true פותח את השעה ללקוחות. inHours מבדיל בין שעה שבתוך הפעילות
     (מנוהלת דרך blocks) לבין שעה מחוץ לפעילות (מנוהלת דרך opens). */
  async function setSlotOpen(dateKey, start, available, inHours) {
    if (backend.mode === "local") { const latest = backend.read(); if (latest) state = latest; }
    const key = dateKey + "|" + start;
    state.blocks = state.blocks || [];
    state.opens = state.opens || [];
    if (available) {
      if (inHours) state.blocks = state.blocks.filter((k) => k !== key);
      else if (!state.opens.includes(key)) state.opens.push(key);
    } else {
      if (inHours) { if (!state.blocks.includes(key)) state.blocks.push(key); }
      else state.opens = state.opens.filter((k) => k !== key);
    }
    return persist();
  }

  /* ---------- חסימת לקוחות בעייתיים (לא יוכלו לקבוע תור אונליין) ---------- */
  function blockClient(entry) {
    state.blockedClients = state.blockedClients || [];
    const p = entry.phone ? u.normalizePhone(entry.phone) : "";
    const dup = state.blockedClients.some((b) =>
      (p && b.phone === p) || (entry.userId && b.userId === entry.userId));
    if (!dup) state.blockedClients.push({
      id: u.uid(), name: entry.name || "", phone: p, userId: entry.userId || "", createdAt: Date.now(),
    });
    return persist();
  }
  function unblockClient(key) {
    state.blockedClients = (state.blockedClients || []).filter((b) =>
      b.id !== key && b.phone !== key && b.userId !== key);
    return persist();
  }

  /* ---------- זיהוי הזמנות חשודות (ספאם) ---------- */
  function detectSpam(cur, data, dphone) {
    if (String(data.userId || "").indexOf("owner:") === 0) return null;
    var now = Date.now();
    var future = cur.bookings.filter(function (b) {
      return b.status !== "cancelled" && u.dateTime(b.date, b.start).getTime() > now;
    });
    var mine = future.filter(function (b) {
      return b.userId === data.userId ||
        (dphone && b.phone && u.normalizePhone(b.phone) === dphone);
    });
    // 3+ תורים עתידיים פעילים מאותו לקוח
    if (mine.length >= 3) return { reason: "multi", count: mine.length + 1 };
    // 3+ הזמנות מאותו לקוח ב-15 הדקות האחרונות
    var window15 = 15 * 60000;
    var recentMine = mine.filter(function (b) { return b.createdAt && now - b.createdAt < window15; });
    if (recentMine.length >= 2) return { reason: "burst", count: recentMine.length + 1 };
    // 5+ הזמנות מלקוחות שונים ב-10 דקות
    var window10 = 10 * 60000;
    var recentAll = future.filter(function (b) { return b.createdAt && now - b.createdAt < window10; });
    if (recentAll.length >= 5) return { reason: "flood", count: recentAll.length + 1 };
    return null;
  }

  /* האם להסתיר את פרטי הלקוח של תור זה? כן — בכל מספרה מאובטחת עם חשבון
     (ownerUid קיים; רק אז חוקי האבטחה יודעים לזהות את הבעלים ולתת לו לקרוא את
     הצומת הפרטי). מספרה שאינה מאובטחת בחשבון — הפרטים נשארים בתור עד לאבטחתה. */
  function piiPrivate(cur) {
    const st = cur || state;
    return !!(st && st.shop && st.shop.ownerUid);
  }

  /* שער נפרד ל"שלב 4" — ספר הלקוחות ורשימת ההמתנה. כרגע מופעל למספרת "try" בלבד
     לבדיקה; אחרי אימות יורחב לכל המספרות המאובטחות (הסרת התנאי על shopId). */
  function extraPrivate(cur) {
    const st = cur || state;
    return shopId === "try" && !!(st && st.shop && st.shop.ownerUid);
  }

  /* ---------- הזמנת תור (עם הגנה מפני כפילויות) ---------- */
  function buildBooking(cur, data) {
    const svc = cur.services.find((s) => s.id === data.serviceId);
    if (!svc) return { ok: false, reason: "השירות אינו קיים יותר" };
    const dphone = data.phone ? u.normalizePhone(data.phone) : "";
    // לקוח חסום — לא יכול לקבוע אונליין (הספר עדיין יכול להוסיף ידנית)
    const isBlocked = (cur.blockedClients || []).some((b) =>
      (dphone && b.phone && b.phone === dphone) || (b.userId && b.userId === data.userId));
    if (isBlocked && String(data.userId || "").indexOf("owner:") !== 0) {
      return { ok: false, reason: "לא ניתן לקבוע תור אונליין — צרו קשר עם המספרה" };
    }
    const startMin = u.toMin(data.start);
    const endMin = startMin + svc.durationMin;
    /* תור שגולש מעבר לחצות. בלי הבדיקה הזו שעת הסיום נשמרת כ-"24:15" — שעה
       שאינה קיימת — ובדיקת החפיפה (שמשווה רק תורים באותו תאריך) לא הייתה
       חוסמת את המשבצות המוקדמות של המחרת, כך שהספר היה נקבע פעמיים.
       רלוונטי מאז שאפשר לפתוח שעות גם בלילה. */
    if (endMin > 24 * 60) {
      return { ok: false, reason: "התור חורג מעבר לחצות — בחרו שעה מוקדמת יותר" };
    }
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

    /* חלון ההרשמה. הבעלים פטור משניהם — הוא עדיין יכול לרשום לקוח מזדמן
       ברגע האחרון, או לתעד תספורת שכבר קרתה. */
    if (String(data.userId || "").indexOf("owner:") !== 0) {
      const lead = u.dateTime(data.date, data.start).getTime() - Date.now();
      // מועד שכבר עבר — לא ניתן להזמין (גם אם דף ישן עדיין מציג אותו)
      if (lead <= 0) return { ok: false, reason: "המועד הזה כבר עבר — בחרו מועד אחר" };
      // "סגירת הרשמה": משבצת פנויה שנותרו לה פחות מהזמן שהוגדר מוסתרת מהלקוח,
      // ולכן גם אי אפשר להזמין אותה — למשל מדף שנשאר פתוח ברקע.
      const cutoffMin = Math.max(0, Number((cur.shop && cur.shop.hideFreeBeforeMin) || 0));
      if (cutoffMin && lead < cutoffMin * 60000) {
        return { ok: false, reason: "ההרשמה לתור הזה נסגרה — בחרו מועד אחר" };
      }
      /* תור בתוך שעות הפעילות חייב להסתיים עד שעת הסגירה — כדי ששירות ארוך
         (למשל 90 דק׳ סמוך לסגירה) לא ייקבע ויגלוש אחרי הסגירה. שעה שהבעלים
         פתח ידנית (opens) פטורה — היא פתיחה מפורשת מחוץ לשעות. */
      const dow = u.parseKey(data.date).getDay();
      const sched = cur.schedule && cur.schedule[dow];
      const isOpened = (cur.opens || []).includes(data.date + "|" + data.start);
      if (sched && sched.active && !isOpened) {
        const openMin = u.toMin(sched.open), closeMin = u.toMin(sched.close);
        if (startMin >= openMin && startMin < closeMin && endMin > closeMin) {
          return { ok: false, reason: "התור חורג משעת הסגירה — בחרו מועד מוקדם יותר" };
        }
      }
    }

    // כמה פעמים הלקוח הזה כבר לא הגיע בעבר (לפי מזהה או טלפון) — כדי להתריע למנהל
    const priorNoShow = cur.bookings.filter((b) =>
      b.status === "noshow" &&
      (b.userId === data.userId || (dphone && b.phone && u.normalizePhone(b.phone) === dphone))
    ).length;

    // זיהוי פעילות חשודה (ספאם) — לא חוסם, רק מסמן לספר
    const spam = detectSpam(cur, data, dphone);

    const booking = {
      id: u.uid(),
      serviceId: svc.id, serviceName: svc.name, price: svc.price, durationMin: svc.durationMin,
      date: data.date, start: data.start, end: u.toHHMM(endMin),
      userId: data.userId,
      staff: data.staff || "",
      priorNoShow: priorNoShow,
      status: "booked", createdAt: Date.now(),
    };
    // חשוב: לא לשים undefined בשום שדה — טרנזקציית RTDB דוחה נתונים עם undefined
    // ("Data returned contains undefined ...") וההזמנה נכשלת. לכן spam נוסף רק אם יש.
    if (spam) booking.spam = spam;
    // פרטי הלקוח: במספרה מאובטחת הם נשמרים בצומת הפרטי (priv) ולא בצומת הציבורי,
    // כדי שלא ייחשפו לכל מי שקורא את המספרה. אחרת — כמו קודם, בתוך התור.
    let priv = null;
    if (piiPrivate(cur)) {
      priv = { name: data.userName || "", phone: data.phone || "", email: data.email || "" };
    } else {
      booking.userName = data.userName || "";
      booking.phone = data.phone || "";
      booking.email = data.email || "";
    }
    cur.bookings.push(booking);
    return { ok: true, booking, priv };
  }

  /* מרוץ מול פסק-זמן — כדי שקריאה לשרת שנתקעת (חיבור סלולרי חלש) לא תשאיר את
     כפתור "קובע תור…" תקוע לנצח. אחרי הזמן הזה מחזירים כשל, והלקוח יכול לנסות שוב. */
  function withTimeout(promise, ms) {
    return Promise.race([
      promise,
      new Promise((_, rej) => setTimeout(() => rej(new Error("__timeout__")), ms)),
    ]);
  }
  /* הודעת שגיאה ידידותית ללקוח לפי סוג הכשל (וגם רמז טכני לאבחון). */
  function bookingErrText(e) {
    const msg = String((e && (e.code || e.message)) || "");
    if (msg === "__timeout__") return "החיבור איטי מדי והתור לא נקבע — בדקו את האינטרנט ונסו שוב.";
    if (/permission|denied/i.test(msg)) return "אין כרגע הרשאה לקבוע תור. רעננו את הדף ונסו שוב. (permission-denied)";
    if (/network|unavailable|offline|timeout/i.test(msg)) return "אין חיבור יציב לשרת. בדקו את האינטרנט ונסו שוב.";
    return "לא הצלחנו לקבוע את התור — נסו שוב. (" + (msg || "שגיאה") + ")";
  }

  // כתיבת פרטי הלקוח לצומת הפרטי (אחרי שהתור נשמר). best-effort: אם הכתיבה
  // נכשלת (נדיר) — התור עצמו כבר נקבע; הבעלים פשוט לא יראה שם עד לתיקון.
  async function savePriv(booking, priv) {
    if (!priv || !booking || !backend.savePrivate) return;
    try { await backend.savePrivate(booking.id, priv); } catch (e) { /* התור כבר נקבע */ }
  }

  async function createBooking(data) {
    if (backend.transactBooking) {
      // חשוב: לא לתת לשגיאת רשת/הרשאה "לזרוק" החוצה — אחרת כפתור ההזמנה נתקע.
      // תמיד מחזירים אובייקט תוצאה ({ok:false, reason}) שהמסך יודע לטפל בו.
      let res;
      try {
        res = await withTimeout(backend.transactBooking((cur) => buildBooking(cur, data)), 22000);
      } catch (e) {
        console.warn("[UG] createBooking נכשל:", (e && (e.code || e.message)) || e);
        return { ok: false, reason: bookingErrText(e) };
      }
      if (res && res.ok) await savePriv(res.booking, res.priv);   // פרטי הלקוח לצומת הפרטי
      // Firestore יעדכן דרך onSnapshot; נטען מיידית ליתר ביטחון (לא חוסם — התור כבר נקבע)
      try { await withTimeout(reloadFromRemote(), 8000); } catch (e) { /* רענון בלבד */ }
      return res;
    }
    // מקומי: קרא מחדש את המצב העדכני לפני כתיבה כדי לצמצם התנגשויות
    const latest = backend.read();
    if (latest) state = latest;
    const res = buildBooking(state, data);
    if (res.ok) {
      try { await persist(); } catch (e) { return { ok: false, reason: bookingErrText(e) }; }
      await savePriv(res.booking, res.priv);
    }
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
    const lead = u.dateTime(dateKey, start).getTime() - Date.now();
    if (lead <= 0) return false;
    // אחרי "סגירת ההרשמה" המשבצת כבר לא נחשבת פנויה — אין טעם להתריע שהתפנתה
    const cutoffMin = Math.max(0, Number((cur.shop && cur.shop.hideFreeBeforeMin) || 0));
    if (cutoffMin && lead < cutoffMin * 60000) return false;
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
          id: u.uid(), userId: w.userId, userName: w.userName || "",
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
        processFreed(state, b);   // מעדכן גם waitlist ו-alerts
      }
      // לקוח מבטל את התור שלו — כותב רק את הצמתים שהשתנו (מותר גם במספרה נעולה)
      await persistChildren(["bookings", "waitlist", "alerts"]);
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
      const entry = { id: u.uid(), date: data.date, start: data.start, userId: data.userId, createdAt: Date.now() };
      // במספרה מאובטחת לא שומרים שם/טלפון ברשימת ההמתנה — הם אינם מוצגים בשום מקום
      // (ההודעה על התפנות מגיעה לפי userId), ולכן אין סיבה לחשוף אותם בצומת הציבורי.
      if (!extraPrivate()) { entry.userName = data.userName || ""; entry.phone = data.phone || ""; }
      state.waitlist.push(entry);
      await persistChildren(["waitlist"]);
    }
  }
  async function leaveWaitlist(id) {
    refreshLocal();
    state.waitlist = (state.waitlist || []).filter((w) => w.id !== id);
    await persistChildren(["waitlist"]);
  }
  async function consumeAlert(ids) {
    refreshLocal();
    const set = new Set(Array.isArray(ids) ? ids : [ids]);
    state.alerts = (state.alerts || []).filter((a) => !set.has(a.id));
    await persistChildren(["alerts"]);
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
    await persistChildren(["reviews"]);
  }

  // מחיקת רשומת תור לצמיתות (לניקוי הדוח)
  async function deleteBooking(id) {
    refreshLocal();
    state.bookings = state.bookings.filter((b) => b.id !== id);
    await persist();
    // ניקוי פרטי הלקוח הפרטיים של אותו תור (אם היו) — best-effort
    if (backend.removePrivate) { try { await backend.removePrivate(id); } catch (e) {} }
  }

  /* מחיקת עקבות של לקוח לפי בקשתו — ביקורות, המתנות והתראות ממתינות.
     תורים אינם נמחקים כאן: עתידיים מבוטלים ע״י הקורא, ועבר נשאר כרישום עסקי. */
  async function purgeClient(userId) {
    if (!userId) return;
    refreshLocal();
    state.reviews = (state.reviews || []).filter((r) => r.userId !== userId);
    state.waitlist = (state.waitlist || []).filter((w) => w.userId !== userId);
    state.alerts = (state.alerts || []).filter((a) => a.userId !== userId);
    await persist();
    // אסימוני הפוש של המכשיר — כדי שלא יקבל יותר התראות
    try {
      await connect();
      if (_conn && _conn.kind === "rtdb") await _conn.db.ref("pushTokens/" + userId).remove();
    } catch (e) {}
  }

  return {
    init, subscribe, get,
    setDay, saveShop, upsertService, removeService, upsertProduct, removeProduct,
    addContacts, removeContact, addBroadcast, addClosedDates, removeClosedDate,
    setSlotOpen, blockClient, unblockClient,
    createBooking, setBookingStatus, deleteBooking,
    joinWaitlist, leaveWaitlist, consumeAlert, addReview, savePushToken,
    subscribeGallery, getGallery, addPhoto, removePhoto,
    subscribeMedia, getMedia, setShopMedia, getBookingPriv, getContacts, subscribeContacts,
    createShop, shopExists, peekShop, passcodeTaken, registerPasscodeIfFree, setOwnerPassHash,
    getSub, markPaymentPending, adminListShops, adminSetPaid,
    exportData, importData, deleteShop, purgeClient, onWriteError,
    get mode() { return backend ? backend.mode : "local"; },
    get shopId() { return shopId; },
    get notFound() { return notFound; },
  };
})();
