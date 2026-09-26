#!/usr/bin/env bash
# מעלה את גרסת האפליקציה בכל המקומות בבת אחת.
# שימוש:  tools/bump-version.sh 82
#
# למה צריך את זה: לדפדפן (ובמיוחד לאפליקציה מותקנת) יש נטייה להגיש קבצים
# ישנים מהמטמון. לכן כל שחרור מקבל כתובת חדשה לקבצים (‎?v=NN‎) — כתובת שהדפדפן
# לא ראה מעולם ולכן חייב להוריד מהרשת. הסקריפט מסנכרן את המספר בכל המקומות:
#   index.html   — ‎?v=NN‎ על ה-CSS וקובצי ה-JS
#   assets/js/app.js — APP_VERSION (מוצג בהגדרות)
#   sw.js        — שם המטמון
#   version.json — הגרסה שהאפליקציה בודקת מולה כדי לזהות עדכון
set -euo pipefail

V="${1:-}"
if [[ ! "$V" =~ ^[0-9]+$ ]]; then
  echo "שימוש: tools/bump-version.sh <מספר גרסה>   (למשל: tools/bump-version.sh 82)" >&2
  exit 1
fi

cd "$(dirname "$0")/.."

# ?v=NN על כל קובצי ה-JS/CSS המקומיים ב-index.html (מוסיף אם אין, מחליף אם יש)
perl -0pi -e 's{(href="assets/styles\.css)(\?v=\d+)?"}{$1?v='"$V"'"}g'   index.html
perl -0pi -e 's{(src="config\.js)(\?v=\d+)?"}{$1?v='"$V"'"}g'            index.html
perl -0pi -e 's{(src="assets/js/[a-z]+\.js)(\?v=\d+)?"}{$1?v='"$V"'"}g'  index.html

perl -pi -e 's/const APP_VERSION = "\d+";/const APP_VERSION = "'"$V"'";/' assets/js/app.js
perl -pi -e 's/const CACHE = "ug-barber-v\d+";/const CACHE = "ug-barber-v'"$V"'";/' sw.js
printf '{ "version": "%s" }\n' "$V" > version.json

echo "עודכן לגרסה $V:"
grep -o 'assets/js/app\.js?v=[0-9]*' index.html | head -1
grep -o 'const APP_VERSION = "[0-9]*"' assets/js/app.js
grep -o 'const CACHE = "ug-barber-v[0-9]*"' sw.js
cat version.json
