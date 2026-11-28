#!/usr/bin/env python3
"""מפרסם לספרייה פירוש שבועי לפרשת השבוע: site/parshiot.json

לכל פרשה נוצרים:
  1. העמקה: הרחבה מחולקת לנושאים (כ-30% מאורך הפרשה, ניתן לשינוי ב-config.json)
  2. סיכום קצר (כ-10%) עם הומור מכבד, שמקשר לנושאי ההעמקה

הרצה:
  python parsha_digest.py               # מפרסם רק ביום publish_day ואחרי השעה publish_hour (שעון ישראל) שהוגדרו ב-config.json
  python parsha_digest.py --force       # מפרסם עכשיו בלי קשר ליום
  python parsha_digest.py --dry-run     # מדפיס למסך בלבד, לא שומר
  python parsha_digest.py --regenerate  # מחליף פירוש קיים של פרשת השבוע

משתנה סביבה: ANTHROPIC_API_KEY
משתמש רק בספרייה הסטנדרטית של Python (אין צורך בהתקנות).
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "site", "parshiot.json")
SEFARIA = "https://www.sefaria.org/api"
MODEL = "claude-sonnet-5-5"
TZ = ZoneInfo("Asia/Jerusalem")


# ---------- עזרים ----------
def http(url, data=None, headers=None, method=None, timeout=180):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {e.code} מ-{url.split('?')[0]}: {body[:300]}")


def load_config():
    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def load_entries():
    try:
        with open(DATA, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_entries(entries):
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ---------- טקסט הפרשה ----------
def get_parsha(cfg):
    diaspora = "1" if cfg.get("diaspora") else "0"
    d = http(f"{SEFARIA}/calendars?diaspora={diaspora}&timezone=Asia/Jerusalem")
    for item in d.get("calendar_items", []):
        if (item.get("title") or {}).get("en") == "Parashat Hashavua":
            dv = item.get("displayValue") or {}
            return {"ref": item["ref"], "he": dv.get("he", ""), "en": dv.get("en", "")}
    raise RuntimeError("לא נמצאה פרשת השבוע בלוח של ספריא")


def _flatten(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, list):
        for i in x:
            yield from _flatten(i)


def clean_hebrew(s):
    s = s.replace("־", " ")                 # מקף
    s = re.sub(r"<[^>]+>", " ", s)               # תגיות HTML
    s = re.sub(r"&[a-zA-Z#0-9]+;", " ", s)       # ישויות HTML
    s = re.sub(r"\{[^}]*\}", " ", s)             # {פ} {ס}
    s = re.sub(r"[֑-ׇ]", "", s)        # ניקוד וטעמים
    return re.sub(r"\s+", " ", s).strip()


def clean_display(s):
    """טקסט לקריאה: משאיר ניקוד ופסוקים, מוריד תגיות HTML, סימני פרשיות וטעמי מקרא."""
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"&[a-zA-Z#0-9]+;", " ", s)
    s = re.sub(r"\{[^}]*\}", " ", s)
    s = re.sub(r"[\u0591-\u05AF\u05C0\u05C4\u05C5]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def count_words(s):
    return len(s.split())


def fetch_parsha_verses(ref):
    """מחזיר רשימת פסוקים {label, text}. התווית (למשל 32:4) נקבעת לפי הטווח ב-ref,
    ומושמטת אם המספור שחושב לא מסתיים בפסוק האחרון הצפוי."""
    url = f"{SEFARIA}/v3/texts/{urllib.parse.quote(ref, safe='')}?version=hebrew"
    d = http(url)
    versions = d.get("versions") or []
    if not versions:
        raise RuntimeError(f"ספריא לא החזירה טקסט עבור {ref}")
    raw = versions[0]["text"]
    chapters = raw if raw and isinstance(raw[0], list) else [raw]
    m = re.search(r"(\d+):(\d+)\s*-\s*(\d+):(\d+)\s*$", ref)
    verses, last = [], None
    for ci, chapter in enumerate(chapters):
        c = int(m.group(1)) + ci if m else None
        start = int(m.group(2)) if (m and ci == 0) else 1
        for vi, v in enumerate(_flatten(chapter)):
            text = clean_hebrew(v)
            label = f"{c}:{start + vi}" if m else None
            last = label
            if text:
                verses.append({"label": label, "text": text, "display": clean_display(v)})
    if not m or last != f"{m.group(3)}:{m.group(4)}":
        for v in verses:
            v["label"] = None
    return verses


# ---------- Claude ----------
def ask_claude(system, messages, max_tokens=8000):
    """שולח בקשה ל-Claude. אם התשובה נחתכה (נגמר תקציב הטוקנים), מנסה שוב עם תקציב כפול."""
    budget = max_tokens
    for attempt in range(3):
        payload = {"model": MODEL, "max_tokens": budget, "system": system, "messages": messages}
        d = http(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        text = "".join(b.get("text", "") for b in d["content"] if b.get("type") == "text").strip()
        if d.get("stop_reason") != "max_tokens":
            return text
        print(f"התשובה נחתכה ב-{budget} טוקנים ({count_words(text)} מילים), מנסה שוב עם תקציב כפול")
        budget = min(budget * 2, 32000)
    raise RuntimeError("התשובה של Claude נחתכה שוב ושוב (stop_reason=max_tokens)")


# ----- העמקה: סיכום ארוך לפי נושאים -----
DEEP_SYSTEM = """אתה כותב העמקה בעברית לפרשת השבוע, לקורא שכבר קרא סיכום קצר ורוצה לדעת עוד. ההעמקה מתמקדת במה שמוסיף מעבר לכתוב, ולא חוזרת על הסיפור.
כללים:
- האורך הוא יעד: בערך {target} מילים בסך הכול (בין {lo} ל-{hi}), בין 5 ל-7 נושאים לפי סדר האירועים, כך שכל הפרשה מכוסה.
- בכל נושא: פסקה ראשונה של משפט או שניים בלבד שמזהים מה כתוב (עוגן לקורא). אחריה פסקה שמתחילה במילה "להעמקה:" ובה עיקר הנושא: הקשר, הסבר, שאלה שהטקסט מעלה, פרט שחשוב להבין, ודעות של פרשנים.
- פרשנים ומדרשים: ציין שם פרשן או מקור רק אם אתה בטוח לגמרי שהוא אמר זאת. אם אינך בטוח, כתוב "יש פרשנים" או "אפשר לקרוא את זה כך", או הימנע. אל תמציא ציטוטים, גימטריות או מדרשים.
- אל תוסיף עובדות או אירועים שאינם כתובים בפרשה. הרחבה היא הקשר, הסבר ופרשנות, לא עלילה חדשה.
- טון: מכבד וברור, בלי חזרות ובלי מילים מיותרות. הומור קל רק אם הוא טבעי.
- פורמט מחייב, בלי שום טקסט לפני הנושא הראשון. כל נושא מתחיל בשורת כותרת בדיוק בצורה הזאת:
=== SECTION מזהה | כותרת | טווח ===
ואחריה גוף הנושא. המזהה באותיות לטיניות קטנות ומקפים בלבד (למשל shechem). הכותרת: שתיים עד חמש מילים בעברית. הטווח בצורה 32:4-32:9 לפי מספרי הפסוקים שבטקסט (ואם אין מספרי פסוקים, השאר ריק).
- בלי Markdown ובלי אימוג'י."""


