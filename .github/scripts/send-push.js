/* =========================================================================
   שולח התראות פוש (FCM) — רץ ב-GitHub Actions כל כמה דקות.
   קורא את המספרות מ-Realtime Database, מזהה alerts/bookings/ביטולים חדשים
   שעוד לא טופלו, ושולח הודעת פוש לטלפונים הרשומים. בלי Functions ובלי Blaze.
   דורש secret בשם FIREBASE_SERVICE_ACCOUNT (מפתח service account של barbertor, JSON).
   =========================================================================*/
/* אזור זמן — חייב להיקבע לפני כל שימוש ב-Date.
   התאריכים והשעות במסד נשמרים כזמן מקומי של המספרה ("2026-08-17", "13:00")
   בלי אזור זמן. הראנר של GitHub רץ ב-UTC, ולכן בלי השורה הזו כל חישוב זמן
   יוצא מוסט בשעתיים-שלוש (לפי שעון קיץ/חורף), והתזכורות היו נשלחות אחרי
   שהתור כבר עבר. */
process.env.TZ = "Asia/Jerusalem";

const admin = require("firebase-admin");
const crypto = require("crypto");

/* ניקוי חד-פעמי של סיסמאות ניהול גלויות: ממיר shop.ownerPass (טקסט גלוי) ל-hash
   מלוחלח (shop.ownerPassHash) ומוחק את הגלויה. הלקוח מחשב את אותו hash בדיוק
   (ownerHash: sha256("btpass1|"+כתובת+"|"+סיסמה)), כך שההתחברות ממשיכה לעבוד.
   רץ על כל המספרות בכל הפעלה; אחרי שכולן נוקו — no-op. best-effort לחלוטין. */
function ownerHashNode(handle, pass) {
  return crypto.createHash("sha256").update("btpass1|" + String(handle) + "|" + String(pass)).digest("hex");
}
async function migratePasswords(db, shopsVal) {
  let migrated = 0;
  for (const sid of Object.keys(shopsVal || {})) {
    const shop = shopsVal[sid];
    const pass = shop && shop.shop && shop.shop.ownerPass;
    if (pass == null || pass === "") continue;   // אין סיסמה גלויה — כבר נקי
    try {
      await db.ref("shops/" + sid + "/shop/ownerPassHash").set(ownerHashNode(sid, pass));
      await db.ref("shops/" + sid + "/shop/ownerPass").remove();
      migrated++;
    } catch (e) { /* best-effort — לא חוסם את הקרון */ }
  }
  return migrated;
}

/* ניקוי חד-פעמי של תמונות (רקע/לוגו): מעביר shop.cover/shop.logo מתוך רשומת
   המספרה לצומת הנפרד shopMedia/<id> ומסיר אותן מהמספרה — כדי שרשומת המספרה
   תישאר קטנה ולא תחסום את טעינת האפליקציה. הלקוח (v132+) קורא מ-shopMedia עם
   נפילה-לאחור, לכן הפעולה שקופה. אידמפוטנטי ו-best-effort לחלוטין. */
async function migrateMedia(db, shopsVal) {
  let migrated = 0;
  for (const sid of Object.keys(shopsVal || {})) {
    const s = shopsVal[sid] && shopsVal[sid].shop;
    if (!s) continue;
    const patch = {};
    if (typeof s.cover === "string" && s.cover) patch.cover = s.cover;
    if (typeof s.logo === "string" && s.logo) patch.logo = s.logo;
    if (!Object.keys(patch).length) continue;   // אין תמונות בתוך המספרה — כבר נקי
    try {
      await db.ref("shopMedia/" + sid).update(patch);   // קודם כותבים למדיה
      const rm = {};
      if (patch.cover) rm["shops/" + sid + "/shop/cover"] = null;
      if (patch.logo) rm["shops/" + sid + "/shop/logo"] = null;
      await db.ref().update(rm);                         // ואז מסירים מהמספרה
      migrated++;
    } catch (e) { /* best-effort — לא חוסם את הקרון */ }
  }
  return migrated;
}

