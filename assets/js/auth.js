/* =========================================================================
   Auth — התחברות מנהל מאובטחת (Firebase Authentication, אימייל+סיסמה).
   אופציונלי ובטוח: אם אין חיבור ענן / ה-SDK לא נטען — פשוט לא זמין,
   והאפליקציה ממשיכה לעבוד עם הכניסה הרגילה (קוד).
   =========================================================================*/
window.UG = window.UG || {};
UG.Auth = (function () {
  let _auth = null, ready = false, failed = false, loading = null;
  const changeSubs = new Set();

  function configured() {
    const c = UG_CONFIG.firebase;
    return !!(c && c.apiKey && c.projectId);
  }
  function loadScript(src) {
    return new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src; s.onload = res; s.onerror = rej; document.head.appendChild(s);
    });
  }

  async function ensure() {
    if (ready) return true;
    if (failed || !configured()) { failed = true; return false; }
    if (!loading) loading = (async () => {
      try {
        // firebase-app כבר אותחל ע״י store.js כשיש חיבור ענן; בלי אפליקציה — אין Auth
        if (typeof firebase === "undefined" || !firebase.apps || !firebase.apps.length) { failed = true; return false; }
        if (!firebase.auth) await loadScript("https://www.gstatic.com/firebasejs/10.12.2/firebase-auth-compat.js");
        _auth = firebase.auth();
        _auth.onAuthStateChanged((user) => changeSubs.forEach((fn) => { try { fn(user); } catch (e) {} }));
        ready = true;
        return true;
      } catch (e) {
        failed = true;
        console.warn("[UG] Auth לא זמין:", e && e.message ? e.message : e);
        return false;
      }
    })();
    return loading;
  }

  function humanError(e) {
    const c = (e && e.code) || "";
    if (c === "auth/invalid-email") return "אימייל לא תקין";
    if (c === "auth/missing-password" || c === "auth/weak-password") return "סיסמה חלשה מדי (לפחות 6 תווים)";
    if (c === "auth/email-already-in-use") return "האימייל כבר רשום — נסו להתחבר";
    if (c === "auth/invalid-credential" || c === "auth/wrong-password" || c === "auth/user-not-found") return "אימייל/סיסמה שגויים — אם זו הפעם הראשונה, לחצו ״הרשמה (חשבון חדש)״";
    if (c === "auth/operation-not-allowed") return "התחברות במייל עדיין לא הופעלה ב-Firebase";
    if (c === "auth/too-many-requests") return "יותר מדי ניסיונות — נסו שוב מאוחר יותר";
    return "שגיאת התחברות";
  }

  return {
    configured,
    async available() { return await ensure(); },
    ensure,
    currentUid() { return _auth && _auth.currentUser ? _auth.currentUser.uid : null; },
    currentEmail() { return _auth && _auth.currentUser ? _auth.currentUser.email : null; },
    async signIn(email, pass) { await ensure(); return _auth.signInWithEmailAndPassword(email, pass); },
    async signUp(email, pass) { await ensure(); return _auth.createUserWithEmailAndPassword(email, pass); },
    async signOut() { if (_auth) return _auth.signOut(); },
    async reset(email) { await ensure(); return _auth.sendPasswordResetEmail(email); },
    onChange(fn) { changeSubs.add(fn); },
    humanError,
  };
})();
