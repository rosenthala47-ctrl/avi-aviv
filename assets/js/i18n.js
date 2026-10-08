/* =========================================================================
   מנוע תרגום (שכבת DOM) — נטען רק כשהשפה אינה עברית. כרגע: מספרת try באנגלית.
   במספרות אחרות הקובץ הזה לא נטען בכלל, ולכן אין לו שום השפעה עליהן.

   איך זה עובד: האפליקציה ממשיכה לייצר את המסכים בעברית בדיוק כמו תמיד. אחרי כל
   ציור, MutationObserver עובר על מה שנוסף ומחליף טקסטים לפי מילון (i18n-en.js).
   כך לא נוגעים ב-~1,700 המקומות בקוד שבהם מופיע טקסט, וגם לא מסתכנים בהם.

   סדר החיפוש לכל טקסט:
   1. התאמה מדויקת (אחרי נרמול רווחים).
   2. התאמה של "הליבה" — בלי אמוג׳י/פיסוק בהתחלה ובסוף — ואז מחזירים אותם.
   3. תבניות עם {0},{1}… לטקסט שמשולבים בו ערכים (שעה, שם, מספר). הערכים עצמם
      מתורגמים רקורסיבית ("היום" → "Today").
   4. פירוק לפי מפרידים נפוצים (" · ", " — ") ותרגום כל חלק.
   משפט שמפוצל בתגיות (למשל <b>) מתורגם כיחידה אחת דרך "תבנית אלמנט": הטקסט עם
   <0/>,<1/> במקום תתי-האלמנטים, שנשמרים כמו שהם (כולל data-act ומאזינים).
   =========================================================================*/