/* העברת פרטי הלקוח (שם/טלפון/מייל) של תורים קיימים אל הצומת הפרטי (private/<id>/bk)
   והסרתם מהצומת הציבורי — רק במספרות מאובטחות בחשבון (ownerUid), בהתאמה ל-piiPrivate
   שבלקוח. כך גם התורים הישנים (שנוצרו לפני ההסתרה) מפסיקים לחשוף פרטים לכל מי שקורא
   את המספרה. פועל פר-שדה (מסיר רק userName/phone/email של אותו תור לפי המפתח שלו),
   כדי לא לדרוס מערך תורים שלם ולא להתנגש בכתיבת לקוח באותו רגע. אידמפוטנטי ו-best-effort. */
async function migrateBookingPii(db, shopsVal) {
  let moved = 0;
  for (const sid of Object.keys(shopsVal || {})) {
    const shop = shopsVal[sid];
    if (!shop || !shop.shop || !shop.shop.ownerUid) continue;   // רק מספרות מאובטחות
    const bk = shop.bookings;
    if (!bk || typeof bk !== "object") continue;
    // Object.keys עובד גם על מערך (מפתחות "0","1"...) וגם על אובייקט — המפתח משמש בנתיב.
    for (const k of Object.keys(bk)) {
      const b = bk[k];
      if (!b || !b.id) continue;
      const name = typeof b.userName === "string" ? b.userName : "";
      const phone = typeof b.phone === "string" ? b.phone : "";
      const email = typeof b.email === "string" ? b.email : "";
      if (!name && !phone && !email) continue;   // אין פרטים בצומת הציבורי — כבר נקי
      try {
        // קודם כותבים לכספת (רק אם עוד אין שם — לא לדרוס), ואז מסירים מהצומת הציבורי
        const privRef = db.ref("private/" + sid + "/bk/" + b.id);
        const snap = await privRef.once("value");
        if (!snap.exists()) await privRef.set({ name: name, phone: phone, email: email });
        await db.ref().update({
          ["shops/" + sid + "/bookings/" + k + "/userName"]: null,
          ["shops/" + sid + "/bookings/" + k + "/phone"]: null,
          ["shops/" + sid + "/bookings/" + k + "/email"]: null,
        });
        moved++;
      } catch (e) { /* best-effort — לא חוסם את הקרון */ }
    }
  }
  return moved;
}

/* שלב 4 — הסתרת ספר הלקוחות ורשימת ההמתנה. תואם ל-extraPrivate בלקוח: כל מספרה
   מאובטחת בחשבון (ownerUid). מעביר את ספר הלקוחות לצומת הפרטי (עם איחוד ומניעת
   כפילות), ומסיר שם/טלפון מרשומות רשימת ההמתנה וההתראות בצומת הציבורי (הם אינם
   מוצגים בשום מקום). אידמפוטנטי ו-best-effort. */
const asArr = (x) => Array.isArray(x) ? x : (x && typeof x === "object" ? Object.keys(x).map((k) => x[k]) : []);
async function migrateExtraPii(db, shopsVal) {
  let moved = 0;
  for (const sid of Object.keys(shopsVal || {})) {
    const shop = shopsVal[sid];
    if (!shop || !shop.shop || !shop.shop.ownerUid) continue;   // רק מספרות מאובטחות
    // ספר הלקוחות → כספת (איחוד עם הקיים, מניעת כפילות), ואז הסרה מהצומת הציבורי
    const pub = asArr(shop.contacts).filter((c) => c && (c.name || c.phone));
    if (pub.length) {
      try {
        const cRef = db.ref("private/" + sid + "/contacts");
        const priv = asArr((await cRef.once("value")).val());
        const key = (c) => (String((c && c.phone) || "").replace(/\D/g, "")) + "|" + String((c && c.name) || "").trim();
        const seen = new Set(priv.map(key));
        const merged = priv.slice();
        for (const c of pub) { const k = key(c); if (seen.has(k)) continue; seen.add(k); merged.push(c); }
        await cRef.set(merged);
        await db.ref("shops/" + sid + "/contacts").remove();
        moved++;
      } catch (e) { /* best-effort */ }
    }
    // רשימת המתנה + התראות → הסרת שם/טלפון (לא מוצגים; ההודעה לפי userId)
    for (const node of ["waitlist", "alerts"]) {
      const arr = shop[node];
      if (!arr || typeof arr !== "object") continue;
      for (const k of Object.keys(arr)) {
        const w = arr[k];
        if (!w) continue;
        const hasName = typeof w.userName === "string" && w.userName !== "";
        const hasPhone = typeof w.phone === "string" && w.phone !== "";
        if (!hasName && !hasPhone) continue;
        try {
          const patch = {};
          if (hasName) patch["shops/" + sid + "/" + node + "/" + k + "/userName"] = null;
          if (hasPhone) patch["shops/" + sid + "/" + node + "/" + k + "/phone"] = null;
          await db.ref().update(patch);
          moved++;
        } catch (e) { /* best-effort */ }
      }
    }
  }
  return moved;
}