def parse_sections(raw):
    sections, seen = [], set()
    for block in re.split(r"^===\s*SECTION\s+", raw, flags=re.M)[1:]:
        head, _, body = block.partition("\n")
        parts = [p.strip() for p in head.strip().rstrip("=").strip().split("|")]
        sid = re.sub(r"[^a-z0-9]+", "-", parts[0].lower()).strip("-") or f"s{len(sections) + 1}"
        while sid in seen:
            sid += "-2"
        seen.add(sid)
        title = parts[1] if len(parts) > 1 and parts[1] else f"נושא {len(sections) + 1}"
        ref = parts[2] if len(parts) > 2 else ""
        if not re.fullmatch(r"\d+:\d+-\d+:\d+", ref):
            ref = ""
        text = body.strip()
        if text:
            sections.append({"id": sid, "title": title, "ref": ref, "text": text})
    return sections


def deep_word_count(sections):
    return sum(count_words(s["text"]) for s in sections)


def write_deep(parsha_he, numbered_text, words, ratio):
    target = max(150, round(words * ratio))
    # טווח רחב בכוונה: חזרה על ההעמקה עולה כסף, והאורך שלה פחות קריטי מהסיכום הקצר
    lo, hi = round(target * 0.5), round(target * 1.8)
    system = DEEP_SYSTEM.format(target=target, lo=lo, hi=hi)
    messages = [{"role": "user", "content": f"פרשת {parsha_he} ({words} מילים בנוסח המקור):\n\n{numbered_text}"}]
    best = []
    for _ in range(3):
        raw = ask_claude(system, messages, max_tokens=16000)
        sections = parse_sections(raw)
        n = deep_word_count(sections)
        print(f"העמקה: {len(sections)} נושאים, {n} מילים (יעד {target}, טווח {lo}-{hi})")
        if len(sections) >= 3:
            best = sections
            if lo <= n <= hi:
                break
            direction = "קצר" if n > hi else "הרחב"
            feedback = f"ההעמקה באורך {n} מילים, והיעד {target} (טווח {lo}-{hi}). {direction} אותה בהתאם ושמור על אותו פורמט וכללים. החזר את כל הנושאים מחדש."
        else:
            feedback = "לא קיבלתי לפחות שלושה נושאים בפורמט הנדרש (שורת === SECTION מזהה | כותרת | טווח ===). החזר שוב לפי הפורמט."
        messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": feedback}]
    if not best:
        raise RuntimeError("Claude לא החזיר העמקה בפורמט תקין")
    return best


