"""Visual effects driven by markers in the narration script.

Markers (written by Claude into script.json, placed at the start of the sentence they belong to):
  [[show:S3]]          reveal shape S3 when this sentence starts (it is hidden before)
  [[show:S3.2,S3.3]]   reveal paragraphs 2 and 3 of shape S3 (bullet-by-bullet build)
  [[show:0.52,0.30,0.40,0.25]]   reveal a region x,y,w,h (0..1 of slide) - for slides that are one big picture
  [[focus:S4]] / [[focus:S3.2]] / [[focus:0.1,0.2,0.3,0.2]]   spotlight that element/region from here on
  [[focus:off]]        remove the spotlight
  several items in one marker are separated by "|", e.g. [[show:S3|0.5,0.5,0.4,0.3]]

usage:
  python effects.py WORK --grids     # draw WORK/grid/slide_NNN.png (0.1 grid + shape ids) to help pick boxes
  python effects.py WORK             # after tts: build state images -> WORK/effects.json
"""
import argparse, glob, json, os, re, statistics, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
LEAD = 0.15          # effects start slightly before the sentence
BOX = re.compile(r"^\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*$")


def load_meta(work):
    p = os.path.join(work, "slides", "slides.json")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8-sig") as f:
        return json.load(f)


def id_boxes(slide_meta):
    m = {}
    for s in slide_meta.get("shapes", []) or []:
        m[s["id"].upper()] = s["box"]
        for p in s.get("paras", []) or []:
            m[p["id"].upper()] = p["box"]
    return m


def parse_items(arg):
    items = []
    for part in [p for p in arg.split("|") if p.strip()]:
        b = BOX.match(part)
        if b:
            items.append(("box", tuple(round(float(v), 4) for v in b.groups())))
        else:
            items += [("id", t.upper()) for t in re.split(r"[,\s]+", part.strip()) if t]
    return items