(function () {
  "use strict";
  window.UG = window.UG || {};
  const HEB = /[֐-׿]/;
  const ATTRS = ["placeholder", "title", "aria-label", "alt"];
  const INLINE = new Set(["B", "STRONG", "I", "EM", "SPAN", "A", "SMALL", "U", "BDI", "BR", "SUP", "SUB", "MARK"]);
  const SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA", "NOSCRIPT", "CODE", "PRE"]);
  // ערך שמשובץ באמצע משפט מקבל אות קטנה: "You have an appointment today at 10:00"
  const MID_LOWER = new Set(["Today", "Tomorrow"]);
  const SEPS = /( · | — | – | \| |\n)/;
  const EDGE = /^([^֐-׿A-Za-z0-9]*)([\s\S]*?)([^֐-׿A-Za-z0-9]*)$/;

  const exact = new Map();
  const patterns = [];
  const cache = new Map();
  const missing = new Map();
  let obs = null;

  function norm(s) { return String(s).replace(/\s+/g, " ").trim(); }
  function reEsc(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  function load(dict) {
    Object.keys(dict).forEach((k) => {
      const nk = norm(k), v = dict[k];
      if (v == null) return;   // ערך null = ביטול רשומה מהמילון האוטומטי
      if (/\{\d+\}/.test(nk)) {
        const idx = [];
        let re = "^";
        const parts = nk.split(/(\{\d+\})/);
        parts.forEach((p, i) => {
          const m = /^\{(\d+)\}$/.exec(p);
          // ערך בקצה הטקסט יכול להיות ריק ("…בעבר{0}" כשאין מה להוסיף); באמצע — לפחות תו
          const edge = (i === 0 || (i === 1 && parts[0] === "")) || (i === parts.length - 1 || (i === parts.length - 2 && parts[parts.length - 1] === ""));
          if (m) { re += edge ? "([\\s\\S]*?)" : "([\\s\\S]+?)"; idx.push(+m[1]); } else re += reEsc(p);
        });
        patterns.push({ re: new RegExp(re + "$"), idx, v, lit: nk.replace(/\{\d+\}/g, "").length });
      } else exact.set(nk, v);
    });
    // תבנית עם יותר טקסט קבוע נבדקת קודם — היא ספציפית יותר
    patterns.sort((a, b) => b.lit - a.lit);
  }

  // כמה מילים בעברית יש בטקסט
  function hebWords(s) { return (String(s).match(/[֐-׿][֐-׿׳״'"\-]*/g) || []).length; }

  function fill(v, idx, caps, depth) {
    const vals = caps.map((c) => tr(c, depth + 1));
    /* הגנה: ערך שנשאר בעברית ויש בו 4 מילים ומעלה הוא כנראה קטע משפט, לא שם —
       סימן שהתבנית "בלעה" משפט אחר (למשל "{0} תורים{1}" על "רוצים גם אתר… תורים בחינם").
       במקרה כזה דוחים את ההתאמה, במקום לייצר עירוב של עברית ואנגלית. */
    if (vals.some((x) => HEB.test(x) && hebWords(x) >= 4)) return null;
    if (typeof v === "function") return v.apply(null, vals);
    return v.replace(/\{(\d+)\}/g, (m, n, off) => {
      const i = idx.indexOf(+n);
      if (i < 0) return m;
      const s = vals[i];
      return off > 0 && MID_LOWER.has(s) ? s.toLowerCase() : s;
    });
  }

  function viaPatterns(s, depth) {
    for (const p of patterns) {
      const m = p.re.exec(s);
      if (m) { const r = fill(p.v, p.idx, m.slice(1), depth); if (r != null) return r; }
    }
    return null;
  }

  // חיפוש בלי פירוק לפי מפרידים — משמש גם לתבניות אלמנט (שבהן פירוק ישבור את <0/>)
  function lookupCore(n, depth) {
    if (exact.has(n)) return exact.get(n);
    const m = EDGE.exec(n);
    const lead = m[1], core = m[2], trail = m[3];
    if ((lead || trail) && core && exact.has(core)) return lead + exact.get(core) + trail;
    let r = viaPatterns(n, depth);
    if (r != null) return r;
    if ((lead || trail) && core) {
      r = viaPatterns(core, depth);
      if (r != null) return lead + r + trail;
    }
    return null;
  }

  function lookup(n, depth) {
    const r = lookupCore(n, depth);
    if (r != null) return r;
    if (SEPS.test(n)) {
      const parts = n.split(SEPS);
      const out = parts.map((p, i) => (i % 2 || !p.trim()) ? p : tr(p, depth + 1)).join("");
      if (out !== n) return out;
    }
    return null;
  }

  function tr(text, depth) {
    depth = depth || 0;
    if (text == null) return text;
    const s = String(text);
    if (depth > 5 || !HEB.test(s)) return s;
    const n = norm(s);
    if (cache.has(n)) { const c = cache.get(n); return c == null ? s : c; }
    const r = lookup(n, depth);
    cache.set(n, r);
    if (r == null) missing.set(n, (missing.get(n) || 0) + 1);
    return r == null ? s : r;
  }

  /* עברית שנשארה בתוך משפט באנגלית היא נתון של משתמש (שם לקוח, שם זמר). בלי בידוד,
     אלגוריתם הכיווניות "מושך" אליה מספרים ופיסוק סמוכים: "מאיה כהן — 1 suspicious"
     הופך ל-"1 — מאיה כהן suspicious". תווי בידוד (FSI…PDI) שומרים כל רצף עברי במקומו. */
  const HEB_RUN = /[֐-׿](?:[֐-׿׳״'"\-\s]*[֐-׿])?/g;
  function isolate(s) {
    return (HEB.test(s) && /[A-Za-z]/.test(s)) ? s.replace(HEB_RUN, (m) => "⁨" + m + "⁩") : s;
  }

  function translateText(node) {
    const s = node.data;
    if (!HEB.test(s) || s.indexOf("⁨") !== -1) return;   // כבר טופל (בודד)
    const r = tr(s);
    if (r === s) return;
    node.data = /^\s*/.exec(s)[0] + isolate(r) + /\s*$/.exec(s)[0];
  }

  // משפט שמפוצל בתגיות inline — מתרגמים כיחידה, ותתי-האלמנטים נשמרים ומוזזים למקומם
  function tryTemplate(el) {
    const kids = el.childNodes;
    if (kids.length < 2) return false;
    let hebText = false, hasEl = false;
    for (let i = 0; i < kids.length; i++) {
      const k = kids[i];
      if (k.nodeType === 1) { if (!INLINE.has(k.tagName)) return false; hasEl = true; }
      else if (k.nodeType === 3) { if (HEB.test(k.data)) hebText = true; }
      else if (k.nodeType !== 8) return false;
    }
    if (!hebText || !hasEl) return false;
    let key = "";
    const els = [];
    for (let i = 0; i < kids.length; i++) {
      const k = kids[i];
      if (k.nodeType === 1) { key += "<" + els.length + "/>"; els.push(k); }
      else if (k.nodeType === 3) key += k.data;
    }
    const r = lookupCore(norm(key), 0);
    if (r == null) return false;
    const frag = document.createDocumentFragment();
    const used = new Set();
    String(r).split(/(<\d+\/>)/).forEach((part) => {
      const m = /^<(\d+)\/>$/.exec(part);
      if (m) { const e = els[+m[1]]; if (e && !used.has(e)) { used.add(e); frag.appendChild(e); } }
      else if (part) frag.appendChild(document.createTextNode(isolate(part)));
    });
    els.forEach((e) => { if (!used.has(e)) frag.appendChild(e); });   // לא לאבד אף אלמנט
    while (el.firstChild) el.removeChild(el.firstChild);
    el.appendChild(frag);
    return true;
  }

  function translateAttrs(el) {
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (v && HEB.test(v)) { const r = tr(v); if (r !== v) el.setAttribute(a, r); }
    }
    if (el.tagName === "INPUT" && (el.type === "button" || el.type === "submit") && HEB.test(el.value)) el.value = tr(el.value);
  }

  function processElement(el) {
    if (!el || el.nodeType !== 1) return;
    if (el.tagName === "TEXTAREA") { translateAttrs(el); return; }   // לא נוגעים בתוכן — רק ב-placeholder
    if (SKIP.has(el.tagName)) return;
    if (el.getAttribute("translate") === "no" || el.classList.contains("notranslate")) return;
    translateAttrs(el);
    tryTemplate(el);
    const kids = Array.prototype.slice.call(el.childNodes);
    for (const k of kids) {
      if (k.nodeType === 3) translateText(k);
      else if (k.nodeType === 1) processElement(k);
    }
  }

  function inSkipped(node) {
    for (let p = node.parentNode; p && p.nodeType === 1; p = p.parentNode) {
      if (SKIP.has(p.tagName) || p.getAttribute("translate") === "no" || p.classList.contains("notranslate")) return true;
    }
    return false;
  }

  function observe() {
    obs.observe(document.documentElement, {
      subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS,
    });
  }

  function onMutations(records) {
    obs.disconnect();   // השינויים שלנו לא יגרמו לסבב נוסף
    try {
      for (const r of records) {
        if (r.type === "childList") {
          r.addedNodes.forEach((n) => {
            if (!n.isConnected || inSkipped(n)) return;
            if (n.nodeType === 3) translateText(n);
            else if (n.nodeType === 1) processElement(n);
          });
        } else if (r.type === "characterData") {
          if (r.target.isConnected && !inSkipped(r.target)) translateText(r.target);
        } else if (r.type === "attributes") {
          if (r.target.isConnected) translateAttrs(r.target);
        }
      }
    } finally { observe(); }
  }

  UG.I18N = UG.I18N || {};
  UG.I18N.start = function (dict) {
    load(dict || {});
    processElement(document.body);
    if (HEB.test(document.title)) document.title = tr(document.title);
    obs = new MutationObserver(onMutations);
    observe();
    UG.I18N.started = true;
  };
  UG.I18N.t = function (s) { return tr(s); };
  // לבדיקות: הטקסטים שלא נמצא להם תרגום
  UG.I18N.missing = function () { return Array.from(missing.keys()); };
})();
