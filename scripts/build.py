"""Compose the final video from WORK/slides/slide_NNN.png + WORK/audio/aNNN.wav (+ aNNN.json cues).

usage: python build.py WORK OUT.mp4 [--title T] [--subtitle S] [--kicker K] [--no-intro] [--no-subs]
                       [--zoom 0.07] [--bgm music.mp3] [--encoder libx264|h264_nvenc|h264_qsv]
                       [--jobs N] [--start-from clips|final]

Timeline is computed in whole frames; the narration is laid down as ONE track at absolute
positions, so audio, video and subtitles cannot drift apart however many slides there are.
"""
import argparse, array, glob, json, math, os, re, subprocess, sys, wave
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

FPS, W, H = 30, 1920, 1080
SR = 48000
PRE, POST = 0.6, 0.9          # silence before / after each slide's narration
TF = 21                       # transition length in frames (0.7 s)
INTRO_F = 4 * FPS
CHUNK = 10                    # xfade at most this many clips per ffmpeg call
FONT_DIR = "C:/Windows/Fonts"
TRANS = ["fadeblack", "smoothleft", "circleopen", "slideleft", "wipeleft", "smoothup",
         "fade", "radial", "slideup", "dissolve"]
MID = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", "-an"]


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        print(" ".join(cmd)[:500]); print(r.stderr[-3000:]); sys.exit(1)


def wav_len(p):
    with wave.open(p, "rb") as w:
        return w.getnframes() / w.getframerate()


def font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)


def intro_card(first_slide, out, title, subtitle, kicker):
    bg = ImageOps.fit(Image.open(first_slide).convert("RGB"), (W, H)).filter(ImageFilter.GaussianBlur(18))
    img = Image.alpha_composite(bg.convert("RGBA"), Image.new("RGBA", (W, H), (14, 30, 60, 205)))
    d = ImageDraw.Draw(img)
    fb, fm, fs = font("msjhbd.ttc", 92), font("msjh.ttc", 44), font("msjhl.ttc", 36)
    while d.textlength(title, font=fb) > W - 200 and fb.size > 40:
        fb = font("msjhbd.ttc", fb.size - 4)
    while subtitle and d.textlength(subtitle, font=fm) > W - 200 and fm.size > 24:
        fm = font("msjh.ttc", fm.size - 2)

    def ctext(y, s, f, fill):
        d.text(((W - d.textlength(s, font=f)) / 2, y), s, font=f, fill=fill)
    if kicker:
        ctext(330, kicker, fs, (150, 190, 240))
    ctext(420, title, fb, (255, 255, 255))
    d.rectangle([(W / 2 - 90, 565), (W / 2 + 90, 571)], fill=(255, 170, 60))
    if subtitle:
        ctext(615, subtitle, fm, (220, 230, 245))
    img.convert("RGB").save(out)


