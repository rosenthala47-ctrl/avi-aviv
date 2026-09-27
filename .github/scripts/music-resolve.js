/* =========================================================================
   מוזיקה בכיסא — הפיכת בקשה חופשית של לקוח ("עדן בן זקן") לנגן ספוטיפי אמיתי.
   רץ כשלב מבודד בתוך קרון ההתראות (send-push.js), אחרי שכל ההתראות כבר נשלחו.

   למה בשרת ולא באפליקציה: החיפוש בספוטיפי דורש מפתח סודי (Client Secret).
   באפליקציה כל אחד היה יכול לשלוף אותו, ולכן הוא יושב רק ב-GitHub Secrets
   (SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET). בלי המפתחות — השלב פשוט מדלג.

   מה הוא עושה: עובר על התורים הקרובים במספרות שהפעילו "אישי לכל לקוח",
   מוצא בקשות טקסט שעוד לא הומרו (music.query בלי music.url), מחפש בספוטיפי,
   ואם יש התאמה בטוחה — כותב לתור קישור + נגן מוטמע (אמן / שיר / פלייליסט).
   אין התאמה בטוחה → לא נוגעים (אצל הספר נשאר כפתור "חפש בספוטיפי").
   בנוסף: קישור מקוצר (spotify.link) מומר לקישור המלא, כדי שיהיה לו נגן.
   =========================================================================*/

const TOKEN_URL = "https://accounts.spotify.com/api/token";
const SEARCH_URL = "https://api.spotify.com/v1/search";
const RETRY_NOT_FOUND_MS = 24 * 3600000;   // בקשה שלא נמצאה — ננסה שוב רק אחרי יממה
const REQ_TIMEOUT_MS = 8000;               // בקשה שנתקעת לא תעכב את סיום הקרון
const STEP_BUDGET_MS = 25000;              // תקרת זמן לכל השלב — הריצות בתור אחת אחרי השנייה,
                                           // ושלב איטי היה מעכב את התזכורות של הריצה הבאה
const isTimeout = (e) => !!e && (e.name === "AbortError" || /abort|timeout|timed out/i.test(String(e.message || "")));

// נרמול לשם השוואה: אותיות קטנות, בלי ניקוד, בלי סימנים, רווחים בודדים
function norm(s) {
  return String(s || "").toLowerCase()
    .replace(/[֑-ׇ]/g, "")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim().replace(/\s+/g, " ");
}

