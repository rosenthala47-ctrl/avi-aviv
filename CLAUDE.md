# BarberTor — הנחיות עבודה

אפליקציית תורים לספרים. PWA סטטי בעברית (RTL), וניל JS, בלי שלב build.

## פרסום — לקרוא לפני שמסיימים סשן

**האפליקציה החיה מתפרסמת רק מדחיפה ל-`main` של המאגר `ori-grushko`.**

זה המקור היחיד לאמת לגבי מה שהמשתמשים רואים:

| מה | איפה | מתפרסם? |
|---|---|---|
| `grushko/main` | `rosenthala47-ctrl/ori-grushko` | **כן** — `firebase-hosting.yml` דוחף ל-`barbertor.web.app` |
| `origin/claude/...` | `rosenthala47-ctrl/avi-aviv` | לא — ענף פיתוח בלבד |
| `origin/main` | `rosenthala47-ctrl/avi-aviv` | לא — פרויקט אחר לגמרי |

### הכלל

בסוף כל סשן שבו שינית קוד, אחרי הדחיפה לענף הפיתוח, **דחוף גם לפרודקשן**:

```bash
git push origin HEAD:claude/barbershop-booking-app-rsgc4x   # ענף העבודה
git push grushko HEAD:main                                   # ← זה מה שמפרסם
```

בעל המאגר אישר במפורש (אוגוסט 2026) שהדחיפה לפרודקשן תתבצע אוטומטית בסוף
כל סשן, בלי לשאול כל פעם.

לפני הדחיפה ודא שזו הרצה קדימה ולא כתיבה מחדש של היסטוריה:

```bash
git merge-base --is-ancestor grushko/main HEAD && echo OK
```

אם זו לא הרצה קדימה — **עצור ושאל**. אל תשתמש ב-`--force` על `main`.

אחרי הדחיפה ודא שהפרסום הצליח (`firebase-hosting.yml` במאגר `ori-grushko`),
ואמור למשתמש איזו גרסה עלתה.

**רקע:** במשך כמה סשנים העבודה נדחפה רק לענף הפיתוח, האפליקציה נשארה תקועה
על גרסה ישנה, והמשתמש הוא זה שגילה את זה בכל פעם. אל תחזור על זה.

## גרסאות

לכל שחרור חייבת להיות גרסה חדשה, אחרת דפדפנים ואפליקציות מותקנות מגישות
קבצים ישנים מהמטמון:

```bash
tools/bump-version.sh 106
```

הסקריפט מסנכרן את המספר ב-`index.html` (‎`?v=NN`‎), `assets/js/app.js`
(`APP_VERSION`), `sw.js` (`CACHE`) ו-`version.json`. אל תערוך אותם ידנית.

## מבנה

| קובץ | תפקיד |
|---|---|
| `assets/js/app.js` | כל ה-UI והאירועים (~5,400 שורות) |
| `assets/js/store.js` | נתונים + Firebase RTDB; `buildBooking` אוכף חוקי הזמנה |
| `assets/js/auth.js` | Firebase Auth (Google + אימייל) |
| `config.js` | הגדרות. `authShops: "*"` = מודל חשבונות לכל המספרות |
| `.github/scripts/send-push.js` | קרון התראות, כל 5 דקות |
| `.github/scripts/backup.js` | גיבוי לילי כ-artifact |

ריבוי מספרות דרך `location.hash` → `shopId`. ללא hash = מסך פתיחה/הרשמה.

## בדיקות

אין מסגרת בדיקות. לפני דחיפה, לכל הפחות:

```bash
node -c assets/js/app.js && node -c assets/js/store.js
```

לשינויים בזרימות אמיתיות — הרם `python3 -m http.server`, והרץ Chromium דרך
Playwright עם `executablePath: '/opt/pw-browsers/chromium'` ו-`--no-sandbox`.
אפשר לזרוע מספרה ישירות ב-`localStorage` תחת המפתח
`ug_barber_state_v1__<shopId>` במקום לעבור את אשף ההרשמה. שים לב: מעבר
לכתובת שנבדלת רק ב-hash אינו טוען את הדף מחדש — צריך `page.reload()` אחריו.

את לוגיקת הקרון אפשר לבדוק מול RTDB מדומה בלי לגעת בפיירבייס אמיתי.

## סודות

`FIREBASE_SERVICE_ACCOUNT` נמצא ב-GitHub Secrets בלבד — לעולם לא במאגר
ולא בצ׳אט. ערכי `config.js` (apiKey וכו׳) אינם סודיים; הם מוגנים בכללי
`database.rules.json`.

גיבויים מכילים שמות וטלפונים של לקוחות — הם עולים כ-artifact פרטי, לא
כקומיט למאגר.

## שפה

הממשק, ההערות בקוד וההודעות למשתמש — בעברית. המשתמש אינו מתכנת: הסבר
בפשטות, בלי ז׳רגון, ואמור מה נבדק בפועל לעומת מה שלא.