def moves(zm):
    e = "(1-cos(PI*({on})/{n}))/2"   # cosine ease 0 -> 1; {on} = global frame within the slide
    return [
        (f"1+{zm}*{e}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),            # zoom in
        (f"{1 + zm}-{zm}*{e}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),     # zoom out
        (f"{1 + zm * 0.85}", f"(iw-iw/zoom)*{e}", "(ih-ih/zoom)/2"),       # pan L->R
        (f"1+{zm * 0.85}*{e}", "(iw-iw/zoom)*0.35", "(ih-ih/zoom)*0.4"),   # zoom to upper-left
        (f"{1 + zm * 0.85}", f"(iw-iw/zoom)*(1-{e})", "(ih-ih/zoom)/2"),   # pan R->L
    ]


SF = 9   # crossfade between effect states of one slide (0.3 s)


def render_clip(work, imgs, out, nf, mv):
    """imgs: [(start_frame, image)] (first start 0). One zoom/pan runs continuously over the whole
    slide; each state is rendered as its own segment and states are crossfaded."""
    pre = "scale=3840:2160:force_original_aspect_ratio=decrease,pad=3840:2160:(ow-iw)/2:(oh-ih)/2:black,"
    segs = []
    for j, (st, img) in enumerate(imgs):
        end = imgs[j + 1][0] + SF if j + 1 < len(imgs) else nf
        z, x, y = (s.format(n=nf, on=f"on+{st}") for s in mv)
        vf = f"{pre}zoompan=z='{z}':x='{x}':y='{y}':d={end - st}:s={W}x{H}:fps={FPS},format=yuv420p"
        seg = out if len(imgs) == 1 else out[:-4] + f"_s{j}.mp4"
        run(["ffmpeg", "-y", "-i", img, "-vf", vf, "-frames:v", str(end - st), "-threads", "2", *MID, seg], cwd=work)
        segs.append((seg, end - st))
    if len(imgs) > 1:
        f, n = xfade_chain(work, [s for s, _ in segs], [n for _, n in segs], os.path.basename(out)[:-4],
                           tf=SF, trans=["fade"])
        assert n == nf, (n, nf)
        os.replace(os.path.join(work, f), os.path.join(work, out))


def xfade_chain(work, clips, nfs, tag, tidx=0, tf=TF, trans=TRANS):
    """Join clips with xfade; returns (file, frames). Recurses in chunks to keep filter graphs small."""
    if len(clips) == 1:
        return clips[0], nfs[0]
    if len(clips) > CHUNK:
        parts, pf = [], []
        for c in range(0, len(clips), CHUNK):
            f, n = xfade_chain(work, clips[c:c + CHUNK], nfs[c:c + CHUNK], f"{tag}_{c // CHUNK}", tidx + c, tf, trans)
            parts.append(f); pf.append(n)
        return xfade_chain(work, parts, pf, tag + "_m", tidx + 7, tf, trans)
    cmd = ["ffmpeg", "-y"]
    for c in clips:
        cmd += ["-i", c]
    fc, prev, acc = [], "0:v", 0
    for k in range(1, len(clips)):
        acc += nfs[k - 1] - tf
        fc.append(f"[{prev}][{k}:v]xfade=transition={trans[(tidx + k - 1) % len(trans)]}"
                  f":duration={tf / FPS:.4f}:offset={acc / FPS:.4f}[x{k}]")
        prev = f"x{k}"
    total = sum(nfs) - tf * (len(clips) - 1)
    out = f"clips/join_{tag}.mp4"
    run(cmd + ["-filter_complex", ";".join(fc), "-map", f"[{prev}]", "-frames:v", str(total), *MID, out], cwd=work)
    return out, total


def ass_text(s):
    return s.replace("\\", "＼").replace("{", "｛").replace("}", "｝")


def chunks(text, maxlen=24):
    parts = [p for p in re.split(r"(?<=[，；：、。？！;!?])|(?<=[,:])(?!\d)", text.strip()) if p]
    pieces = []
    for p in parts:                       # hard-split very long clauses
        while len(p) > maxlen + 6:
            cut = p.rfind(" ", 0, maxlen)
            cut = cut if cut > maxlen // 2 else maxlen
            pieces.append(p[:cut]); p = p[cut:].lstrip()
        pieces.append(p)
    out, cur = [], ""
    for p in pieces:
        if not cur or len(cur) + len(p) <= maxlen:
            cur += p
        else:
            out.append(cur); cur = p
    if cur:
        out.append(cur)
    return out


def ts_ass(t):
    cs = max(0, round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def ts_srt(t):
    ms = max(0, round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work"); ap.add_argument("out")
    ap.add_argument("--title", default=""); ap.add_argument("--subtitle", default="")
    ap.add_argument("--kicker", default="")
    ap.add_argument("--no-intro", action="store_true"); ap.add_argument("--no-subs", action="store_true")
    ap.add_argument("--zoom", type=float, default=0.07)
    ap.add_argument("--bgm", default="")
    ap.add_argument("--encoder", default="libx264")
    ap.add_argument("--jobs", type=int, default=max(1, min(8, (os.cpu_count() or 4) // 2)))
    ap.add_argument("--start-from", default="clips", choices=["clips", "final"])
    ap.add_argument("--no-effects", action="store_true", help="ignore effects.json (show/focus markers)")
    a = ap.parse_args()

    work, out = os.path.abspath(a.work), os.path.abspath(a.out)
    os.makedirs(os.path.join(work, "clips"), exist_ok=True)
    slides = sorted(p for p in glob.glob(os.path.join(work, "slides", "slide_*.png"))
                    if re.fullmatch(r"slide_\d+\.png", os.path.basename(p)))
    n = len(slides)
    audios = [os.path.join(work, "audio", f"a{i:03d}.wav") for i in range(1, n + 1)]
    missing = [p for p in audios if not os.path.exists(p)]
    if n == 0 or missing:
        sys.exit(f"{n} slides but missing audio: {missing[:3]} … run tts first (slide/script counts must match)")

    # ----- timeline in frames -----
    items = []  # (kind, image, frames, audio)
    if not a.no_intro:
        items.append(("intro", "intro.png", INTRO_F, None))
    for img, au in zip(slides, audios):
        nf = max(round((PRE + wav_len(au) + POST) * FPS), 2 * TF + FPS)
        items.append(("slide", img, nf, au))
    starts, acc = [], 0
    for it in items:
        starts.append(acc); acc += it[2] - TF
    total_f = sum(it[2] for it in items) - TF * (len(items) - 1)
    total = total_f / FPS
    print(f"{n} slides, expected length {total:.2f}s ({total / 60:.1f} min)", flush=True)

    # ----- clips (parallel) -----
    clips = [f"clips/c{k:03d}.mp4" for k in range(len(items))]
    if a.start_from == "clips":
        if not a.no_intro:
            intro_card(slides[0], os.path.join(work, "intro.png"), a.title or "簡報", a.subtitle, a.kicker)
        mv = moves(a.zoom)
        effects = {}
        ep = os.path.join(work, "effects.json")
        if os.path.exists(ep) and not a.no_effects:
            with open(ep, encoding="utf-8") as f:
                effects = json.load(f)
        jobs = []
        for k, (kind, img, nf, _) in enumerate(items):
            if kind == "intro":
                jobs.append(([(0, img)], clips[k], nf,
                             ("1+0.04*(1-cos(PI*({on})/{n}))/2", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)")))
                continue
            si = k + (1 if a.no_intro else 0)          # 1-based slide number
            imgs = [(0, img)]
            if str(si) in effects:
                imgs = []
                for st in effects[str(si)]:
                    f0 = min(max(0, round((PRE + st["t"]) * FPS)), nf - SF - 2) if imgs else 0
                    if imgs and f0 - imgs[-1][0] < SF + 2:
                        imgs[-1] = (imgs[-1][0], st["img"])     # too close: keep the later state
                    else:
                        imgs.append((f0, st["img"]))
            jobs.append((imgs, clips[k], nf, mv[(si - 1) % len(mv)]))
        done = 0
        with ThreadPoolExecutor(a.jobs) as ex:
            for _ in ex.map(lambda j: render_clip(work, *j), jobs):
                done += 1; print(f"clip {done}/{len(jobs)}", flush=True)

    video, vf_frames = xfade_chain(work, clips, [it[2] for it in items], "all")
    assert vf_frames == total_f, (vf_frames, total_f)

    # ----- narration as one track at absolute positions -----
    buf = array.array("h", bytes(int(math.ceil(total * SR)) * 2))
    for k, (kind, _, _, au) in enumerate(items):
        if kind != "slide":
            continue
        pos = round((starts[k] / FPS + PRE) * SR)
        with wave.open(au, "rb") as w:
            pcm = array.array("h"); pcm.frombytes(w.readframes(w.getnframes()))
        end = min(len(buf), pos + len(pcm))
        buf[pos:end] = pcm[:end - pos]
    with wave.open(os.path.join(work, "narration.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(buf.tobytes())

    # ----- subtitles: burned ASS + sidecar SRT -----
    subs = []
    for k, (kind, _, _, au) in enumerate(items):
        if kind != "slide":
            continue
        base = starts[k] / FPS + PRE
        with open(au[:-4] + ".json", encoding="utf-8") as f:
            cues = json.load(f)
        for c in cues:
            segs = chunks(c["text"]); tot = sum(len(s) for s in segs) or 1
            t0, span = c["start"], c["end"] - c["start"]
            for s in segs:
                t1 = t0 + span * len(s) / tot
                subs.append((base + t0, base + t1 - 0.02, s.rstrip("，；：、。,;:")))
                t0 = t1
    srt = os.path.splitext(out)[0] + ".srt"
    with open(srt, "w", encoding="utf-8") as f:
        for i, (s, e, t) in enumerate(subs, 1):
            f.write(f"{i}\n{ts_srt(s)} --> {ts_srt(e)}\n{t}\n\n")
    if not a.no_subs:
        head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\n\n"
                "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
                "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
                "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                "Style: Sub,Microsoft JhengHei,50,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90201810,"
                "1,0,0,0,100,100,1,0,3,14,0,2,80,80,46,1\n\n"
                "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        with open(os.path.join(work, "subs.ass"), "w", encoding="utf-8") as f:
            f.write(head + "".join(f"Dialogue: 0,{ts_ass(s)},{ts_ass(e)},Sub,,0,0,0,,{ass_text(t)}\n"
                                   for s, e, t in subs))

    # ----- chapters (mp4 chapters + YouTube-style list) and timeline for qa.py -----
    titles = {}
    mp = os.path.join(work, "slides", "slides.json")
    if os.path.exists(mp):
        with open(mp, encoding="utf-8-sig") as f:
            titles = {m["n"]: (m.get("title") or "").strip() for m in json.load(f)}
    chap, si = [], 0
    for k, (kind, _, nf, _) in enumerate(items):
        if kind == "intro":
            chap.append((starts[k] / FPS, a.title or "片頭"))
        else:
            si += 1
            chap.append((starts[k] / FPS if chap else 0.0, titles.get(si) or f"第 {si} 頁"))
    chap[0] = (0.0, chap[0][1])

    def meta_esc(s):
        return re.sub(r"([=;#\\\n])", r"\\\1", s)
    with open(os.path.join(work, "chapters.ffmeta"), "w", encoding="utf-8") as f:
        f.write(";FFMETADATA1\n")
        for c, (t, name) in enumerate(chap):
            end = chap[c + 1][0] if c + 1 < len(chap) else total
            f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={round(t * 1000)}\nEND={round(end * 1000)}\n"
                    f"title={meta_esc(name)}\n")
    chapters_txt = os.path.splitext(out)[0] + "_chapters.txt"
    with open(chapters_txt, "w", encoding="utf-8") as f:
        for t, name in chap:
            f.write(f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{int(t % 60):02d} {name}\n" if total >= 3600
                    else f"{int(t // 60):02d}:{int(t % 60):02d} {name}\n")
    with open(os.path.join(work, "timeline.json"), "w", encoding="utf-8") as f:
        json.dump({"fps": FPS, "total": total, "pre": PRE,
                   "slides": [{"n": i + 1, "start": starts[k] / FPS, "frames": it[2]}
                              for i, (k, it) in enumerate((k, it) for k, it in enumerate(items) if it[0] == "slide")]},
                  f, indent=1)

    # ----- final pass -----
    cmd = ["ffmpeg", "-y", "-i", video, "-i", "narration.wav",
           "-f", "lavfi", "-i", f"color=c=0xFFA53C:s={W}x10:r={FPS}"]
    if a.bgm:
        cmd += ["-stream_loop", "-1", "-i", os.path.abspath(a.bgm)]
    meta_idx = 4 if a.bgm else 3
    cmd += ["-f", "ffmetadata", "-i", "chapters.ffmeta"]
    v = "[0:v]"
    fc = []
    if not a.no_subs:
        fc.append("[0:v]ass=subs.ass[vs]"); v = "[vs]"
    fc.append(f"{v}[2:v]overlay=x='-w+w*t/{total:.3f}':y=H-h:shortest=1,"
              f"fade=t=out:st={total - 1.2:.3f}:d=1.2[vout]")
    fc.append("[1:a]aformat=sample_rates=48000:channel_layouts=stereo[na]")
    aud = "[na]"
    if a.bgm:
        fc.append(f"[3:a]aresample=48000,aformat=channel_layouts=stereo,volume=0.12,atrim=0:{total:.3f}[bg]")
        fc.append("[na][bg]amix=inputs=2:duration=first:normalize=0[mix]"); aud = "[mix]"
    fc.append(f"{aud}afade=t=out:st={total - 1.2:.3f}:d=1.2,loudnorm=I=-16:TP=-1.5,aresample=48000[aout]")
    venc = {"libx264": ["-c:v", "libx264", "-preset", "medium", "-crf", "20"],
            "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21"],
            "h264_qsv": ["-c:v", "h264_qsv", "-global_quality", "21"]}[a.encoder]
    cmd += ["-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]", "-map_chapters", str(meta_idx),
            *venc, "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}", out]
    run(cmd, cwd=work)
    print(f"done: {out}  ({total / 60:.1f} min)\nsubtitles: {srt}\nchapters: {chapters_txt}")


if __name__ == "__main__":
    main()
