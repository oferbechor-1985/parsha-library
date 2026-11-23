// רשימת 54 הפרשות והתאמה בין פרשה שנשמרה בספרייה לבין המקום שלה ברשימה.
(function (root) {
  "use strict";

  const BOOKS = [
    { name: "בראשית", parshiot: ["בראשית", "נח", "לך לך", "וירא", "חיי שרה", "תולדות", "ויצא", "וישלח", "וישב", "מקץ", "ויגש", "ויחי"] },
    { name: "שמות", parshiot: ["שמות", "וארא", "בא", "בשלח", "יתרו", "משפטים", "תרומה", "תצוה", "כי תשא", "ויקהל", "פקודי"] },
    { name: "ויקרא", parshiot: ["ויקרא", "צו", "שמיני", "תזריע", "מצורע", "אחרי מות", "קדושים", "אמור", "בהר", "בחוקותי"] },
    { name: "במדבר", parshiot: ["במדבר", "נשא", "בהעלותך", "שלח לך", "קורח", "חקת", "בלק", "פינחס", "מטות", "מסעי"] },
    { name: "דברים", parshiot: ["דברים", "ואתחנן", "עקב", "ראה", "שפטים", "כי תצא", "כי תבוא", "נצבים", "וילך", "האזינו", "וזאת הברכה"] },
  ];

  // ניקוי ניקוד, "פרשת", סימנים; מקף מחבר פרשות כפולות (ויקהל-פקודי)
  function norm(s) {
    return String(s || "")
      .replace(/[־–—]/g, "-")
      .replace(/[֑-ׇ]/g, "")
      .replace(/^\s*פרשת\s+/, "")
      .replace(/[^א-ת\s-]/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  // שלד עיצורי: מתעלם מכתיב מלא/חסר (תצוה/תצווה, פנחס/פינחס), משאיר את האות הראשונה
  function skel(part) {
    const t = part.replace(/\s+/g, "");
    return t.charAt(0) + t.slice(1).replace(/[וי]/g, "");
  }

  function sameSlot(a, b) {
    return a === b || (Math.min(a.length, b.length) >= 3 && (a.startsWith(b) || b.startsWith(a)));
  }

  const SLOTS = BOOKS.flatMap((b, bi) => b.parshiot.map((name, si) => ({ bi, si, key: skel(norm(name)) })));

  function slotsFor(heName) {
    const out = [];
    for (const part of norm(heName).split("-").map(skel).filter(Boolean)) {
      const hit = SLOTS.find((s) => s.key === part) || SLOTS.find((s) => sameSlot(s.key, part));
      if (hit && !out.includes(hit)) out.push(hit);
    }
    return out;
  }

  function buildLibrary(entries) {
    const books = BOOKS.map((b) => ({ name: b.name, slots: b.parshiot.map((n) => ({ name: n, entry: null })) }));
    const extra = [];
    let filled = 0;
    for (const e of entries) {
      const hits = slotsFor(e.he);
      if (!hits.length) { extra.push(e); continue; }
      for (const hit of hits) {
        const slot = books[hit.bi].slots[hit.si];
        if (!slot.entry) filled++;
        slot.entry = e;
      }
    }
    return { books, extra, filled, total: SLOTS.length };
  }

  const api = { buildLibrary, slotsFor, norm };
  root.ParshaLib = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
