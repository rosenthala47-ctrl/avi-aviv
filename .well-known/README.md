# אימות בעלות לאפליקציית אנדרואיד (Digital Asset Links)

הקובץ `assetlinks.json` מקשר בין האתר לאפליקציה שב-Google Play, וכך
**מסתיר את שורת הכתובת** (האפליקציה נפתחת במסך מלא, כמו אפליקציה רגילה).

הוא חייב להיות נגיש בכתובת:
`https://<הדומיין-שלך>/.well-known/assetlinks.json`

## מה צריך למלא
1. **package_name** — שם החבילה של האפליקציה. חייב להיות זהה לזה שבחרתם
   ב-PWABuilder (ברירת המחדל כאן: `com.origrushko.booking`).
2. **sha256_cert_fingerprints** — טביעת האצבע SHA-256 של מפתח החתימה של האפליקציה.
   איפה משיגים:
   - **PWABuilder**: בחבילת האנדרואיד שמורידים יש קובץ `assetlinks.json` מוכן
     עם טביעת האצבע הנכונה — אפשר פשוט להחליף בו את הקובץ הזה.
   - **Google Play Console**: `Release → Setup → App integrity → App signing` →
     מעתיקים את "SHA-256 certificate fingerprint".

מחליפים את `REPLACE_WITH_YOUR_APP_SIGNING_SHA256_FINGERPRINT` בטביעת האצבע,
דוחפים ל-GitHub, וזהו.