# ----- סיכום קצר עם קישורים להעמקה -----
LINK_RE = re.compile(r"\[\[([a-z0-9-]+)\|([^\]]+)\]\]")


def plain(s):
    return LINK_RE.sub(r"\2", s)


def clean_links(s, valid):
    return LINK_RE.sub(lambda m: m.group(0) if m.group(1) in valid else m.group(2), s)


SYSTEM = """אתה כותב פירוש שבועי קצר בעברית לפרשת השבוע: סיכום של הפרשה ופרשנות קצרה.
כללים:
- האורך הוא יעד מחייב: בערך {target} מילים (בין {lo} ל-{hi}).
- בערך שני שלישים מהטקסט: סיכום נאמן של כל הפרשה לפי סדר האירועים. השליש הנוסף: פרשנות, רעיון אחד או שניים מתוך הפרשה, מבוססים על הטקסט. אם אתה מזכיר פרשן או מדרש, ציין אותו בשמו רק אם אתה בטוח בייחוס.
- הומור: קל, חם וממוקד בצד האנושי של הסיפור או בהקבלה לחיי היומיום. בלי ללגלג על הטקסט, הדמויות, אלוהים, המצוות או המסורת. חיוך או שניים מספיקים.
- אל תמציא פרטים שאינם בפרשה.
- קישורים להעמקה: לסיכום הקצר מצורפת העמקה מפורטת, מחולקת לנושאים (מזהה: כותרת):
{links}
  עטוף בתוך הסיכום ביטוי קצר (שתיים עד שש מילים, חלק טבעי מהמשפט) בסימון [[מזהה|הביטוי]], כדי לקשר לנושא הרלוונטי, למשל [[shechem|שמעון ולוי נוקמים בעיר]]. קשר כל נושא פעם אחת בדיוק, לפי סדר הפרשה. הסימון אינו נספר באורך: נספרות רק מילות הביטוי. הקישורים נמצאים בסיכום ולא בפרשנות.
- פורמט: טקסט פשוט, פסקאות קצרות המופרדות בשורה ריקה, בלי כותרות, בלי Markdown ובלי אימוג'י (שם הפרשה מוצג בנפרד).
- החזר רק את גוף הפירוש, בלי הקדמה ובלי הערות על עצמך."""


def write_summary(parsha_he, text, ratio, sections):
    words = count_words(text)
    target = max(40, round(words * ratio))
    lo, hi = round(target * 0.85), round(target * 1.15)
    ids = [s["id"] for s in sections]
    listing = "\n".join(f"  {s['id']}: {s['title']}" for s in sections)
    system = SYSTEM.format(target=target, lo=lo, hi=hi, links=listing)
    messages = [{"role": "user", "content": f"פרשת {parsha_he} ({words} מילים בנוסח המקור):\n\n{text}"}]
    draft = ""
    for _ in range(3):
        draft = ask_claude(system, messages)
        n = count_words(plain(draft))
        missing = [i for i in ids if f"[[{i}|" not in draft]
        print(f"סיכום: {n} מילים (יעד {target}, טווח {lo}-{hi}), קישורים חסרים: {missing or 'אין'}")
        if lo <= n <= hi and not missing:
            break
        notes = []
        if n > hi:
            notes.append(f"הסיכום באורך {n} מילים וצריך להיות קצר יותר (יעד {target}, טווח {lo}-{hi})")
        elif n < lo:
            notes.append(f"הסיכום באורך {n} מילים וצריך להיות ארוך יותר (יעד {target}, טווח {lo}-{hi})")
        if missing:
            notes.append("לא קישרת את הנושאים: " + ", ".join(missing))
        messages += [
            {"role": "assistant", "content": draft},
            {"role": "user", "content": ". ".join(notes) + ". תקן ושמור על אותם כללים. החזר רק את הפירוש."},
        ]
    final_words = count_words(plain(draft))
    if final_words < target * 0.5:
        raise RuntimeError(f"הסיכום הקצר קצר מדי ({final_words} מילים, יעד {target}). לא נשמר כלום, אפשר להריץ שוב.")
    return clean_links(draft, set(ids)), words, target


