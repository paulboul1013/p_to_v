"""Extract slide text + speaker notes of the *visible* slides.
Writes WORK/slides_text.json and a draft WORK/script.json (one narration string per slide).

usage: python extract_text.py deck.pptx WORK [--slides N]
"""
import argparse, json, os, re
from pptx import Presentation


def shape_texts(shapes):
    for sh in shapes:
        if sh.shape_type == 6:  # group
            yield from shape_texts(sh.shapes)
        elif getattr(sh, "has_table", False) and sh.has_table:
            for r in sh.table.rows:
                yield " | ".join(c.text.strip() for c in r.cells)
        elif sh.has_text_frame and sh.text_frame.text.strip():
            yield sh.text_frame.text.strip()


def join_lines(t):
    # newline -> space between two ASCII word chars, otherwise just drop it (CJK)
    t = re.sub(r"(?<=[A-Za-z0-9])\s*\n\s*(?=[A-Za-z0-9])", " ", t)
    return re.sub(r"\s*\n\s*", "", t).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pptx"); ap.add_argument("work")
    ap.add_argument("--slides", type=int, default=0)
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    prs = Presentation(a.pptx)
    info, script, hidden = [], [], []
    for orig, s in enumerate(prs.slides, 1):
        if s._element.get("show") == "0":
            hidden.append(orig); continue
        if a.slides and len(info) >= a.slides:
            break
        texts = list(shape_texts(s.shapes))
        notes = s.notes_slide.notes_text_frame.text.strip() if s.has_notes_slide else ""
        info.append({"slide": len(info) + 1, "original_index": orig, "text": texts, "notes": notes})
        draft = notes if notes else "。".join(t.replace("\n", "，") for t in texts)
        script.append(join_lines(draft))
    with open(os.path.join(a.work, "slides_text.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    sp = os.path.join(a.work, "script.json")
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=1)
    empty = [i + 1 for i, s in enumerate(script) if not s]
    print(f"{len(script)} slides; notes on {sum(bool(x['notes']) for x in info)}; draft -> {sp}")
    if hidden: print("skipped hidden slides:", hidden)
    if empty: print("slides with no text at all (will be silent unless you write narration):", empty)


if __name__ == "__main__":
    main()
