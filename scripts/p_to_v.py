"""p_to_v: .pptx -> narrated video. Steps: slides -> script -> tts -> clips -> final

  python p_to_v.py --check                                  # preflight: dependencies
  python p_to_v.py deck.pptx --stop-after script            # export slides + draft narration
  (edit WORK/script.json — one string per slide)
  python p_to_v.py deck.pptx --start-from tts --title "..." --subtitle "..."

WORK defaults to %TEMP%/p_to_v/<deck name>; it is printed at the start.
"""
import argparse, glob, importlib.util, json, os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
STEPS = ["slides", "script", "tts", "effects", "clips", "final", "qa"]
POWERPNT = [r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE",
            r"C:\Program Files (x86)\Microsoft Office\root\Office16\POWERPNT.EXE",
            r"C:\Program Files\Microsoft Office\Office16\POWERPNT.EXE"]


def check():
    ok = True
    def row(name, good, hint=""):
        nonlocal ok
        ok &= bool(good)
        print(f"[{'OK' if good else '!!'}] {name}" + ("" if good else f"  -> {hint}"))
    row("ffmpeg", shutil.which("ffmpeg"), "install ffmpeg and add it to PATH (winget install Gyan.FFmpeg)")
    row("ffprobe", shutil.which("ffprobe"), "comes with ffmpeg")
    for mod, pkg in [("pptx", "python-pptx"), ("edge_tts", "edge-tts"), ("PIL", "Pillow")]:
        row(f"python: {pkg}", importlib.util.find_spec(mod), f"{sys.executable} -m pip install {pkg}")
    reg = subprocess.run(["reg", "query", r"HKCR\PowerPoint.Application"], capture_output=True).returncode == 0
    row("PowerPoint (COM)", reg or any(os.path.exists(p) for p in POWERPNT), "Microsoft PowerPoint is required")
    row("font: Microsoft JhengHei", os.path.exists(r"C:\Windows\Fonts\msjh.ttc"), "install 微軟正黑體")
    if shutil.which("ffmpeg"):
        enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        hw = [e for e in ("h264_nvenc", "h264_qsv") if e in enc]
        print(f"[i] hardware encoders available: {', '.join(hw) or 'none'} (use --encoder; may still fail without the GPU)")
    # voice engines (the run itself needs only one of them)
    sapi = subprocess.run(["powershell", "-NoProfile", "-Command",
                           "Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer)"
                           ".GetInstalledVoices() | % { $_.VoiceInfo.Name + ' (' + $_.VoiceInfo.Culture + ')' }"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.splitlines()
    sapi = [v.strip() for v in sapi if v.strip()]
    print("[i] voice engines, tried in this order by --engine auto:")
    print(f"    edge      : {'installed' if importlib.util.find_spec('edge_tts') else 'missing'} (online, free)")
    print(f"    windows   : {', '.join(sapi) or 'no SAPI voices'} (offline)")
    print("ready" if ok else "missing dependencies")
    return ok


def call(cmd):
    print(">>", " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    r = subprocess.run(cmd)
    if r.returncode:
        sys.exit(r.returncode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pptx", nargs="?")
    ap.add_argument("--check", action="store_true", help="only check dependencies")
    ap.add_argument("--work", default="", help="intermediate folder (default %%TEMP%%/p_to_v/<deck>)")
    ap.add_argument("--out", default="", help="output mp4 (default: <pptx>_配音影片.mp4)")
    ap.add_argument("--engine", default="auto", choices=["auto", "edge", "windows"],
                    help="TTS engine; auto = edge, falling back to the offline Windows voice")
    ap.add_argument("--voice", default="zh-TW-HsiaoChenNeural", help="edge voice")
    ap.add_argument("--win-voice", default="Microsoft Hanhan Desktop", help="offline Windows voice")
    ap.add_argument("--rate", default="+8%")
    ap.add_argument("--lexicon", default="", help="extra pronunciation rules (json)")
    ap.add_argument("--title", default=""); ap.add_argument("--subtitle", default="")
    ap.add_argument("--kicker", default="")
    ap.add_argument("--no-intro", action="store_true"); ap.add_argument("--no-subs", action="store_true")
    ap.add_argument("--no-effects", action="store_true", help="ignore [[show]] / [[focus]] markers")
    ap.add_argument("--zoom", type=float, default=0.07)
    ap.add_argument("--bgm", default="")
    ap.add_argument("--encoder", default="libx264", choices=["libx264", "h264_nvenc", "h264_qsv"])
    ap.add_argument("--jobs", type=int, default=0, help="parallel clip renders (default cpu/2, max 8)")
    ap.add_argument("--slides", type=int, default=0, help="only the first N visible slides (quick test)")
    ap.add_argument("--start-from", choices=STEPS, default="slides")
    ap.add_argument("--stop-after", choices=STEPS, default="qa")
    a = ap.parse_args()

    if a.check:
        sys.exit(0 if check() else 1)
    if not a.pptx:
        ap.error("pptx is required")
    pptx = os.path.abspath(a.pptx)
    if not os.path.isfile(pptx):
        sys.exit(f"not found: {pptx}")
    name = os.path.splitext(os.path.basename(pptx))[0]
    work = os.path.abspath(a.work or os.path.join(tempfile.gettempdir(), "p_to_v", re.sub(r"[^\w.-]+", "_", name)))
    os.makedirs(work, exist_ok=True)
    todo = STEPS[STEPS.index(a.start_from): STEPS.index(a.stop_after) + 1]
    out = os.path.abspath(a.out or os.path.splitext(pptx)[0] + "_配音影片.mp4")
    if not a.out and ("clips" in todo or "final" in todo):  # never overwrite an earlier video silently
        base, k = out[:-4], 2
        while os.path.exists(out):
            out = f"{base}_{k}.mp4"; k += 1
    py = sys.executable
    print(f"WORK = {work}\nOUT  = {out}\nsteps: {' -> '.join(todo)}", flush=True)

    if "slides" in todo:
        call(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
              os.path.join(HERE, "export_slides.ps1"), "-Pptx", pptx, "-OutDir", os.path.join(work, "slides"),
              "-MaxSlides", str(a.slides)])
    if "script" in todo:
        call([py, os.path.join(HERE, "extract_text.py"), pptx, work, "--slides", str(a.slides)])
        call([py, os.path.join(HERE, "effects.py"), work, "--grids"])

    if "tts" in todo or "clips" in todo:  # slide images and narration must line up 1:1
        n_slides = len([p for p in glob.glob(os.path.join(work, "slides", "slide_*.png"))
                        if re.fullmatch(r"slide_\d+\.png", os.path.basename(p))])
        with open(os.path.join(work, "script.json"), encoding="utf-8") as f:
            script = json.load(f)
        if not isinstance(script, list) or not all(isinstance(s, str) for s in script):
            sys.exit("script.json must be a JSON array of strings (one per slide)")
        if len(script) != n_slides:
            sys.exit(f"script.json has {len(script)} entries but there are {n_slides} slide images. Fix script.json.")

    if "tts" in todo:
        cmd = [py, os.path.join(HERE, "tts.py"), work, "--engine", a.engine, "--voice", a.voice,
               "--win-voice", a.win_voice, "--rate", a.rate]
        if a.lexicon: cmd += ["--lexicon", os.path.abspath(a.lexicon)]
        call(cmd)
    if "effects" in todo and not a.no_effects:
        call([py, os.path.join(HERE, "effects.py"), work])
    if "clips" in todo or "final" in todo:
        cmd = [py, os.path.join(HERE, "build.py"), work, out, "--title", a.title or name,
               "--subtitle", a.subtitle, "--kicker", a.kicker, "--zoom", str(a.zoom), "--encoder", a.encoder,
               "--start-from", "clips" if "clips" in todo else "final"]
        if a.jobs: cmd += ["--jobs", str(a.jobs)]
        if a.no_intro: cmd.append("--no-intro")
        if a.no_subs: cmd.append("--no-subs")
        if a.no_effects: cmd.append("--no-effects")
        if a.bgm: cmd += ["--bgm", os.path.abspath(a.bgm)]
        call(cmd)
    if "qa" in todo:
        call([py, os.path.join(HERE, "qa.py"), work, out])


if __name__ == "__main__":
    main()