/* באג מחיקת התורים: מפעיל "תורים מחמירים" למספרה — ממפתח מחדש את התורים לפי מזהה
   (כדי שפעולות פר-תור יעבדו) ואז מסמן shop.strictBookings=true. מרגע זה חוקי
   האבטחה מונעים מחיקה/שכתוב המוני של התורים. כרגע "try" בלבד (כמו strictBookings
   בלקוח); יורחב בהמשך. פעם אחת לכל מספרה, best-effort. */
async function migrateStrictBookings(db, shopsVal) {
  let done = 0;
  for (const sid of Object.keys(shopsVal || {})) {
    if (sid !== "try") continue;
    const shop = shopsVal[sid];
    if (!shop || !shop.shop || !shop.shop.ownerUid) continue;   // דורש מספרה מאובטחת
    if (shop.shop.strictBookings) continue;                     // כבר מופעל
    try {
      const fresh = (await db.ref("shops/" + sid + "/bookings").once("value")).val();
      const map = {};
      asArr(fresh).forEach((b) => { if (b && b.id) map[b.id] = b; });   // מפתח = מזהה התור
      await db.ref("shops/" + sid + "/bookings").set(map);
      await db.ref("shops/" + sid + "/shop/strictBookings").set(true);
      done++;
    } catch (e) { /* best-effort */ }
  }
  return done;
}

/* הגנה על תקופת הניסיון: מקבע את תחילת הניסיון בצומת subs/<id> (שהבעלים אינו יכול
   לכתוב אליו — רק המנהל/הקרון), כדי שלא ניתן למתוח את הניסיון ע״י עריכת createdAt.
   - מספרה עם createdAt תקין → trialStart = min(createdAt, now) (חוסם עתיד-תיארוך).
   - מספרה ותיקה (בלי createdAt) שקיימת כבר בהרצת-הבסיס הראשונה → grandfathered=true.
   - מספרה חדשה (אחרי הבסיס) בלי createdAt → trialStart = now (ניסיון רגיל, לא פטור).
   נקבע פעם אחת לכל מספרה; אם כבר נקבע — מדלגים. אידמפוטנטי ו-best-effort. */
async function stampTrials(db, shopsVal, now) {
  let stamped = 0;
  const subsVal = (await db.ref("subs").once("value")).val() || {};
  const backfillDone = !!(await db.ref("system/trialBackfillV1").once("value")).val();
  for (const sid of Object.keys(shopsVal || {})) {
    const shop = shopsVal[sid];
    if (!shop || shop.type === "photo") continue;
    const sub = subsVal[sid] || {};
    if (sub.trialStart != null || sub.grandfathered) continue;   // כבר נקבע — נעול
    const createdAt = Number(shop.shop && shop.shop.createdAt);
    try {
      if (createdAt > 0) {
        await db.ref("subs/" + sid + "/trialStart").set(Math.min(createdAt, now));
      } else if (!backfillDone) {
        await db.ref("subs/" + sid + "/grandfathered").set(true);   // מספרה ותיקה אמיתית
      } else {
        await db.ref("subs/" + sid + "/trialStart").set(now);       // חדשה בלי createdAt → ניסיון
      }
      stamped++;
    } catch (e) { /* best-effort */ }
  }
  if (!backfillDone) { try { await db.ref("system/trialBackfillV1").set(now); } catch (e) {} }
  return stamped;
}

