/* =========================================================================
   שולח התראות פוש (FCM) — רץ ב-GitHub Actions כל כמה דקות.
   קורא את המספרות מ-Realtime Database, מזהה alerts/bookings/ביטולים חדשים
   שעוד לא טופלו, ושולח הודעת פוש לטלפונים הרשומים. בלי Functions ובלי Blaze.
   דורש secret בשם FIREBASE_SERVICE_ACCOUNT (מפתח service account של barbertor, JSON).
   =========================================================================*/
const admin = require("firebase-admin");

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
  let sent = 0, totalNewA = 0, totalNewB = 0;

  for (const sid of shopIds) {
    const shop = shopsVal[sid] || {};
    if (shop.type === "photo") continue;   // דלג על רשומות ישנות
    const shopName = (shop.shop && shop.shop.name) || "המספרה";
    const alerts = Array.isArray(shop.alerts) ? shop.alerts : [];
    const bookings = Array.isArray(shop.bookings) ? shop.bookings : [];

    const st = perShop[sid] || { alertIds: [], bookingIds: [], cancelIds: [] };
    const doneAlerts = new Set(st.alertIds || []);
    const doneBookings = new Set(st.bookingIds || []);
    const doneCancels = new Set(st.cancelIds || []);

    const newAlerts = alerts.filter((a) => a && a.id && !doneAlerts.has(a.id) && apptTs(a.date, a.start) > now);
    const newBookings = bookings.filter((b) =>
      b && b.id && !doneBookings.has(b.id) && b.status !== "cancelled" && apptTs(b.date, b.start) > now);
    // ביטולים חדשים ע״י המנהל — התראה ללקוח שהתור שלו בוטל
    const newCancels = bookings.filter((b) =>
      b && b.id && b.userId && b.status === "cancelled" && b.cancelledBy === "owner" &&
      !doneCancels.has(b.id) && apptTs(b.date, b.start) > now);

    if (!firstRun) {
      for (const a of newAlerts) {
        sent += await sendToUid(a.userId, "🎉 התפנה תור!",
          `${relDay(a.date)} בשעה ${a.start} — מהרו להזמין לפני שייתפס · ${shopName}`, "freed-" + a.id);
      }
      for (const b of newBookings) {
        const warn = b.priorNoShow ? "\n⚠️ הלקוח לא הגיע בפעם הקודמת" : "";
        sent += await sendToUid("owner_" + sid, "📅 תור חדש נקבע",
          `${b.userName} — ${b.serviceName}, ${relDay(b.date)} בשעה ${b.start}${warn}`, "newbook-" + b.id);
      }
      for (const b of newCancels) {
        sent += await sendToUid(b.userId, "❌ התור שלך בוטל",
          `${shopName}\n${b.serviceName} · ${relDay(b.date)} בשעה ${b.start}`, "cancel-" + b.id);
      }
    }

    // סמן הכל כטופל
    alerts.forEach((a) => a && a.id && doneAlerts.add(a.id));
    bookings.forEach((b) => b && b.id && doneBookings.add(b.id));
    bookings.forEach((b) => b && b.id && b.status === "cancelled" && doneCancels.add(b.id));
    perShop[sid] = {
      alertIds: [...doneAlerts].slice(-500),
      bookingIds: [...doneBookings].slice(-500),
      cancelIds: [...doneCancels].slice(-500),
    };
    totalNewA += newAlerts.length; totalNewB += newBookings.length;
  }

  await stateRef.set({ shops: perShop, updatedAt: now });
  if (firstRun) { console.log("ריצה ראשונה — סימון מצב קיים בלבד, ללא שליחה."); return; }
  console.log(`הושלם. מספרות=${shopIds.length}, alerts חדשים=${totalNewA}, bookings חדשים=${totalNewB}, פושים שנשלחו=${sent}`);
})().catch((e) => { console.error(e); process.exit(1); });
