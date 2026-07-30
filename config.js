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
    apiKey: "AIzaSyC_kyz97Ee0t42cDqfy4NcApxw14eceCQM",
    authDomain: "barbertor.firebaseapp.com",
    projectId: "barbertor",
    storageBucket: "barbertor.firebasestorage.app",
    messagingSenderId: "239278537111",
    appId: "1:239278537111:web:91867686ede5e9f2006cd2",
    // המסד של barbertor (בבעלותך) — Realtime Database, חינמי ומוגן.
    databaseURL: "https://barbertor-default-rtdb.europe-west1.firebasedatabase.app",
  },

  /* ---------------------------------------------------------------------
     שליחת מייל אישור הזמנה ללקוח (אופציונלי, חינם, ללא כרטיס אשראי) — EmailJS.
     איך מפעילים:
       1) נרשמים בחינם ב-https://www.emailjs.com
       2) Email Services → מחברים חשבון (למשל Gmail) → מעתיקים Service ID
       3) Email Templates → יוצרים תבנית עם המשתנים:
          {{to_name}} {{service}} {{date}} {{time}} {{duration}} {{price}}
          {{shop_name}} {{shop_address}} {{shop_phone}} {{calendar_url}}
          ({{calendar_url}} = קישור "הוסף ליומן Google")
          ובשדה "To email" של התבנית שמים: {{to_email}}
          → מעתיקים Template ID
       4) Account → API Keys → מעתיקים את ה-Public Key
       5) מדביקים כאן את שלושת הערכים. אם ריק — התכונה פשוט כבויה. */
  emailjs: {
    publicKey: "EWeh0d1Y_5DcQd-5A",
    serviceId: "service_t2k5p7c",
    templateId: "template_vpwtm9r",
  },

  /* מפתח Web Push (VAPID) — לשליחת התראות פוש גם כשהאפליקציה סגורה לגמרי (FCM).
     איפה משיגים: Firebase Console → Project Settings → Cloud Messaging →
     "Web Push certificates" → Generate key pair → להעתיק לכאן.
     אם ריק — האפליקציה תשלח תזכורות רק כשהיא פתוחה/ברקע (כמו קודם). */
  vapidKey: "BLenPWZ30w1QMg3ojqW032DY4cPKex6Dv8XMnKznwlrHZOhdWkpF7712cfVfjsUH3C0fvSCSGdSYMKj4VK4CKtU",
};