# ---------------------------------------------------------------- grids
def make_grids(work):
    meta = {m["n"]: m for m in load_meta(work)}
    out = os.path.join(work, "grid"); os.makedirs(out, exist_ok=True)
    f = ImageFont.truetype("C:/Windows/Fonts/msjhbd.ttc", 22)
    fs = ImageFont.truetype("C:/Windows/Fonts/msjh.ttc", 18)
    for p in sorted(glob.glob(os.path.join(work, "slides", "slide_*.png"))):
        n = int(re.search(r"(\d+)", os.path.basename(p)).group(1))
        im = Image.open(p).convert("RGB")
        W = 1600; H = round(im.height * W / im.width)
        im = im.resize((W, H)).convert("RGBA")
        ov = Image.new("RGBA", im.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
        for k in range(1, 10):
            x, y = W * k / 10, H * k / 10
            d.line([(x, 0), (x, H)], fill=(255, 0, 0, 90), width=1)
            d.line([(0, y), (W, y)], fill=(255, 0, 0, 90), width=1)
            d.text((x + 3, 2), f".{k}", font=f, fill=(220, 0, 0, 255))
            d.text((3, y + 2), f".{k}", font=f, fill=(220, 0, 0, 255))
        for s in meta.get(n, {}).get("shapes", []) or []:
            bx, by, bw, bh = s["box"]
            d.rectangle([bx * W, by * H, (bx + bw) * W, (by + bh) * H], outline=(0, 90, 255, 200), width=2)
            d.text((bx * W + 4, by * H + 2), s["id"], font=fs, fill=(0, 70, 230, 255))
        Image.alpha_composite(im, ov).convert("RGB").save(os.path.join(out, f"slide_{n:03d}.png"))
    print(f"grid images -> {out}")


# ---------------------------------------------------------------- painting
def paint_hidden(im, box):
    """Cover a region with the colour sampled just around it (works on plain backgrounds)."""
    W, H = im.size
    x, y, w, h = box
    pad = 0.004 * W
    L, T, R, B = max(0, x * W - pad), max(0, y * H - pad), min(W, (x + w) * W + pad), min(H, (y + h) * H + pad)
    ring = 6
    px = im.load(); samples = []
    for xx in range(int(L), int(R), 4):
        for yy in (int(T) - ring, int(B) + ring):
            if 0 <= yy < H: samples.append(px[xx, yy][:3])
    for yy in range(int(T), int(B), 4):
        for xx in (int(L) - ring, int(R) + ring):
            if 0 <= xx < W: samples.append(px[xx, yy][:3])
    col = tuple(int(statistics.median(c[i] for c in samples)) for i in range(3)) if samples else (255, 255, 255)
    ImageDraw.Draw(im).rectangle([L, T, R, B], fill=col)


def spotlight(im, box):
    W, H = im.size
    x, y, w, h = box
    pad, rad = 0.008 * W, 0.01 * W
    L, T, R, B = x * W - pad, y * H - pad, (x + w) * W + pad, (y + h) * H + pad
    dim = Image.new("RGBA", im.size, (10, 15, 30, 115))
    hole = Image.new("L", im.size, 255)
    ImageDraw.Draw(hole).rounded_rectangle([L, T, R, B], radius=rad, fill=0)
    dim.putalpha(Image.eval(hole, lambda v: v * 115 // 255))
    out = Image.alpha_composite(im.convert("RGBA"), dim)
    ImageDraw.Draw(out).rounded_rectangle([L, T, R, B], radius=rad, outline=(255, 165, 60, 255),
                                          width=max(4, round(0.0025 * W)))
    return out.convert("RGB")


# ---------------------------------------------------------------- main
def build_effects(work):
    meta = {m["n"]: m for m in load_meta(work)}
    slides = sorted(glob.glob(os.path.join(work, "slides", "slide_*.png")))
    if not slides:
        sys.exit("no slides")
    size = Image.open(slides[0]).size
    sdir = os.path.join(work, "states"); os.makedirs(sdir, exist_ok=True)
    for f in glob.glob(os.path.join(sdir, "*.png")):
        os.remove(f)
    effects, com_jobs, plans, warnings = {}, [], {}, []

    for i in range(1, len(slides) + 1):
        cue_p = os.path.join(work, "audio", f"a{i:03d}.json")
        if not os.path.exists(cue_p):
            continue
        with open(cue_p, encoding="utf-8") as f:
            cues = json.load(f)
        boxes = id_boxes(meta.get(i, {}))
        events, hidden = [], set()
        marks = [(max(0.0, c["start"] - LEAD), k, v) for c in cues for k, v in c.get("marks", [])] + \
                [(c["end"] + 0.2, k, v) for c in cues for k, v in c.get("end_marks", [])]
        for t, kind, arg in marks:
            if kind == "show":
                its = [it for it in parse_items(arg) if it[0] == "box" or it[1] in boxes or warnings.append(
                    f"slide {i}: unknown id {it[1]}")]
                hidden |= set(its); events.append((t, "show", its))
            elif kind == "focus":
                if arg.strip().lower() in ("", "off", "none"):
                    events.append((t, "focus", None))
                else:
                    it = parse_items(arg)[0]
                    b = it[1] if it[0] == "box" else boxes.get(it[1])
                    if b is None:
                        warnings.append(f"slide {i}: unknown id {it[1]}"); continue
                    events.append((t, "focus", tuple(b)))
            else:
                warnings.append(f"slide {i}: unknown marker [[{kind}:…]]")
        if not events:
            continue
        segs = [[0.0, frozenset(hidden), None]]
        for t, kind, val in sorted(events, key=lambda e: e[0]):
            h, fo = set(segs[-1][1]), segs[-1][2]
            if kind == "show":
                h -= set(val)
            else:
                fo = val
            if t - segs[-1][0] < 0.3:
                segs[-1][1:] = [frozenset(h), fo]
            else:
                segs.append([t, frozenset(h), fo])
        plans[i] = segs
        for _, h, _ in segs:
            ids = sorted(x[1] for x in h if x[0] == "id")
            if ids:
                key = (i, tuple(ids))
                if key not in {(j["n"], tuple(j["hide"])) for j in com_jobs}:
                    com_jobs.append({"n": i, "orig": meta[i]["orig"], "hide": ids, "w": size[0], "h": size[1],
                                     "out": os.path.join(sdir, f"s{i:03d}_h{len(com_jobs)}.png")})

    if com_jobs:
        jp = os.path.join(work, "state_jobs.json")
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(com_jobs, f, ensure_ascii=False)
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            os.path.join(HERE, "export_states.ps1"), "-Deck", os.path.join(work, "_deck_copy.pptx"),
                            "-Jobs", jp])
        if r.returncode:
            sys.exit("state export failed")
    base_of = {(j["n"], tuple(j["hide"])): j["out"] for j in com_jobs}

    for i, segs in plans.items():
        lst = []
        for k, (t, h, fo) in enumerate(segs):
            ids = tuple(sorted(x[1] for x in h if x[0] == "id"))
            base = base_of.get((i, ids), os.path.join(work, "slides", f"slide_{i:03d}.png"))
            regions = [x[1] for x in h if x[0] == "box"]
            if not regions and fo is None:
                img = base
            else:
                im = Image.open(base).convert("RGB")
                for b in regions:
                    paint_hidden(im, b)
                if fo is not None:
                    im = spotlight(im, fo)
                img = os.path.join(sdir, f"s{i:03d}_seg{k}.png"); im.save(img)
            lst.append({"t": round(t, 3), "img": os.path.relpath(img, work)})
        effects[str(i)] = lst
        print(f"slide {i}: {len(lst)} states")
    with open(os.path.join(work, "effects.json"), "w", encoding="utf-8") as f:
        json.dump(effects, f, ensure_ascii=False, indent=1)
    for w in warnings:
        print("WARNING:", w)
    print(f"effects on {len(effects)} slides -> effects.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("work"); ap.add_argument("--grids", action="store_true")
    a = ap.parse_args()
    make_grids(os.path.abspath(a.work)) if a.grids else build_effects(os.path.abspath(a.work))
