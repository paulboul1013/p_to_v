"""Narrate WORK/script.json one sentence per request, with automatic engine fallback
(edge -> offline Windows voice; see tts_engines.py).

For slide i writes WORK/audio/a{i:03d}.wav (48 kHz mono) and a{i:03d}.json (exact sentence cues).
- sentences are joined with controlled pauses (SSML <break> is no longer allowed by edge-tts)
- a lexicon rewrites the *spoken* text only; subtitles keep the original wording
- synthesized sentences are cached by sha1(engine|voice|rate|spoken text) in WORK/tts_cache
- retries with exponential backoff; a dead engine is detected by a 1-sentence probe and skipped
- the engine actually used is written to WORK/audio/engine.txt

usage: python tts.py WORK [--engine auto|edge|windows] [--voice zh-TW-HsiaoChenNeural]
                          [--rate +8%] [--lexicon extra.json]
"""
import argparse, array, asyncio, hashlib, json, os, re, sys, wave
from tts_engines import ENGINES, ORDER

SR = 48000
HERE = os.path.dirname(os.path.abspath(__file__))
SPLIT = re.compile(r"(?<=[。！？；!?;])")
GAP_LONG, GAP_SHORT = 0.45, 0.30     # pause after 。！？ / after ；
EMPTY_SLIDE = 2.0                    # seconds of silence for a slide with no narration


MARK = re.compile(r"\[\[\s*(\w+)\s*:([^\]]*)\]\]")


def sentences(text):
    """-> [(display_text, marks, end_marks)]. Visual markers like [[focus:S3]] / [[show:S2.1]] are
    removed from the text and fire when the sentence they precede starts; markers after the last
    sentence fire when that sentence ends."""
    out, pending = [], []
    for raw in SPLIT.split(text or ""):
        marks = [[k.lower(), v.strip()] for k, v in MARK.findall(raw)]
        clean = re.sub(r"\s{2,}", " ", MARK.sub("", raw)).strip()
        if clean and re.search(r"\w", clean):
            out.append((clean, pending + marks, [])); pending = []
        else:
            pending += marks
    if pending and out:
        out[-1][2].extend(pending)
    return out


def load_lexicon(paths):
    rules = []
    for p in paths:
        if p and os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                for r in json.load(f):
                    rules.append((re.compile(r["pattern"]), r["replace"]))
    return rules


def speakable(s, rules):
    for pat, rep in rules:
        s = pat.sub(rep, s)
    return s


def read_wav(p):
    with wave.open(p, "rb") as w:
        a = array.array("h"); a.frombytes(w.readframes(w.getnframes()))
    return a


def write_wav(p, pcm):
    with wave.open(p, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())


def build_plan(script, rules, engine, cache):
    plan, jobs = [], {}
    vid, rate = engine.voice_id(), engine.cfg["rate"]
    prefix = "" if engine.name == "edge" else engine.name + "|"   # keeps old edge caches valid
    for text in script:
        items = []
        for s, marks, end_marks in sentences(text):
            spoken = speakable(s, rules)
            key = hashlib.sha1(f"{prefix}{vid}|{rate}|{spoken}".encode("utf-8")).hexdigest()[:16]
            wp = os.path.join(cache, key + ".wav")
            if not os.path.exists(wp):
                jobs[wp] = spoken
            items.append((s, wp, marks, end_marks))
        plan.append(items)
    return plan, jobs


async def probe(engine, cache):
    """Quick 1-sentence test so a dead service is detected in seconds, not after 5 retries x N sentences."""
    wp = os.path.join(cache, f"_probe_{engine.name}.wav")
    try:
        if engine.name == "edge":
            await engine.synth_all({wp: "測試。"}, tries=2)
        else:
            await engine.synth_all({wp: "測試。"})
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        if os.path.exists(wp):
            os.remove(wp)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("--engine", default="auto", choices=["auto"] + ORDER,
                    help="auto = edge, and the offline Windows voice if edge fails")
    ap.add_argument("--voice", default="zh-TW-HsiaoChenNeural", help="edge voice")
    ap.add_argument("--win-voice", default="Microsoft Hanhan Desktop", help="Windows SAPI voice")
    ap.add_argument("--rate", default="+8%")
    ap.add_argument("--lexicon", default="")
    ap.add_argument("--concurrency", type=int, default=2)
    a = ap.parse_args()
    work = os.path.abspath(a.work)
    with open(os.path.join(work, "script.json"), encoding="utf-8") as f:
        script = json.load(f)
    rules = load_lexicon([os.path.join(HERE, "..", "lexicon.json"), os.path.join(work, "lexicon.json"), a.lexicon])
    cache = os.path.join(work, "tts_cache"); os.makedirs(cache, exist_ok=True)
    out = os.path.join(work, "audio"); os.makedirs(out, exist_ok=True)

    cfg = {"voice": a.voice, "win_voice": a.win_voice, "rate": a.rate, "concurrency": a.concurrency}
    order = ORDER if a.engine == "auto" else [a.engine]
    plan, used, tried = None, None, []
    for name in order:
        eng = ENGINES[name](cfg)
        ok, why = eng.available()
        if not ok:
            tried.append(f"{name}: skipped ({why})"); continue
        plan, jobs = build_plan(script, rules, eng, cache)
        total = sum(len(x) for x in plan)
        if jobs:
            ok, why = await probe(eng, cache)
            if not ok:
                tried.append(f"{name}: failed test ({why})"); print(f"[tts] {name} unavailable: {why}", flush=True)
                continue
        print(f"[tts] engine={name}  {total} sentences, {len(jobs)} to synthesize ({total - len(jobs)} cached)",
              flush=True)
        try:
            await eng.synth_all(jobs)
            used = name; break
        except Exception as e:
            tried.append(f"{name}: failed mid-run ({e})"); print(f"[tts] {name} failed: {e}", flush=True)
    if not used:
        sys.exit("all TTS engines failed:\n  " + "\n  ".join(tried))
    if used != order[0]:
        print(f"[tts] WARNING: fell back to '{used}' (voice differs from the default). Tried:\n  "
              + "\n  ".join(tried), flush=True)
    for f in os.listdir(out):            # only now: a failed run keeps the previous audio
        os.remove(os.path.join(out, f))
    with open(os.path.join(out, "engine.txt"), "w", encoding="utf-8") as f:
        f.write(used + "\n")

    for i, items in enumerate(plan, 1):
        pcm, cues = array.array("h"), []
        for j, (disp, wp, marks, end_marks) in enumerate(items):
            st = len(pcm) / SR
            pcm.extend(read_wav(wp))
            cue = {"start": round(st, 3), "end": round(len(pcm) / SR, 3), "text": disp}
            if marks:
                cue["marks"] = marks
            if end_marks:
                cue["end_marks"] = end_marks
            cues.append(cue)
            if j < len(items) - 1:
                gap = GAP_SHORT if disp[-1] in "；;" else GAP_LONG
                pcm.extend(array.array("h", bytes(int(gap * SR) * 2)))
        if not items:
            pcm = array.array("h", bytes(int(EMPTY_SLIDE * SR) * 2))
        write_wav(os.path.join(out, f"a{i:03d}.wav"), pcm)
        with open(os.path.join(out, f"a{i:03d}.json"), "w", encoding="utf-8") as f:
            json.dump(cues, f, ensure_ascii=False, indent=1)
        print(f"slide {i}: {len(items)} sentences, {len(pcm) / SR:.1f}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