const DB_URL = process.env.DATABASE_URL ||
  "https://barbertor-default-rtdb.europe-west1.firebasedatabase.app";

const DOW = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"];
function relDay(dateKey) {
  const [y, m, d] = dateKey.split("-").map(Number);
  const target = new Date(y, m - 1, d);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const diff = Math.round((target - today) / 86400000);
  if (diff === 0) return "היום";
  if (diff === 1) return "מחר";
  return "יום " + DOW[target.getDay()];
}
function apptTs(date, start) {
  const [y, m, d] = date.split("-").map(Number);
  const [hh, mm] = start.split(":").map(Number);
  return new Date(y, m - 1, d, hh, mm).getTime();
}

(async () => {
  const raw = process.env.FIREBASE_SERVICE_ACCOUNT;
  if (!raw) { console.error("חסר secret: FIREBASE_SERVICE_ACCOUNT"); process.exit(1); }
  let creds;
  try { creds = JSON.parse(raw); }
  catch (e) { console.error("FIREBASE_SERVICE_ACCOUNT אינו JSON תקין"); process.exit(1); }

  admin.initializeApp({ credential: admin.credential.cert(creds), databaseURL: DB_URL });
  const db = admin.database();
  const messaging = admin.messaging();

  async function tokensFor(uid) {
    const snap = await db.ref("pushTokens/" + uid).once("value");
    const d = snap.val();
    return d && Array.isArray(d.tokens) ? d.tokens : [];
  }
  async function sendToUid(uid, title, body, tag) {
    const tokens = await tokensFor(uid);
    if (!tokens.length) return 0;
    const res = await messaging.sendEachForMulticast({
      tokens,
      data: { title: title, body: body, tag: tag || "" },
      android: { priority: "high" },
      apns: { headers: { "apns-priority": "10" } },
    });
    const bad = [];
    res.responses.forEach((r, i) => {
      if (!r.success) {
        const c = r.error && r.error.code;
        if (c === "messaging/registration-token-not-registered" ||
            c === "messaging/invalid-argument" ||
            c === "messaging/invalid-registration-token") bad.push(tokens[i]);
      }
    });
    if (bad.length) {
      const remaining = tokens.filter((t) => bad.indexOf(t) === -1);
      await db.ref("pushTokens/" + uid + "/tokens").set(remaining);
    }
    return res.successCount;
  }

  // מצב "כבר טופל" לכל המספרות בצומת אחד
  const stateRef = db.ref("system/pushState");
  const stateVal = (await stateRef.once("value")).val();
  const firstRun = !stateVal;
  const perShop = (stateVal && stateVal.shops) || {};

  const now = Date.now();
  const shopsVal = (await db.ref("shops").once("value")).val() || {};
  const shopIds = Object.keys(shopsVal);
  // פרטי לקוח של תורים במספרות מאובטחות נשמרים בצומת פרטי (private/<id>/bk).
  // הקרון (service account) קורא הכול, ומשתמש בשם משם להתראות. מספרות שאינן
  // מאובטחות אינן מופיעות כאן — עבורן נופלים-לאחור ל-b.userName שבתוך התור.
  const privVal = (await db.ref("private").once("value")).val() || {};
  const bkNameOf = (sid, b) => {
    const p = privVal[sid] && privVal[sid].bk && privVal[sid].bk[b.id];
    return (p && p.name) || b.userName || "לקוח";
  };
  let sent = 0, totalNewA = 0, totalNewB = 0;

  for (const sid of shopIds) {
    const shop = shopsVal[sid] || {};
    if (shop.type === "photo") continue;   // דלג על רשומות ישנות
    const shopName = (shop.shop && shop.shop.name) || "המספרה";
    const alerts = Array.isArray(shop.alerts) ? shop.alerts : [];
    const bookings = Array.isArray(shop.bookings) ? shop.bookings : [];
    const broadcasts = Array.isArray(shop.broadcasts) ? shop.broadcasts : [];

    const st = perShop[sid] || { alertIds: [], bookingIds: [], cancelIds: [], bcIds: [], remIds: [] };
    const doneAlerts = new Set(st.alertIds || []);
    const doneBookings = new Set(st.bookingIds || []);
    const doneCancels = new Set(st.cancelIds || []);    // ביטול ע״י המנהל → התראה ללקוח
    // ביטול ע״י הלקוח → התראה לספר (סט נפרד!). במעבר מגרסה ישנה יורשים את הסט
    // המשולב הישן כדי לא להציף פושים על ביטולים שכבר טופלו.
    const doneCcancels = new Set(st.ccancelIds || st.cancelIds || []);
    const doneBc = new Set(st.bcIds || []);
    const doneRem = new Set(st.remIds || []);   // תזכורות שכבר נשלחו
    const doneDayRem = new Set(st.dayRemIds || []); // תזכורות "התור מחר" שכבר נשלחו

    const newAlerts = alerts.filter((a) => a && a.id && !doneAlerts.has(a.id) && apptTs(a.date, a.start) > now);
    const newBookings = bookings.filter((b) =>
      b && b.id && !doneBookings.has(b.id) && b.status !== "cancelled" && apptTs(b.date, b.start) > now);
    // ביטולים חדשים ע״י המנהל — התראה ללקוח שהתור שלו בוטל
    const newCancels = bookings.filter((b) =>
      b && b.id && b.userId && b.status === "cancelled" && b.cancelledBy === "owner" &&
      !doneCancels.has(b.id) && apptTs(b.date, b.start) > now);
    // ביטולים חדשים ע״י הלקוח — התראה לספר שהתפנתה משבצת (סט dedup נפרד משל המנהל)
    const newClientCancels = bookings.filter((b) =>
      b && b.id && b.status === "cancelled" && b.cancelledBy === "client" &&
      !doneCcancels.has(b.id) && apptTs(b.date, b.start) > now);
    // הודעות קבוצתיות חדשות שהמנהל שלח ללקוחות
    const newBc = broadcasts.filter((b) => b && b.id && b.text && !doneBc.has(b.id));
    // תזכורות לפני התור — נשלחות כשנותר פחות מ-reminderMinutes עד המועד
    const reminderMin = Number((shop.shop && shop.shop.reminderMinutes) || 60);
    const dueReminders = bookings.filter((b) => {
      if (!b || !b.id || !b.userId || b.status === "cancelled" || doneRem.has(b.id)) return false;
      const lead = apptTs(b.date, b.start) - now;
      return lead > 0 && lead <= reminderMin * 60000;
    });
    /* תזכורת "התור מחר" — נשלחת כשנותרו בין 18 ל-26 שעות עד התור.
       חלון בשעות (ולא "תאריך מחר") כדי שלא יהיה תלוי באזור הזמן של הראנר,
       שרץ ב-UTC בעוד המספרות בישראל. ה-dedup מבטיח שליחה אחת בלבד. */
    const dayBeforeOn = (shop.shop && shop.shop.remindDayBefore) !== false;
    const dueDayReminders = dayBeforeOn ? bookings.filter((b) => {
      if (!b || !b.id || !b.userId || b.status === "cancelled" || doneDayRem.has(b.id)) return false;
      const lead = apptTs(b.date, b.start) - now;
      return lead > 18 * 3600000 && lead <= 26 * 3600000;
    }) : [];

    if (!firstRun) {
      for (const a of newAlerts) {
        sent += await sendToUid(a.userId, "🎉 התפנה תור!",
          `${relDay(a.date)} בשעה ${a.start} — מהרו להזמין לפני שייתפס · ${shopName}`, "freed-" + a.id);
      }
      for (const b of newBookings) {
        const warn = b.priorNoShow ? "\n⚠️ הלקוח לא הגיע בפעם הקודמת" : "";
        const spamWarn = b.spam ? "\n🛡️ פעילות חריגה — " +
          (b.spam.reason === "multi" ? b.spam.count + " תורים פעילים מאותו לקוח" :
           b.spam.reason === "burst" ? b.spam.count + " הזמנות ברצף קצר" :
           b.spam.count + " תורים הוזמנו בזמן קצר") : "";
        sent += await sendToUid("owner_" + sid,
          b.spam ? "🛡️ תור חדש — פעילות חריגה" : "📅 תור חדש נקבע",
          `${bkNameOf(sid, b)} — ${b.serviceName}, ${relDay(b.date)} בשעה ${b.start}${warn}${spamWarn}`, "newbook-" + b.id);
      }
      for (const b of newCancels) {
        sent += await sendToUid(b.userId, "❌ התור שלך בוטל",
          `${shopName}\n${b.serviceName} · ${relDay(b.date)} בשעה ${b.start}`, "cancel-" + b.id);
      }
      for (const b of newClientCancels) {
        sent += await sendToUid("owner_" + sid, "❌ לקוח ביטל תור",
          `${bkNameOf(sid, b)} — ${b.serviceName}, ${relDay(b.date)} בשעה ${b.start}`, "ccancel-" + b.id);
        doneCcancels.add(b.id);
      }
      // תזכורת יום לפני — מגיעה כיממה מראש, כדי שיהיה זמן לבטל אם צריך
      for (const b of dueDayReminders) {
        sent += await sendToUid(b.userId, "📅 התור שלך מחר",
          `${b.serviceName} · ${relDay(b.date)} בשעה ${b.start}\n${shopName}`, "dayrem-" + b.id);
        doneDayRem.add(b.id);
      }
      // תזכורת לפני התור — מסמנים כנשלח רק אחרי השליחה בפועל
      for (const b of dueReminders) {
        const mins = Math.max(1, Math.round((apptTs(b.date, b.start) - now) / 60000));
        sent += await sendToUid(b.userId, "⏰ תזכורת לתור",
          `${b.serviceName} · ${relDay(b.date)} בשעה ${b.start} (בעוד ${mins} דק׳)\n${shopName}`, "rem-" + b.id);
        doneRem.add(b.id);
      }
      // הודעה קבוצתית — נשלחת לכל הלקוחות שהזמינו דרך האפליקציה
      if (newBc.length) {
        const clientIds = [...new Set(
          bookings.filter((b) => b && b.userId && b.userId.indexOf("owner") !== 0).map((b) => b.userId)
        )];
        for (const bc of newBc) {
          for (const uid of clientIds) {
            sent += await sendToUid(uid, shopName, bc.text, "bc-" + bc.id);
          }
        }
      }
    }

    // סמן הכל כטופל. שים לב: doneRem מתמלא רק מתזכורות שנשלחו בפועל —
    // אסור לסמן כאן את כל התורים, אחרת אף תזכורת עתידית לא תישלח.
    if (firstRun) {
      dueReminders.forEach((b) => doneRem.add(b.id));
      dueDayReminders.forEach((b) => doneDayRem.add(b.id));
    }
    alerts.forEach((a) => a && a.id && doneAlerts.add(a.id));
    bookings.forEach((b) => b && b.id && doneBookings.add(b.id));
    // ביטול מסומן כטופל בסט המתאים לפי מי שביטל — כך שני הכיוונים אינם משתיקים זה את זה
    bookings.forEach((b) => {
      if (!b || !b.id || b.status !== "cancelled") return;
      if (b.cancelledBy === "client") doneCcancels.add(b.id);
      else doneCancels.add(b.id);   // מנהל, או ביטול ישן ללא cancelledBy
    });
    broadcasts.forEach((b) => b && b.id && doneBc.add(b.id));
    perShop[sid] = {
      alertIds: [...doneAlerts].slice(-500),
      bookingIds: [...doneBookings].slice(-500),
      cancelIds: [...doneCancels].slice(-500),
      ccancelIds: [...doneCcancels].slice(-500),
      bcIds: [...doneBc].slice(-200),
      remIds: [...doneRem].slice(-500),
      dayRemIds: [...doneDayRem].slice(-500),
    };
    totalNewA += newAlerts.length; totalNewB += newBookings.length;
  }

  // ניקוי מצב יתום — מספרות שנמחקו. הלקוח אינו יכול לכתוב ל-system/ (כללי אבטחה),
  // לכן המחיקה של הרשומה כאן מתבצעת בריצה הבאה של הקרון.
  const live = new Set(shopIds);
  let pruned = 0;
  Object.keys(perShop).forEach((k) => { if (!live.has(k)) { delete perShop[k]; pruned++; } });

  await stateRef.set({ shops: perShop, updatedAt: now });
  if (pruned) console.log(`נוקו ${pruned} רשומות של מספרות שנמחקו.`);

  // ניקוי סיסמאות ניהול גלויות — מבודד לחלוטין: כישלון כאן לא פוגע בהתראות.
  try {
    const migrated = await migratePasswords(db, shopsVal);
    if (migrated) console.log(`ניקוי סיסמאות: ${migrated} מספרות עברו ל-hash (הוסרה סיסמה גלויה).`);
  } catch (e) { console.warn("ניקוי סיסמאות נכשל:", (e && e.message) || e); }

  // העברת תמונות (רקע/לוגו) לצומת נפרד — גם כן מבודד, לא פוגע בהתראות.
  try {
    const movedMedia = await migrateMedia(db, shopsVal);
    if (movedMedia) console.log(`העברת תמונות: ${movedMedia} מספרות — רקע/לוגו הועברו ל-shopMedia.`);
  } catch (e) { console.warn("העברת תמונות נכשלה:", (e && e.message) || e); }

  // העברת פרטי לקוח של תורים קיימים לכספת (private) — מבודד, לא פוגע בהתראות.
  try {
    const movedPii = await migrateBookingPii(db, shopsVal);
    if (movedPii) console.log(`הסתרת פרטי לקוח: ${movedPii} תורים הועברו לצומת הפרטי (הוסרו מהצומת הציבורי).`);
  } catch (e) { console.warn("הסתרת פרטי לקוח נכשלה:", (e && e.message) || e); }

  // שלב 4 — ספר לקוחות + רשימת המתנה (כרגע "try" בלבד). מבודד, לא פוגע בהתראות.
  try {
    const movedExtra = await migrateExtraPii(db, shopsVal);
    if (movedExtra) console.log(`הסתרת ספר לקוחות/המתנה: ${movedExtra} פריטים טופלו.`);
  } catch (e) { console.warn("הסתרת ספר לקוחות/המתנה נכשלה:", (e && e.message) || e); }

  // קיבוע תחילת הניסיון בצומת המוגן (subs) — הגנה מפני מתיחת הניסיון. מבודד.
  try {
    const stampedTrials = await stampTrials(db, shopsVal, now);
    if (stampedTrials) console.log(`קיבוע ניסיון: ${stampedTrials} מספרות סומנו (trialStart/grandfathered).`);
  } catch (e) { console.warn("קיבוע ניסיון נכשל:", (e && e.message) || e); }

  // הפעלת "תורים מחמירים" (כרגע "try" בלבד) — מניעת מחיקת תורים המונית. מבודד.
  try {
    const strict = await migrateStrictBookings(db, shopsVal);
    if (strict) console.log(`תורים מחמירים: הופעל ל-${strict} מספרות.`);
  } catch (e) { console.warn("הפעלת תורים מחמירים נכשלה:", (e && e.message) || e); }

  if (firstRun) console.log("ריצה ראשונה — סימון מצב קיים בלבד, ללא שליחה.");
  else console.log(`הושלם. מספרות=${shopIds.length}, alerts חדשים=${totalNewA}, bookings חדשים=${totalNewB}, פושים שנשלחו=${sent}`);
})().catch((e) => { console.error(e); process.exitCode = 1; })
  .finally(() => admin.app().delete());   // סוגר את חיבור ה-RTDB כדי שהתהליך יסתיים
