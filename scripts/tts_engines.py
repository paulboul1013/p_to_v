"""TTS engines for p_to_v. Every engine turns {wav_path: text} into 48 kHz mono WAV files.

  edge       Microsoft Edge read-aloud (edge-tts) - free, no key, online, may break without notice
  windows    Windows built-in SAPI voice (default "Microsoft Hanhan Desktop", zh-TW female) - offline, always there
"""
import asyncio, json, os, re, subprocess, tempfile

SR = 48000
HERE = os.path.dirname(os.path.abspath(__file__))
ORDER = ["edge", "windows"]


class EngineError(RuntimeError):
    pass


def to_wav(src, dst):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-ac", "1", "-ar", str(SR), dst],
                       capture_output=True, text=True)
    if r.returncode or not os.path.exists(dst) or os.path.getsize(dst) < 1000:
        raise EngineError(f"could not decode audio: {r.stderr[-300:]}")


def pct(rate):  # "+8%" -> 8
    m = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*%\s*", rate or "0%")
    return float(m.group(1)) if m else 0.0


class Engine:
    name = "?"

    def __init__(self, cfg):
        self.cfg = cfg

    def available(self):
        return True, ""

    def voice_id(self):           # goes into the cache key
        return self.cfg["voice"]

    async def synth_all(self, jobs):
        raise NotImplementedError


# ------------------------------------------------------------------ edge
class Edge(Engine):
    name = "edge"

    def available(self):
        try:
            import edge_tts  # noqa
            return True, ""
        except ImportError:
            return False, "edge-tts not installed"

    async def one(self, text, wav, sem, tries):
        import edge_tts
        async with sem:
            mp3 = wav[:-4] + ".edge.mp3"
            for k in range(tries):
                try:
                    c = edge_tts.Communicate(text, self.cfg["voice"], rate=self.cfg["rate"])
                    await asyncio.wait_for(c.save(mp3), timeout=60)
                    if os.path.getsize(mp3) == 0:
                        raise EngineError("empty audio")
                    break
                except Exception as e:
                    if k == tries - 1:
                        raise EngineError(f"edge-tts failed ({type(e).__name__}: {e})")
                    await asyncio.sleep(2 ** k)
            to_wav(mp3, wav); os.remove(mp3)

    async def synth_all(self, jobs, tries=5):
        sem = asyncio.Semaphore(self.cfg.get("concurrency", 2))
        await asyncio.gather(*(self.one(t, p, sem, tries) for p, t in jobs.items()))


# ------------------------------------------------------------------ Windows SAPI (offline)
class Windows(Engine):
    name = "windows"

    def voice_id(self):
        return f"sapi:{self.cfg['win_voice']}"

    def available(self):
        if os.name != "nt":
            return False, "not Windows"
        return True, ""

    async def synth_all(self, jobs):
        rate = max(-10, min(10, round(pct(self.cfg["rate"]) / 10)))
        tmp = tempfile.mkdtemp(prefix="p2v_sapi_")
        lst = [{"text": t, "out": os.path.join(tmp, f"{k}.wav")} for k, t in enumerate(jobs.values())]
        jp = os.path.join(tmp, "jobs.json")
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(lst, f, ensure_ascii=False)
        r = await asyncio.to_thread(subprocess.run, [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(HERE, "sapi_tts.ps1"),
            "-Jobs", jp, "-Voice", self.cfg["win_voice"], "-Rate", str(rate)], capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        if r.returncode:
            raise EngineError(f"Windows SAPI failed: {(r.stderr or r.stdout)[-400:]}")
        for (wav, _), j in zip(jobs.items(), lst):
            to_wav(j["out"], wav)


ENGINES = {"edge": Edge, "windows": Windows}