// הסרת מילות מילוי שלקוחות נוטים לכתוב ("שירים של עדן בן זקן" → "עדן בן זקן")
const FILLERS = [
  "שירים של", "השירים של", "שירי", "פלייליסט של", "פלייליסט", "מוזיקה של", "מוזיקה",
  "להיטים של", "הלהיטים של", "הזמרת", "הזמר", "הלהקה", "להקת",
  "songs by", "songs of", "playlist of", "playlist", "music by", "music of", "the best of",
];
function cleanQuery(q) {
  let s = String(q || "").trim();
  let changed = true;
  while (changed) {
    changed = false;
    for (const f of FILLERS) {
      const re = new RegExp("^" + f.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s+", "i");
      if (re.test(s)) { s = s.replace(re, "").trim(); changed = true; }
    }
  }
  return s || String(q || "").trim();
}

// האם שני שמות "אותו דבר" אחרי נרמול (שוויון מלא)
function sameName(a, b) { const x = norm(a), y = norm(b); return !!x && x === y; }
// האם אחד מכיל את השני (לפחות 3 תווים — כדי ש"עד" לא יתאים לכל דבר)
function containsName(hay, needle) {
  const h = norm(hay), n = norm(needle);
  return n.length >= 3 && h.length >= 3 && (h === n || (" " + h + " ").includes(" " + n + " "));
}

/* בחירת התוצאה המתאימה מתוך תשובת החיפוש של ספוטיפי. מחזיר {type,id,name} או null.
   סדר עדיפויות — מהבטוח לפחות בטוח; אם שום כלל לא מתקיים, עדיף לא לנחש. */
function pickSpotifyMatch(query, json) {
  const q = cleanQuery(query);
  const A = ((json && json.artists && json.artists.items) || []).filter(Boolean);
  const T = ((json && json.tracks && json.tracks.items) || []).filter(Boolean);
  const P = ((json && json.playlists && json.playlists.items) || []).filter(Boolean);   // ספוטיפי מחזיר לפעמים null ברשימה
  const out = (type, it) => ({ type: type, id: it.id, name: it.name });

  // 1. אמן בשם זהה
  const a1 = A.find((a) => a.id && sameName(a.name, q));
  if (a1) return out("artist", a1);
  // 2. שיר בשם זהה ("מי אנחנו שניפול")
  const t1 = T.find((t) => t.id && sameName(t.name, q));
  if (t1) return out("track", t1);
  // 3. "זמר + שיר" — הבקשה מכילה גם את שם השיר וגם את שם הזמר שלו
  const t2 = T.find((t) => t.id && containsName(q, t.name) &&
    (t.artists || []).some((ar) => ar && containsName(q, ar.name)));
  if (t2) return out("track", t2);
  // 4. האמן הראשון מאושר ע״י השירים: רוב השירים המובילים הם שלו. תופס גם שם
  //    שנכתב בעברית כשהאמן רשום בספוטיפי באנגלית, וגם שגיאות כתיב.
  if (A[0] && A[0].id) {
    const topT = T.slice(0, 5);
    const byTop = topT.filter((t) => (t.artists || []).some((ar) => ar && ar.id === A[0].id)).length;
    if (byTop >= 2 || (topT.length === 1 && byTop === 1)) return out("artist", A[0]);
  }
  // 5. אמן שהשם שלו מוכל בבקשה או מכיל אותה ("בן זקן" → "עדן בן זקן")
  const a2 = A.find((a) => a.id && (containsName(q, a.name) || containsName(a.name, q)));
  if (a2) return out("artist", a2);
  // 6. פלייליסט שהשם שלו מכיל את הבקשה — מתאים לסגנונות ("רגאטון", "chill")
  const p1 = P.find((p) => p.id && containsName(p.name, q));
  if (p1) return out("playlist", p1);
  return null;
}

// fetch עם מגבלת זמן
async function fetchT(fetchImpl, url, opts) {
  const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = ctl ? setTimeout(() => ctl.abort(), REQ_TIMEOUT_MS) : null;
  try { return await fetchImpl(url, Object.assign({}, opts || {}, ctl ? { signal: ctl.signal } : {})); }
  finally { if (timer) clearTimeout(timer); }
}

async function getSpotifyToken(fetchImpl, clientId, clientSecret) {
  const basic = Buffer.from(clientId + ":" + clientSecret).toString("base64");
  const res = await fetchT(fetchImpl, TOKEN_URL, {
    method: "POST",
    headers: { Authorization: "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded" },
    body: "grant_type=client_credentials",
  });
  if (!res.ok) throw new Error("spotify-token-" + res.status);
  const j = await res.json();
  if (!j || !j.access_token) throw new Error("spotify-token-empty");
  return j.access_token;
}

async function searchSpotify(fetchImpl, token, q) {
  const url = SEARCH_URL + "?q=" + encodeURIComponent(q) + "&type=artist,track,playlist&limit=5&market=IL";
  const res = await fetchT(fetchImpl, url, { headers: { Authorization: "Bearer " + token } });
  if (res.status === 429) { const e = new Error("spotify-rate-limited"); e.rateLimited = true; throw e; }
  if (!res.ok) throw new Error("spotify-search-" + res.status);
  return res.json();
}

// קישור ספוטיפי מלא → {type,id} (אותו כלל כמו parseSpotify באפליקציה)
function parseSpotifyUrl(s) {
  const m = /open\.spotify\.com\/(?:intl-[a-z]{2}\/)?(playlist|album|track|artist)\/([A-Za-z0-9]+)/i.exec(String(s || ""));
  return m ? { type: m[1].toLowerCase(), id: m[2] } : null;
}
const SHORT_LINK_RE = /^https?:\/\/(spotify\.link|spoti\.fi)\/\S+$/i;

/* קישור מקוצר (spotify.link) → הקישור המלא. מנסים קודם את כותרת ההפניה, ואם
   השירות מחזיר דף HTML — מחפשים בתוכו את הכתובת המלאה. best-effort. */
async function expandShortLink(fetchImpl, link) {
  const res = await fetchT(fetchImpl, link, { redirect: "manual", headers: { "User-Agent": "Mozilla/5.0" } });
  const loc = res.headers && typeof res.headers.get === "function" ? res.headers.get("location") : null;
  const fromLoc = parseSpotifyUrl(loc);
  if (fromLoc) return fromLoc;
  const fromUrl = parseSpotifyUrl(res.url);
  if (fromUrl) return fromUrl;
  let body = "";
  try { body = await res.text(); } catch (e) { body = ""; }
  return parseSpotifyUrl(body);
}

function spotifyFields(type, id) {
  return {
    type: type, id: id,
    url: "https://open.spotify.com/" + type + "/" + id,
    embed: "https://open.spotify.com/embed/" + type + "/" + id + "?utm_source=barbertor",
  };
}

// תאריך "YYYY-MM-DD" של היום לפי שעון המכונה (בקרון: Asia/Jerusalem)
function todayKeyLocal(now) {
  const d = new Date(now);
  const p = (n) => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
}

/* השלב עצמו. db = firebase-admin database (או מדומה עם ref(path).update/set).
   מחזיר סיכום {resolved, notFound, links, skipped, apiCalls, reason?}. */
async function resolveMusicQueries(db, shopsVal, privVal, opts) {
  const o = opts || {};
  const fetchImpl = o.fetchImpl || (typeof fetch !== "undefined" ? fetch : null);
  const clientId = o.clientId || process.env.SPOTIFY_CLIENT_ID || "";
  const clientSecret = o.clientSecret || process.env.SPOTIFY_CLIENT_SECRET || "";
  const now = o.now || Date.now();
  const todayKey = o.todayKey || todayKeyLocal(now);
  const maxPerRun = o.maxPerRun || 25;
  const shopFilter = o.shopFilter || "";
  const log = o.log || (() => {});
  const summary = { resolved: 0, notFound: 0, links: 0, skipped: 0, apiCalls: 0 };

  // 1. איסוף עבודה: תורים קרובים עם בקשת טקסט שלא הומרה / קישור מקוצר
  const jobs = [];
  for (const sid of Object.keys(shopsVal || {})) {
    if (shopFilter && sid !== shopFilter) continue;
    const shop = shopsVal[sid] || {};
    if (!shop.shop || shop.shop.musicMode !== "personal") continue;
    const bk = shop.bookings;
    if (!bk || typeof bk !== "object") continue;
    const priv = (privVal && privVal[sid] && privVal[sid].bk) || {};
    for (const k of Object.keys(bk)) {
      const b = bk[k];
      if (!b || !b.id || b.status === "cancelled" || !b.date || b.date < todayKey) continue;
      // המוזיקה בצומת הגלוי (מספרה לא מאובטחת) או בכספת (מספרה מאובטחת)
      const cands = [];
      if (b.music && typeof b.music === "object") cands.push({ music: b.music, path: "shops/" + sid + "/bookings/" + k + "/music" });
      const p = priv[b.id];
      if (p && p.music && typeof p.music === "object") cands.push({ music: p.music, path: "private/" + sid + "/bk/" + b.id + "/music" });
      for (const c of cands) {
        const mu = c.music;
        if (mu.url || mu.silence) continue;                                     // כבר יש נגן / ביקש שקט
        const tried = Number(mu.resolveTried) || 0;
        if (tried && now - tried < RETRY_NOT_FOUND_MS) { summary.skipped++; continue; }
        if (typeof mu.link === "string" && SHORT_LINK_RE.test(mu.link)) jobs.push({ kind: "link", key: mu.link, music: mu, path: c.path });
        else if (typeof mu.query === "string" && mu.query.trim()) {
          if (SHORT_LINK_RE.test(mu.query.trim())) jobs.push({ kind: "link", key: mu.query.trim(), music: mu, path: c.path });
          else jobs.push({ kind: "query", key: norm(cleanQuery(mu.query)), music: mu, path: c.path });
        }
      }
    }
  }
  if (!jobs.length) return Object.assign(summary, { reason: "nothing-to-do" });
  if (!fetchImpl) return Object.assign(summary, { reason: "no-fetch" });

  // 2. קישורים מקוצרים — לא דורשים מפתח
  const cache = new Map();   // אותה בקשה בכמה תורים → קריאה אחת
  let token = null, tokenFailed = false, stop = false;
  const startedAt = Date.now();
  for (const j of jobs) {
    if (stop) break;
    if (Date.now() - startedAt > STEP_BUDGET_MS) { log("מוזיקה: נגמר הזמן המוקצב לשלב — השאר בריצה הבאה"); break; }
    try {
      let hit;
      if (cache.has(j.kind + "|" + j.key)) hit = cache.get(j.kind + "|" + j.key);
      else {
        if (summary.apiCalls >= maxPerRun) { summary.skipped++; continue; }
        if (j.kind === "link") {
          summary.apiCalls++;
          const r = await expandShortLink(fetchImpl, j.key);
          hit = r ? { type: r.type, id: r.id, name: "" } : null;
        } else {
          if (!clientId || !clientSecret) { summary.skipped++; continue; }   // אין מפתחות — מדלגים בשקט
          if (tokenFailed) { summary.skipped++; continue; }
          if (!token) {
            try { token = await getSpotifyToken(fetchImpl, clientId, clientSecret); }
            catch (e) {
              tokenFailed = true; summary.skipped++;
              if (isTimeout(e)) { log("מוזיקה: ספוטיפי לא ענה בזמן — ננסה שוב בריצה הבאה"); stop = true; }
              else log("מוזיקה: אימות מול ספוטיפי נכשל (" + ((e && e.message) || e) + ") — בדקו את SPOTIFY_CLIENT_ID/SECRET ב-GitHub Secrets");
              continue;
            }
          }
          summary.apiCalls++;
          const json = await searchSpotify(fetchImpl, token, cleanQuery(j.music.query));
          hit = pickSpotifyMatch(j.music.query, json);
        }
        cache.set(j.kind + "|" + j.key, hit);
      }
      if (hit && hit.id) {
        const next = Object.assign({}, j.music, spotifyFields(hit.type, hit.id));
        if (hit.name) next.label = hit.name;
        next.resolvedAt = now;
        delete next.resolveTried;
        await db.ref(j.path).set(next);
        if (j.kind === "link") summary.links++; else summary.resolved++;
      } else {
        await db.ref(j.path + "/resolveTried").set(now);
        summary.notFound++;
      }
    } catch (e) {
      if (e && e.rateLimited) { log("מוזיקה: ספוטיפי ביקש להאט — ננסה שוב בריצה הבאה"); stop = true; }
      else if (isTimeout(e)) { log("מוזיקה: ספוטיפי לא ענה בזמן — עוצרים, ננסה שוב בריצה הבאה"); summary.skipped++; stop = true; }
      else { log("מוזיקה: דילוג על בקשה (" + ((e && e.message) || e) + ")"); summary.skipped++; }
    }
  }
  return summary;
}

module.exports = {
  norm, cleanQuery, pickSpotifyMatch, getSpotifyToken, searchSpotify,
  expandShortLink, parseSpotifyUrl, resolveMusicQueries, todayKeyLocal,
};
