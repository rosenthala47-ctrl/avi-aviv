/* =========================================================================
   גיבוי אוטומטי — רץ ב-GitHub Actions פעם ביום.
   קורא את כל המספרות מ-Realtime Database וכותב קובצי JSON לתיקיית out/,
   שהוורקפלואו מעלה כ-artifact (נשמר 90 יום, פרטי למאגר).

   למה artifact ולא commit למאגר: הגיבוי מכיל שמות וטלפונים של לקוחות.
   הכנסת מידע כזה להיסטוריית git תשאיר אותו שם לתמיד ותחשוף אותו לכל מי
   שיש לו גישה למאגר. artifact פרטי, מתיישן לבד, ואפשר להוריד בלחיצה.

   דורש secret בשם FIREBASE_SERVICE_ACCOUNT (אותו אחד של send-push).
   =========================================================================*/
const admin = require("firebase-admin");
const fs = require("fs");
const path = require("path");

const DB_URL = process.env.DATABASE_URL ||
  "https://barbertor-default-rtdb.europe-west1.firebasedatabase.app";
const OUT = process.env.OUT_DIR || "out";

function mb(bytes) { return (bytes / 1048576).toFixed(2) + " MB"; }

function write(name, obj) {
  const file = path.join(OUT, name);
  const json = JSON.stringify(obj, null, 0);
  fs.writeFileSync(file, json, "utf8");
  return Buffer.byteLength(json, "utf8");
}

(async () => {
  const raw = process.env.FIREBASE_SERVICE_ACCOUNT;
  if (!raw) { console.error("חסר secret: FIREBASE_SERVICE_ACCOUNT"); process.exit(1); }
  let creds;
  try { creds = JSON.parse(raw); }
  catch (e) { console.error("FIREBASE_SERVICE_ACCOUNT אינו JSON תקין"); process.exit(1); }

  admin.initializeApp({ credential: admin.credential.cert(creds), databaseURL: DB_URL });
  const db = admin.database();

  fs.mkdirSync(OUT, { recursive: true });
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");

  // הנתונים העסקיים — תורים, לקוחות, שירותים, שעות, הגדרות
  const [shopsSnap, indexSnap, subsSnap] = await Promise.all([
    db.ref("shops").once("value"),
    db.ref("shopIndex").once("value"),
    db.ref("subs").once("value"),
  ]);
  const shops = shopsSnap.val() || {};
  const shopIds = Object.keys(shops);

  /* אם אין אף מספרה — משהו שבור (הרשאות, כתובת מסד שגויה). עדיף להיכשל
     ברעש מאשר לשמור גיבוי ריק שייראה תקין עד שיצטרכו אותו באמת. */
  if (!shopIds.length) {
    console.error("שגיאה: לא נמצאו מספרות. הגיבוי מבוטל כדי לא לשמור קובץ ריק.");
    process.exit(1);
  }

  let bookings = 0, contacts = 0;
  shopIds.forEach((id) => {
    const s = shops[id] || {};
    bookings += Array.isArray(s.bookings) ? s.bookings.length : 0;
    contacts += Array.isArray(s.contacts) ? s.contacts.length : 0;
  });

  const coreSize = write("barbertor-" + stamp + ".json", {
    format: "barbertor-full-backup",
    version: 1,
    createdAt: Date.now(),
    counts: { shops: shopIds.length, bookings: bookings, contacts: contacts },
    shops: shops,
    shopIndex: indexSnap.val() || {},
    subs: subsSnap.val() || {},
  });

  /* הגלריה בנפרד: התמונות שמורות כ-data URL ומנפחות מאוד את הקובץ.
     קובץ נפרד מאפשר להוריד רק את מה שצריך, ולוותר עליו אם הוא גדול מדי. */
  let gallerySize = 0, photos = 0;
  try {
    const g = (await db.ref("gallery").once("value")).val() || {};
    Object.keys(g).forEach((sid) => {
      const list = g[sid];
      photos += list && typeof list === "object" ? Object.keys(list).length : 0;
    });
    gallerySize = write("barbertor-gallery-" + stamp + ".json", {
      format: "barbertor-gallery-backup", version: 1, createdAt: Date.now(), gallery: g,
    });
  } catch (e) {
    console.warn("אזהרה: הגלריה לא גובתה —", (e && e.message) || e);
  }

  console.log("גיבוי הושלם:");
  console.log("  מספרות : " + shopIds.length);
  console.log("  תורים  : " + bookings);
  console.log("  לקוחות : " + contacts);
  console.log("  תמונות : " + photos);
  console.log("  נתונים : " + mb(coreSize));
  console.log("  גלריה  : " + mb(gallerySize));
})().catch((e) => { console.error(e); process.exitCode = 1; })
  .finally(() => admin.app().delete());
