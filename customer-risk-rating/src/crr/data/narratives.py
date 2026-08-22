"""Synthetic unstructured text: support-call summaries, underwriter notes, KYC extracts.

Design goal
-----------
The hybrid architecture only pays for itself if the free text carries signal the
tabular features do NOT contain. So the phrase banks below are keyed on two
*latent* variables that the structured feature block deliberately under-reveals:

    distress    0..3   forward-looking repayment stress ("my contract ends in March")
    concealment 0..3   opacity / evasiveness about counterparties and fund origin

Both feed the ground-truth outcome model in ``synthetic.py``. A model that reads
only the tabular block therefore leaves measurable AUC on the table, which is
exactly the gap the LLM branch is meant to close.

Text is template-driven (no external LLM call) so generation stays deterministic,
free and offline. Every narrative is fictional.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import numpy as np

LANGUAGES: tuple[str, ...] = ("en", "he")


@dataclass
class NarrativeContext:
    """Everything a narrative template is allowed to see."""

    customer_ref: str
    language: str
    segment: str
    occupation: str
    employment_status: str
    country_code: str
    distress: int  # 0..3
    concealment: int  # 0..3
    pep_flag: bool
    account_age_months: int
    as_of: dt.date
    extras: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Phrase banks — support call summaries
# --------------------------------------------------------------------------

_CALL_OPENERS = {
    "en": [
        "Inbound call, {duration} min, handled by agent {agent}.",
        "Customer called the service centre ({duration} min, agent {agent}).",
        "Outbound retention call placed by agent {agent}, {duration} min.",
        "Chat session escalated to voice; total handling time {duration} min (agent {agent}).",
    ],
    "he": [
        "שיחה נכנסת, {duration} דקות, טופלה על ידי נציג {agent}.",
        "הלקוח פנה למוקד השירות ({duration} דקות, נציג {agent}).",
        "שיחת שימור יוצאת בוצעה על ידי נציג {agent}, {duration} דקות.",
        "צ'אט שהוסלם לשיחה קולית; זמן טיפול כולל {duration} דקות (נציג {agent}).",
    ],
}

_CALL_TOPIC_BY_DISTRESS = {
    0: {
        "en": [
            "Customer asked about the interest rate on the savings product and closed the call satisfied.",
            "Routine request to update the mailing address; identity verified in call.",
            "Customer enquired about adding a second cardholder for a family member.",
            "Question about the mobile app statement export. Resolved on the call.",
            "Customer wanted to confirm the value date of an incoming salary transfer.",
        ],
        "he": [
            "הלקוח שאל על הריבית במוצר החיסכון וסיים את השיחה מרוצה.",
            "בקשה שגרתית לעדכון כתובת למשלוח דואר; זהות אומתה בשיחה.",
            "הלקוח ביקש להוסיף מורשה חתימה נוסף עבור בן משפחה.",
            "שאלה לגבי ייצוא דף חשבון באפליקציה. נפתר במהלך השיחה.",
            "הלקוח ביקש לאמת את תאריך הערך של העברת משכורת נכנסת.",
        ],
    },
    1: {
        "en": [
            "Customer asked whether the monthly instalment could be moved a few days later to align with a delayed salary.",
            "Enquiry about the cost of temporarily increasing the overdraft facility 'just for a month or two'.",
            "Customer asked what happens if one payment is missed; said it was a hypothetical question.",
            "Requested a fee waiver, mentioning that the month had been tighter than usual.",
            "Asked to switch from monthly to bi-weekly billing to smooth cash flow.",
        ],
        "he": [
            "הלקוח שאל האם ניתן לדחות את מועד ההחזר החודשי במספר ימים בהתאם לעיכוב במשכורת.",
            "בירור לגבי עלות הגדלה זמנית של מסגרת האשראי 'רק לחודש-חודשיים'.",
            "הלקוח שאל מה קורה אם החזר אחד לא מבוצע; ציין שמדובר בשאלה תיאורטית.",
            "ביקש ויתור על עמלה, וציין שהחודש היה מאתגר מהרגיל.",
            "ביקש לעבור מחיוב חודשי לחיוב דו-שבועי כדי לאזן תזרים.",
        ],
    },
    2: {
        "en": [
            "Customer explained that their employer has announced a restructuring and their role is under review.",
            "Customer's spouse has stopped working following a medical issue; household income is down materially.",
            "Business owner reported losing their largest client, which represented most of their turnover.",
            "Customer said two of their invoices are 90 days overdue and they are covering payroll from personal funds.",
            "Customer asked about payment holidays and hardship programmes, unprompted, and asked twice how they are recorded.",
            "Customer mentioned an unexpected family medical expense that has consumed their savings buffer.",
        ],
        "he": [
            "הלקוח הסביר שהמעסיק הודיע על ארגון מחדש ותפקידו נמצא בבחינה.",
            "בן/בת הזוג של הלקוח הפסיק/ה לעבוד בעקבות בעיה רפואית; הכנסת משק הבית ירדה משמעותית.",
            "בעל עסק דיווח על אובדן הלקוח הגדול ביותר שלו, שהיווה את עיקר המחזור.",
            "הלקוח ציין ששתי חשבוניות שלו באיחור של 90 יום והוא מממן שכר עבודה מכספים פרטיים.",
            "הלקוח שאל ביוזמתו על הקפאות תשלום ותוכניות מצוקה, ושאל פעמיים כיצד הדבר נרשם.",
            "הלקוח הזכיר הוצאה רפואית משפחתית בלתי צפויה ששחקה את כרית החיסכון שלו.",
        ],
    },
    3: {
        "en": [
            "Customer stated they will not be able to meet next month's instalment and asked what the consequences are.",
            "Customer disclosed they are already in arrears with two other lenders and are prioritising which to pay.",
            "Customer's employment ended last week; severance covers roughly one month of obligations.",
            "Business account holder said they are considering insolvency advice and asked about account freezing.",
            "Customer became distressed on the call, said they are 'juggling' cards to make minimum payments.",
            "Customer asked to draw the full remaining limit immediately and was evasive about the purpose.",
        ],
        "he": [
            "הלקוח מסר שלא יוכל לעמוד בהחזר של החודש הבא ושאל מהן ההשלכות.",
            "הלקוח גילה שהוא כבר בפיגור מול שני מלווים נוספים ומתעדף למי לשלם.",
            "העסקתו של הלקוח הסתיימה בשבוע שעבר; הפיצויים מכסים כחודש אחד של התחייבויות.",
            "בעל חשבון עסקי ציין שהוא שוקל ייעוץ חדלות פירעון ושאל לגבי הקפאת חשבון.",
            "הלקוח היה במצוקה במהלך השיחה, ואמר שהוא 'מגלגל' כרטיסים כדי לעמוד בתשלומי מינימום.",
            "הלקוח ביקש למשוך מיידית את מלוא יתרת המסגרת והתחמק משאלה לגבי הייעוד.",
        ],
    },
}

_CALL_CONCEALMENT_ADDON = {
    1: {
        "en": [
            "Customer preferred not to say who is sending the recurring incoming transfer.",
            "When asked about the purpose of a recent transfer the customer changed the subject.",
        ],
        "he": [
            "הלקוח העדיף שלא לציין מי מבצע את ההעברה הנכנסת החוזרת.",
            "כשנשאל לגבי מטרת העברה אחרונה, הלקוח שינה נושא.",
        ],
    },
    2: {
        "en": [
            "A third party was present and answered several identity questions on the customer's behalf.",
            "Customer asked whether deposits below the reporting threshold are 'handled differently'.",
            "Customer asked the agent to note the funds as a family gift without providing documentation.",
        ],
        "he": [
            "צד שלישי נכח בשיחה והשיב על מספר שאלות זיהוי במקום הלקוח.",
            "הלקוח שאל האם הפקדות מתחת לסף הדיווח 'מטופלות אחרת'.",
            "הלקוח ביקש שהנציג ירשום את הכספים כמתנה משפחתית ללא הצגת מסמכים.",
        ],
    },
    3: {
        "en": [
            "Customer refused to identify the counterparty of a large incoming wire and ended the call abruptly.",
            "Customer asked how to route funds so that they 'do not show up as one transaction'.",
            "Caller could not answer basic security questions; the account holder was reportedly 'travelling and unreachable'.",
        ],
        "he": [
            "הלקוח סירב לזהות את הצד הנגדי בהעברה נכנסת גדולה וניתק את השיחה בפתאומיות.",
            "הלקוח שאל כיצד לנתב כספים כך ש'לא יופיעו כעסקה אחת'.",
            "המתקשר לא ידע להשיב על שאלות אבטחה בסיסיות; לפי הנמסר בעל החשבון 'בחו\"ל ולא זמין'.",
        ],
    },
}

_CALL_CLOSERS = {
    "en": [
        "No follow-up action recorded.",
        "Ticket left open pending documentation from the customer.",
        "Agent scheduled a call-back in 14 days.",
        "Case referred to the relationship manager.",
        "Customer sentiment logged as neutral.",
    ],
    "he": [
        "לא נרשמה פעולת המשך.",
        "הקריאה נותרה פתוחה עד לקבלת מסמכים מהלקוח.",
        "הנציג קבע שיחה חוזרת בעוד 14 יום.",
        "המקרה הועבר למנהל הקשר.",
        "סנטימנט הלקוח תועד כניטרלי.",
    ],
}


# --------------------------------------------------------------------------
# Phrase banks — underwriter notes
# --------------------------------------------------------------------------

_UW_OPENERS = {
    "en": [
        "Underwriting note ({date}), file {ref}.",
        "Credit committee memo, file {ref}, dated {date}.",
        "Analyst review of file {ref} completed {date}.",
    ],
    "he": [
        "הערת חיתום ({date}), תיק {ref}.",
        "מזכר ועדת אשראי, תיק {ref}, מתאריך {date}.",
        "סקירת אנליסט לתיק {ref} הושלמה בתאריך {date}.",
    ],
}

_UW_BY_DISTRESS = {
    0: {
        "en": [
            "Income documentation is complete and consistent with the declared occupation. No adverse findings.",
            "Repayment history across all facilities is clean. Buffer covers more than six months of obligations.",
            "Affordability comfortably within policy; recommend standard terms.",
        ],
        "he": [
            "תיעוד ההכנסה מלא ועקבי עם העיסוק המוצהר. לא נמצאו ממצאים שליליים.",
            "היסטוריית ההחזרים בכל המסגרות תקינה. הכרית מכסה מעל שישה חודשי התחייבויות.",
            "כושר ההחזר בתוך גבולות המדיניות; מומלץ תנאים סטנדרטיים.",
        ],
    },
    1: {
        "en": [
            "Affordability is adequate but the buffer is thin; a single missed salary would breach the stress scenario.",
            "Two late payments in the last year were cured quickly, but the pattern is worth monitoring.",
            "Income is stable on paper; concentration on a single employer is a mild concern.",
        ],
        "he": [
            "כושר ההחזר סביר אך הכרית דקה; החמצת משכורת אחת תפר את תרחיש הקיצון.",
            "שני איחורי תשלום בשנה האחרונה תוקנו במהירות, אך התבנית ראויה למעקב.",
            "ההכנסה יציבה על הנייר; ריכוזיות מול מעסיק יחיד מהווה חשש קל.",
        ],
    },
    2: {
        "en": [
            "Applicant's sector has deteriorated sharply this year; forward-looking affordability is materially weaker than the historical ratios suggest.",
            "Bank statements show the customer is servicing debt from new borrowing rather than from operating income.",
            "The declared income has not been observed in the account for two months. Explanation given was not documented.",
            "Applicant is refinancing to release equity to cover operating losses. The stated purpose does not match the pattern of use.",
        ],
        "he": [
            "הענף של המבקש נחלש בחדות השנה; כושר ההחזר הצופה פני עתיד חלש מהותית ממה שהיחסים ההיסטוריים מרמזים.",
            "דפי החשבון מראים שהלקוח משרת חוב מהלוואות חדשות ולא מהכנסה תפעולית.",
            "ההכנסה המוצהרת לא נצפתה בחשבון במשך חודשיים. ההסבר שניתן לא תועד.",
            "המבקש ממחזר אשראי כדי לשחרר הון לכיסוי הפסדים תפעוליים. הייעוד המוצהר אינו תואם את דפוס השימוש.",
        ],
    },
    3: {
        "en": [
            "In my assessment the file will not perform. Recommend decline; the applicant is refinancing to stay current, not to invest.",
            "Cash flow is negative in five of the last six months and the shortfall is widening. Recommend decline.",
            "Customer is materially over-committed across lenders. Any further exposure is imprudent.",
            "Recommend decline. The supporting documents appear reconstructed after the fact and do not reconcile to the account.",
        ],
        "he": [
            "להערכתי התיק לא יבצע. ממליץ על דחייה; המבקש ממחזר כדי להישאר בתשלומים, לא כדי להשקיע.",
            "התזרים שלילי בחמישה מתוך ששת החודשים האחרונים והפער מתרחב. ממליץ על דחייה.",
            "הלקוח ממונף מהותית מול מלווים שונים. כל חשיפה נוספת אינה זהירה.",
            "ממליץ על דחייה. המסמכים התומכים נראים כמשוחזרים בדיעבד ואינם מתיישבים עם החשבון.",
        ],
    },
}

_UW_CONCEALMENT_ADDON = {
    1: {
        "en": ["Source of wealth narrative is thin but not contradicted by the file."],
        "he": ["נרטיב מקור ההון דל אך אינו נסתר על ידי החומר בתיק."],
    },
    2: {
        "en": [
            "Ownership chain runs through two intermediate holding entities; the ultimate beneficial owner is asserted but not evidenced.",
            "Source of wealth is declared as a family gift with no supporting documentation despite two requests.",
        ],
        "he": [
            "שרשרת הבעלות עוברת דרך שתי חברות אחזקה מתווכות; הנהנה הסופי מוצהר אך אינו מגובה בראיות.",
            "מקור ההון מוצהר כמתנה משפחתית ללא תיעוד תומך, על אף שתי בקשות.",
        ],
    },
    3: {
        "en": [
            "The counterparty structure has no discernible commercial rationale. Escalating to Financial Crime for EDD before any decision.",
            "Documents supplied conflict with the register extract on both incorporation date and shareholding. Treating as a red flag.",
        ],
        "he": [
            "מבנה הצדדים הנגדיים חסר היגיון מסחרי נראה לעין. מסלים ליחידת פשיעה פיננסית לבדיקת EDD טרם כל החלטה.",
            "המסמכים שהוגשו סותרים את תמצית הרישום הן במועד ההתאגדות והן בהחזקות. מסומן כדגל אדום.",
        ],
    },
}


# --------------------------------------------------------------------------
# Phrase banks — KYC document extracts
# --------------------------------------------------------------------------

_KYC_TEMPLATE = {
    "en": (
        "KYC FILE EXTRACT — ref {ref}\n"
        "Relationship opened: {opened}\n"
        "Declared occupation: {occupation} ({employment})\n"
        "Primary jurisdiction: {country}\n"
        "Declared source of funds: {sof}\n"
        "Screening: {screening}\n"
        "Documentation: {docs}\n"
        "Analyst remark: {remark}"
    ),
    "he": (
        "תמצית תיק KYC — אסמכתא {ref}\n"
        "מועד פתיחת הקשר: {opened}\n"
        "עיסוק מוצהר: {occupation} ({employment})\n"
        "מדינת פעילות עיקרית: {country}\n"
        "מקור כספים מוצהר: {sof}\n"
        "סינון: {screening}\n"
        "תיעוד: {docs}\n"
        "הערת אנליסט: {remark}"
    ),
}

_KYC_SCREENING = {
    "clean": {
        "en": ["no sanctions, PEP or adverse-media matches"],
        "he": ["ללא התאמות לרשימות סנקציות, PEP או מדיה שלילית"],
    },
    "pep": {
        "en": ["PEP match confirmed; enhanced due diligence applied"],
        "he": ["התאמת PEP אושרה; הופעלה בדיקת נאותות מוגברת"],
    },
    "adverse": {
        "en": ["adverse media hit under review; relevance not yet dispositioned"],
        "he": ["ממצא מדיה שלילית בבדיקה; טרם הוכרעה רלוונטיות"],
    },
    "hit": {
        "en": ["possible sanctions-list match pending manual disposition"],
        "he": ["התאמה אפשרית לרשימת סנקציות ממתינה להכרעה ידנית"],
    },
}

_KYC_DOCS = {
    0: {"en": ["complete; ID, proof of address and income all verified"], "he": ["מלא; תעודת זהות, אישור כתובת ואסמכתאות הכנסה אומתו"]},
    1: {"en": ["substantially complete; proof of address expires within 60 days"], "he": ["מלא ברובו; אישור הכתובת יפוג בתוך 60 יום"]},
    2: {"en": ["incomplete; source-of-funds evidence outstanding since onboarding"], "he": ["חסר; אסמכתאות למקור הכספים חסרות מאז פתיחת החשבון"]},
    3: {"en": ["materially deficient; identity document expired and refresh overdue"], "he": ["חסר מהותית; מסמך הזיהוי פג תוקף והרענון באיחור"]},
}

_KYC_REMARK = {
    0: {
        "en": ["Profile consistent with declared activity.", "No further action required at this review cycle."],
        "he": ["הפרופיל עקבי עם הפעילות המוצהרת.", "לא נדרשת פעולה נוספת במחזור הסקירה הנוכחי."],
    },
    1: {
        "en": ["Minor inconsistency between declared income and observed credits; tolerable.", "Recommend standard periodic review."],
        "he": ["אי-התאמה קלה בין ההכנסה המוצהרת לזיכויים שנצפו; נסבל.", "מומלצת סקירה תקופתית סטנדרטית."],
    },
    2: {
        "en": [
            "Observed turnover exceeds the declared profile by a wide margin; rationale not established.",
            "Customer engagement with information requests has been slow and partial.",
        ],
        "he": [
            "המחזור שנצפה עולה משמעותית על הפרופיל המוצהר; לא בוסס הסבר.",
            "היענות הלקוח לבקשות מידע הייתה איטית וחלקית.",
        ],
    },
    3: {
        "en": [
            "Activity is inconsistent with every element of the declared profile. Recommend EDD and consider exit.",
            "Repeated failure to evidence source of funds. Escalated for suspicious-activity assessment.",
        ],
        "he": [
            "הפעילות אינה עקבית עם אף מרכיב בפרופיל המוצהר. מומלץ EDD ולשקול סיום התקשרות.",
            "כשל חוזר בהוכחת מקור הכספים. הוסלם להערכת פעילות חשודה.",
        ],
    },
}


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def _pick(rng: np.random.Generator, options: list[str]) -> str:
    return str(options[int(rng.integers(len(options)))])


def _lang(ctx: NarrativeContext) -> str:
    return ctx.language if ctx.language in LANGUAGES else "en"


def build_support_call(rng: np.random.Generator, ctx: NarrativeContext) -> str:
    """Summary of a customer-service interaction."""
    lang = _lang(ctx)
    parts = [
        _pick(rng, _CALL_OPENERS[lang]).format(
            duration=int(rng.integers(2, 27)),
            agent=f"A{int(rng.integers(100, 999))}",
        ),
        _pick(rng, _CALL_TOPIC_BY_DISTRESS[ctx.distress][lang]),
    ]
    if ctx.concealment > 0 and rng.random() < 0.35 + 0.2 * ctx.concealment:
        parts.append(_pick(rng, _CALL_CONCEALMENT_ADDON[ctx.concealment][lang]))
    parts.append(_pick(rng, _CALL_CLOSERS[lang]))
    return " ".join(parts)


def build_underwriter_note(rng: np.random.Generator, ctx: NarrativeContext) -> str:
    """Free-text credit opinion written by a human underwriter."""
    lang = _lang(ctx)
    parts = [
        _pick(rng, _UW_OPENERS[lang]).format(date=ctx.as_of.isoformat(), ref=ctx.customer_ref),
        _pick(rng, _UW_BY_DISTRESS[ctx.distress][lang]),
    ]
    if ctx.concealment > 0 and rng.random() < 0.30 + 0.25 * ctx.concealment:
        parts.append(_pick(rng, _UW_CONCEALMENT_ADDON[ctx.concealment][lang]))
    if ctx.pep_flag and rng.random() < 0.6:
        parts.append(
            "Politically exposed status noted; senior sign-off obtained."
            if lang == "en"
            else "נרשם מעמד של איש ציבור; התקבל אישור דרג בכיר."
        )
    return " ".join(parts)


def build_kyc_extract(rng: np.random.Generator, ctx: NarrativeContext) -> str:
    """Structured-ish extract from the KYC file, rendered as free text."""
    lang = _lang(ctx)
    if ctx.extras.get("sanctions_hits", 0) > 0:
        screening_key = "hit"
    elif ctx.pep_flag:
        screening_key = "pep"
    elif ctx.extras.get("adverse_media_hits", 0) > 0:
        screening_key = "adverse"
    else:
        screening_key = "clean"

    opened = ctx.as_of - dt.timedelta(days=int(ctx.account_age_months * 30.44))
    return _KYC_TEMPLATE[lang].format(
        ref=ctx.customer_ref,
        opened=opened.isoformat(),
        occupation=ctx.occupation,
        employment=ctx.employment_status,
        country=ctx.country_code,
        sof=ctx.extras.get("source_of_funds", "salary"),
        screening=_pick(rng, _KYC_SCREENING[screening_key][lang]),
        docs=_pick(rng, _KYC_DOCS[ctx.concealment][lang]),
        remark=_pick(rng, _KYC_REMARK[max(ctx.distress, ctx.concealment)][lang]),
    )


def build_all(rng: np.random.Generator, ctx: NarrativeContext) -> dict[str, str]:
    """All three narratives for one customer."""
    return {
        "support_call_summary": build_support_call(rng, ctx),
        "underwriter_note": build_underwriter_note(rng, ctx),
        "kyc_document_extract": build_kyc_extract(rng, ctx),
    }
