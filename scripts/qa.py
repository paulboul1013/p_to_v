"""Quality checks for a finished p_to_v video.

usage: python qa.py WORK OUT.mp4
Checks duration vs. the planned timeline, loudness (EBU R128), subtitle bounds, and writes
WORK/qa_contact.png (one frame from the middle of every slide) and, if effects were used,
WORK/qa_effects.png (one frame shortly after every effect state starts) for visual review.
"""
import json, os, re, subprocess, sys, tempfile
from PIL import Image, ImageDraw, ImageFont

TW, COLS = 480, 4


def probe_dur(p):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "csv=p=0", p]).strip())


def grab(video, t, path):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1",
                    "-vf", f"scale={TW}:-1", path], check=True)


def sheet(frames, out):
    font = ImageFont.truetype("C:/Windows/Fonts/msjhbd.ttc", 20)
    ims = [(Image.open(p), label) for p, label in frames]
    th = ims[0][0].height
    rows = (len(ims) + COLS - 1) // COLS
    S = Image.new("RGB", (COLS * TW + (COLS + 1) * 8, rows * (th + 34) + 8), (30, 30, 30))
    d = ImageDraw.Draw(S)
    for i, (im, label) in enumerate(ims):
        x, y = 8 + (i % COLS) * (TW + 8), 8 + (i // COLS) * (th + 34)
        d.text((x, y), label, font=font, fill=(255, 200, 120))
        S.paste(im, (x, y + 26))
    S.save(out)


def main():
    work, video = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
    with open(os.path.join(work, "timeline.json"), encoding="utf-8") as f:
        tl = json.load(f)
    fps, problems = tl["fps"], []

    dur = probe_dur(video)
    print(f"duration: {dur:.2f}s (planned {tl['total']:.2f}s)")
    if abs(dur - tl["total"]) > 0.15:
        problems.append(f"duration differs from plan by {dur - tl['total']:+.2f}s")

    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", video, "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    if m:
        lufs = float(m[-1]); print(f"loudness: {lufs:.1f} LUFS (target -16)")
        if abs(lufs + 16) > 2:
            problems.append(f"loudness {lufs:.1f} LUFS is far from -16")

    srt = os.path.splitext(video)[0] + ".srt"
    if os.path.exists(srt):
        with open(srt, encoding="utf-8") as f:
            ends = re.findall(r"--> (\d+):(\d+):(\d+),(\d+)", f.read())
        if ends:
            h, mi, s, ms = map(int, ends[-1])
            last = h * 3600 + mi * 60 + s + ms / 1000
            print(f"last subtitle ends at {last:.2f}s")
            if last > dur:
                problems.append("subtitles run past the end of the video")

    tmp = tempfile.mkdtemp(prefix="p2v_qa_")
    frames = []
    for s in tl["slides"]:
        t = s["start"] + s["frames"] / fps / 2
        p = os.path.join(tmp, f"m{s['n']:03d}.png"); grab(video, t, p)
        frames.append((p, f"#{s['n']}  {int(t // 60)}:{t % 60:04.1f}"))
    sheet(frames, os.path.join(work, "qa_contact.png"))
    print(f"contact sheet: {os.path.join(work, 'qa_contact.png')}")

    ep = os.path.join(work, "effects.json")
    if os.path.exists(ep):
        with open(ep, encoding="utf-8") as f:
            eff = json.load(f)
        span = {s["n"]: (s["start"], s["start"] + s["frames"] / fps) for s in tl["slides"]}
        frames = []
        for n, states in eff.items():
            s0, s1 = span[int(n)]
            for k, st in enumerate(states):
                t = min(s0 + (tl["pre"] + st["t"] if k else 0) + 0.8, s1 - 0.8, dur - 1.5)
                p = os.path.join(tmp, f"e{int(n):03d}_{k}.png"); grab(video, t, p)
                frames.append((p, f"#{n} state {k}  {int(t // 60)}:{t % 60:04.1f}"))
        if frames:
            sheet(frames, os.path.join(work, "qa_effects.png"))
            print(f"effects sheet: {os.path.join(work, 'qa_effects.png')}")

    print("QA:", "PASS" if not problems else "CHECK ->\n  " + "\n  ".join(problems))


if __name__ == "__main__":
    main()
