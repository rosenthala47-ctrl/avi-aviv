/* =========================================================================
   הגדרות אפליקציית התורים — BarberTor
   -------------------------------------------------------------------------
   קובץ זה הוא המקום היחיד שצריך לגעת בו כדי להתאים את האפליקציה.
   =========================================================================*/
window.UG_CONFIG = {

  /* סיסמת הכניסה הנסתרת לצד הניהול.
     הכניסה למנהל מוסתרת: מקישים 3 פעמים ברצף על ריבוע הלוגו (האות "א")
     במסך הראשי, ואז נפתח חלון להזנת הסיסמה. הלקוחות אינם רואים כניסה זו. */
  ownerPasscode: "origrou201",

  /* סיסמאות נוספות שגם פותחות את מצב המנהל (בנוסף ל-ownerPasscode) */
  ownerPasscodesExtra: ["5335"],

  /* כתובת האתר הציבורית (לשיתוף קישורים).
     חשוב כשעוטפים את האפליקציה כאפליקציית מובייל (Capacitor/Cordova):
     בתוך המעטפת הכתובת הפנימית היא capacitor://localhost ואי אפשר לשתף אותה,
     לכן הקישורים לשיתוף/העתקה ייבנו מהכתובת כאן. בדפדפן רגיל — אם ריק,
     משתמשים בכתובת הנוכחית. לדוגמה: "https://barbertor.web.app".
     חשוב: ללא סלאש בסוף. */
  publicBaseUrl: "https://barbertor.web.app",

  /* ברירות מחדל של מספרה חדשה — כל ספר קובע את הפרטים שלו במסך "הגדרות" */
  defaults: {
    shopName: "המספרה שלי",
    tagline: "מספרה",
    phone: "",
    address: "",
    slotStep: 45,          // מרווח בין תורים (דקות) — למשל כל 45 דקות
    reminderMinutes: 60,   // כמה זמן לפני התור נשלחת תזכורת
  },

  /* ---------------------------------------------------------------------
     חיבור בין מכשירים (אופציונלי):
     כברירת מחדל האפליקציה עובדת במצב מקומי — כל השינויים מסונכרנים
     בזמן אמת בין כל הכרטיסיות/המשתמשים באותו הדפדפן/מכשיר.

     כדי לסנכרן בזמן אמת בין *כל* המכשירים בעולם (הלקוחות בטלפונים שלהם
     והבעלים בטלפון שלו), הדביקו כאן את פרטי הפרויקט שלכם מ-Firebase.
     מדריך מלא נמצא בקובץ README.md.
     אם משאירים ריק — האפליקציה פשוט תרוץ במצב מקומי.
     --------------------------------------------------------------------- */
  firebase: {
    apiKey: "AIzaSyDPl_DjdAV873aHBBCoqnNk1YTZx6eG7kQ",
    authDomain: "gb201-e1c85.firebaseapp.com",
    projectId: "gb201-e1c85",
    storageBucket: "gb201-e1c85.firebasestorage.app",
    messagingSenderId: "92589759772",
    appId: "1:92589759772:web:8fb918b085df403bbe2ed9",
    measurementId: "G-7JXW84Z5PW",
    // ריק = נשארים על Firestore (gb201) עד שנעביר את הנתונים של אורי בבטחה.
    // המעבר ל-Realtime Database של barbertor יופעל שוב אחרי המיגרציה.
    databaseURL: "",
  },

  /* ---------------------------------------------------------------------
     שליחת מייל אישור הזמנה ללקוח (אופציונלי, חינם, ללא כרטיס אשראי) — EmailJS.
     איך מפעילים:
       1) נרשמים בחינם ב-https://www.emailjs.com
       2) Email Services → מחברים חשבון (למשל Gmail) → מעתיקים Service ID
       3) Email Templates → יוצרים תבנית עם המשתנים:
          {{to_name}} {{service}} {{date}} {{time}} {{duration}} {{price}}
          {{shop_name}} {{shop_address}} {{shop_phone}}
          ובשדה "To email" של התבנית שמים: {{to_email}}
          → מעתיקים Template ID
       4) Account → API Keys → מעתיקים את ה-Public Key
       5) מדביקים כאן את שלושת הערכים. אם ריק — התכונה פשוט כבויה. */
  emailjs: {
    publicKey: "",
    serviceId: "",
    templateId: "",
  },

  /* מפתח Web Push (VAPID) — לשליחת התראות פוש גם כשהאפליקציה סגורה לגמרי (FCM).
     איפה משיגים: Firebase Console → Project Settings → Cloud Messaging →
     "Web Push certificates" → Generate key pair → להעתיק לכאן.
     אם ריק — האפליקציה תשלח תזכורות רק כשהיא פתוחה/ברקע (כמו קודם). */
  vapidKey: "BGkGsIAeOaXpzKZI16P919WEnnQN7gU4vtYxBGsgUdsB4ym5hOoy1qbenQSyn5hZH2yesWaymlGJOAsHblyYG-U",
};

/* ---------------------------------------------------------------------
   מצב בדיקה למסד החדש (Realtime Database של barbertor) — לסשן הזה בלבד.
   פותחים: barbertor.web.app/?newdb=1#main  → האפליקציה קוראת מהמסד החדש,
   בלי להשפיע על אף אחד אחר (כולם נשארים על המסד הישן). לאחר אימות שהכל
   עובד, נהפוך את זה לברירת המחדל.
   --------------------------------------------------------------------- */
try {
  if (typeof location !== "undefined" && /[?&]newdb=1/.test(location.search || "")) {
    window.UG_CONFIG.firebase = {
      apiKey: "AIzaSyC_kyz97Ee0t42cDqfy4NcApxw14eceCQM",
      authDomain: "barbertor.firebaseapp.com",
      databaseURL: "https://barbertor-default-rtdb.europe-west1.firebasedatabase.app",
      projectId: "barbertor",
      storageBucket: "barbertor.firebasestorage.app",
      messagingSenderId: "239278537111",
      appId: "1:239278537111:web:91867686ede5e9f2006cd2",
    };
    window.UG_CONFIG.vapidKey = "BLenPWZ30w1QMg3ojqW032DY4cPKex6Dv8XMnKznwlrHZOhdWkpF7712cfVfjsUH3C0fvSCSGdSYMKj4VK4CKtU";
  }
} catch (e) {}