# ---------- הטקסט המלא של הפרשה (בלי Claude, רק העתקה מספריא) ----------
def text_path(entry_id):
    return os.path.join(os.path.dirname(DATA), "text", f"{entry_id}.json")


def save_text(entry_id, verses):
    rows = []
    for v in verses:
        c = n = None
        if v["label"]:
            c, n = (int(x) for x in v["label"].split(":"))
        rows.append({"c": c, "v": n, "t": v["display"]})
    path = text_path(entry_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")


def backfill_text(entries):
    """משלים טקסט מלא לפרשות שכבר בספרייה ועדיין אין להן קובץ טקסט."""
    for e in entries:
        if os.path.exists(text_path(e["id"])):
            continue
        try:
            save_text(e["id"], fetch_parsha_verses(e["ref"]))
            print(f"נוסף טקסט מלא לפרשת {e['he']}")
        except Exception as ex:
            print(f"אזהרה: לא הצלחתי להוסיף טקסט לפרשת {e['he']}: {ex}")


# ---------- ספרייה ----------
def make_id(parsha, today):
    slug = re.sub(r"[^a-z0-9]+", "-", parsha["en"].lower()).strip("-")
    return slug or today


def main():
    cfg = load_config()
    dry = "--dry-run" in sys.argv
    force = "--force" in sys.argv or os.environ.get("FORCE") == "1"
    regenerate = "--regenerate" in sys.argv
    now = datetime.now(TZ)
    today = now.strftime("%A").lower()
    if not dry:
        backfill_text(load_entries())
    if not (dry or force or regenerate):
        if today != cfg["publish_day"].strip().lower():
            print(f"היום {today}, יום הפרסום הוא {cfg['publish_day']} — מדלג.")
            return 0
        if now.hour < int(cfg.get("publish_hour", 0)):
            print(f"השעה {now.strftime('%H:%M')} בישראל, שעת הפרסום היא {cfg['publish_hour']}:00 — מדלג.")
            return 0

    parsha = get_parsha(cfg)
    he_name = re.sub(r"^פרשת\s+", "", parsha["he"]) or parsha["en"]
    print(f"פרשת השבוע: {he_name} ({parsha['ref']})")

    entries = load_entries()
    exists = any(e.get("ref") == parsha["ref"] for e in entries)
    if exists and not regenerate and not dry:
        print("הפירוש של פרשה זו כבר בספרייה — מדלג.")
        return 0

    verses = fetch_parsha_verses(parsha["ref"])
    plain_text = "\n".join(v["text"] for v in verses)
    numbered = "\n".join(f"{v['label']} {v['text']}" if v["label"] else v["text"] for v in verses)
    words = count_words(plain_text)

    sections = write_deep(he_name, numbered, words, cfg.get("deep_ratio", 0.3))
    summary, _, _ = write_summary(he_name, plain_text, cfg.get("summary_ratio", 0.10), sections)
    entry = {
        "id": make_id(parsha, now.date().isoformat()),
        "he": he_name,
        "en": parsha["en"],
        "ref": parsha["ref"],
        "date": now.date().isoformat(),
        "words": words,
        "summary_words": count_words(plain(summary)),
        "deep_words": deep_word_count(sections),
        "summary": summary,
        "sections": sections,
    }

    if dry:
        print(f"\nפרשת {he_name}\n\n{summary}\n")
        for s in sections:
            print(f"--- {s['title']} ({s['id']}, {s['ref']})\n{s['text']}\n")
        print(f"[פרשה: {words} מילים, סיכום: {entry['summary_words']}, העמקה: {entry['deep_words']}]")
        return 0

    entries = [e for e in entries if e.get("ref") != parsha["ref"]]
    entries.append(entry)
    save_entries(entries)
    save_text(entry["id"], verses)
    print(f"נשמר בספרייה ({len(entries)} פרשות).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
