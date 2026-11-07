"""ASMR cutting studio - object name + number of cuts -> vertical ASMR cutting video (Blender + FFmpeg).

  python studio.py check                          find Blender / ffmpeg / ffprobe, show versions, render a test preview.png
  python studio.py preview [--object X --cuts N]  new run folder: scene.blend, impacts.json, opening/middle/fall previews
  python studio.py render [--run DIR]             frames (resumes) -> soundtrack.wav -> MP4 -> FFprobe + full decode check
  python studio.py make [--object X --cuts N]     preview + render in one go (used by the Shorts dashboard)
  python studio.py tools --json                   tool paths + versions as JSON

Values come from config.json; --object/--cuts/--watermark/--samples/--engine override them for one run.
Add --progress for machine-readable "PROGRESS <0..1> <message>" lines.
"""
import argparse
import glob
import json
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import plan  # noqa: E402
import sound  # noqa: E402

RUNS = ROOT / "runs"
SCENE = ROOT / "blender" / "scene.py"
PNG_SIG = b"\x89PNG\r\n\x1a\n"
DEFAULTS = {"object": "Papaya", "cuts": 50, "watermark": "@ai.reviewbyamit", "seed": None,
            "render": {"engine": "eevee", "samples": 48, "width": 540, "height": 960, "fps": 24},
            "tools": {"blender": "", "ffmpeg": "", "ffprobe": ""}}
PROGRESS = False
_last_msg = None


def say(frac: float, msg: str):
    global _last_msg
    if msg == _last_msg:  # pollers call this every second; only print changes
        return
    _last_msg = msg
    print(f"PROGRESS {frac:.3f} {msg}" if PROGRESS else f"[{frac:4.0%}] {msg}", flush=True)


class StudioError(Exception):
    pass


# ---------------------------------------------------------------- config + tools
def load_config(args) -> dict:
    path = ROOT / "config.json"
    cfg = json.loads(json.dumps(DEFAULTS))
    if path.exists():
        user = json.loads(path.read_text("utf-8"))
        for k, v in user.items():
            cfg[k] = {**cfg[k], **v} if isinstance(v, dict) and isinstance(cfg.get(k), dict) else v
    for k in ("object", "cuts", "watermark"):
        if getattr(args, k, None) is not None:
            cfg[k] = getattr(args, k)
    for k in ("samples", "engine"):
        if getattr(args, k, None) is not None:
            cfg["render"][k] = getattr(args, k)
    if getattr(args, "seed", None) is not None:
        cfg["seed"] = args.seed
    if cfg.get("seed") is not None and (isinstance(cfg["seed"], bool) or not isinstance(cfg["seed"], int)):
        raise StudioError("seed must be a whole number (or null for a new random look every run)")
    cfg["object"] = str(cfg["object"]).strip()[:60]
    if not cfg["object"]:
        raise StudioError("Object name is empty")
    cfg["cuts"] = plan.validate_cuts(cfg["cuts"])
    cfg["watermark"] = str(cfg.get("watermark") or "")[:40]
    if cfg["render"]["engine"] not in ("eevee", "cycles"):
        raise StudioError("render.engine must be eevee or cycles")
    return cfg


def _version_key(path: str):
    return [int(x) for x in re.findall(r"\d+", Path(path).parent.name)] or [0]


def find_tools(cfg: dict) -> dict:
    t = cfg.get("tools", {})
    blender = t.get("blender") or shutil.which("blender")
    if not blender:
        found = (glob.glob(r"C:\Program Files\Blender Foundation\Blender*\blender.exe")
                 + glob.glob(r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"))
        blender = max(found, key=_version_key) if found else None
    ffmpeg = t.get("ffmpeg") or shutil.which("ffmpeg")
    if not ffmpeg:
        found = glob.glob(str(Path.home() / r"AppData\Local\Microsoft\WinGet\Packages\*FFmpeg*\**\bin\ffmpeg.exe"), recursive=True)
        ffmpeg = found[0] if found else None
    ffprobe = t.get("ffprobe")
    if not ffprobe and ffmpeg:  # keep ffprobe next to the ffmpeg we use
        sib = Path(ffmpeg).with_name("ffprobe.exe" if ffmpeg.lower().endswith(".exe") else "ffprobe")
        ffprobe = str(sib) if sib.exists() else shutil.which("ffprobe")
    return {"blender": blender, "ffmpeg": ffmpeg, "ffprobe": ffprobe}


def tool_versions(tools: dict) -> dict:
    out = {}
    for name, path in tools.items():
        if not path or not Path(path).exists():
            out[name] = None
            continue
        flag = "--version" if name == "blender" else "-version"
        try:
            first = subprocess.run([path, flag], capture_output=True, text=True, timeout=60).stdout.strip().splitlines()
            out[name] = first[0] if first else "unknown"
        except (OSError, subprocess.TimeoutExpired) as e:
            out[name] = f"error: {e}"
    return out


def need_tools(cfg) -> dict:
    tools = find_tools(cfg)
    missing = [k for k, v in tools.items() if not v or not Path(v).exists()]
    if missing:
        raise StudioError(f"Not found: {', '.join(missing)}. Install it, or put the full path in config.json -> tools.")
    return tools


# ---------------------------------------------------------------- blender
def blender(tools, args: list, log: Path, poll=None):
    """Run Blender in the background, log everything; poll() is called every second while it runs."""
    with open(log, "a", encoding="utf-8", errors="replace") as fh:
        fh.write(f"\n==== {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(args)}\n")
        fh.flush()
        proc = subprocess.Popen([tools["blender"], "-b", "--factory-startup", *args], stdout=fh, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            while proc.poll() is None:
                time.sleep(1)
                if poll:
                    poll()
        except BaseException:
            proc.kill()
            raise
    tail = log.read_text("utf-8", errors="replace")[-3000:]
    if proc.returncode != 0 or "SCENE_OK" not in tail:
        raise StudioError(f"Blender failed (exit {proc.returncode}). Last log lines:\n{tail[-1500:]}")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30] or "object"


def new_run(cfg) -> Path:
    run = RUNS / f"{datetime.now():%Y%m%d-%H%M%S}_{slug(cfg['object'])}_{cfg['cuts']}"
    (run / "previews").mkdir(parents=True)
    if cfg.get("seed") is None:  # new cut angles / camera every run; saved so the run can be rebuilt exactly
        cfg = {**cfg, "seed": random.randrange(1, 10**6)}
    (run / "config.json").write_text(json.dumps({k: v for k, v in cfg.items() if k != "tools"}, indent=2), "utf-8")
    return run


def preview(cfg, tools, lo=0.0, hi=1.0) -> Path:
    run = new_run(cfg)
    say(lo, f"Building scene for {cfg['object']} x {cfg['cuts']} cuts in {run.name}")
    shots = run / "previews"
    blender(tools, ["--python-exit-code", "1", "--python", str(SCENE), "--", "--mode", "preview", "--run", str(run)],
            run / "blender.log", poll=lambda: say(lo + (hi - lo) * (0.3 + 0.7 * len(list(shots.glob("*.png"))) / 3),
                                                  "Building scene and rendering previews"))
    info = json.loads((run / "impacts.json").read_text("utf-8"))
    say(hi, f"Preview ready: {run}")
    if not PROGRESS:
        print(f"\nScene: {run / 'scene.blend'}\nPreviews: {shots}\n{info['pieces']} pieces, "
              f"{info['frame_end']} frames ({info['duration']:.1f} s), fall from frame {info['fall_start']}\nCuts:")
        for imp in info["impacts"]:
            print(f"  {imp['cut']:>3}  frame {imp['frame']:>4}  {imp['direction']}")
    return run


# ---------------------------------------------------------------- render + export
def png_ok(p: Path) -> bool:
    try:
        if p.stat().st_size < 100:
            return False
        with open(p, "rb") as f:
            if f.read(8) != PNG_SIG:
                return False
            f.seek(-12, 2)
            return f.read(12)[4:8] == b"IEND"
    except OSError:
        return False


def clean_frames(frames: Path, frame_end: int) -> int:
    """Delete missing-tail/corrupt PNGs (e.g. from an interrupted render); return how many valid frames remain."""
    ok = 0
    for i in range(1, frame_end + 1):
        p = frames / f"{i:04d}.png"
        if p.exists() and not png_ok(p):
            p.unlink()
        elif p.exists():
            ok += 1
    return ok


def probe(tools, mp4: Path) -> dict:
    r = subprocess.run([tools["ffprobe"], "-v", "error", "-count_frames", "-show_entries",
                        "stream=codec_type,codec_name,width,height,pix_fmt,nb_read_frames,sample_rate,channels:format=duration",
                        "-of", "json", str(mp4)], capture_output=True, text=True)
    if r.returncode:
        raise StudioError(f"ffprobe failed: {r.stderr.strip()}")
    return json.loads(r.stdout)


def verify(tools, mp4: Path, info: dict, cfg: dict) -> dict:
    data = probe(tools, mp4)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), {})
    dur = float(data["format"]["duration"])
    checks = {
        "size": f"{v.get('width')}x{v.get('height')}",
        "video": f"{v.get('codec_name')} {v.get('pix_fmt')}",
        "frames": int(v.get("nb_read_frames") or 0),
        "audio": f"{a.get('codec_name')} {a.get('sample_rate')} Hz {a.get('channels')} ch" if a else None,
        "duration": round(dur, 3),
    }
    problems = []
    if (v.get("width"), v.get("height")) != (cfg["render"]["width"], cfg["render"]["height"]):
        problems.append(f"size {checks['size']}")
    if v.get("codec_name") != "h264" or v.get("pix_fmt") != "yuv420p":
        problems.append(f"video {checks['video']}")
    if checks["frames"] != info["frame_end"]:
        problems.append(f"frames {checks['frames']} != {info['frame_end']}")
    if not a or a.get("codec_name") != "aac":
        problems.append("no AAC audio stream")
    if abs(dur - info["duration"]) > 0.1:
        problems.append(f"duration {dur:.2f}s != {info['duration']:.2f}s")
    dec = subprocess.run([tools["ffmpeg"], "-v", "error", "-i", str(mp4), "-f", "null", "-"], capture_output=True, text=True)
    checks["decode_errors"] = dec.stderr.strip()[:500]
    if dec.returncode or checks["decode_errors"]:
        problems.append("decode errors")
    if problems:
        raise StudioError("MP4 check failed: " + "; ".join(problems))
    return checks


def render(run: Path, tools, lo=0.0, hi=1.0) -> dict:
    cfg = json.loads((run / "config.json").read_text("utf-8"))
    info = json.loads((run / "impacts.json").read_text("utf-8"))
    frames, n = run / "frames", info["frame_end"]
    frames.mkdir(exist_ok=True)
    have = clean_frames(frames, n)
    span = (hi - lo) * 0.85
    if have < n:
        say(lo, f"Rendering {n - have} of {n} frames ({have} already done)")
        blender(tools, [str(run / "scene.blend"), "--python-exit-code", "1", "--python", str(SCENE), "--", "--mode", "render",
                        "--run", str(run)], run / "blender.log",
                poll=lambda: say(lo + span * len(list(frames.glob("*.png"))) / n, f"Rendering frames {len(list(frames.glob('*.png')))}/{n}"))
        have = clean_frames(frames, n)
        if have < n:
            raise StudioError(f"Only {have}/{n} valid frames. Run render again to resume from the missing ones.")
    else:
        say(lo + span, f"All {n} frames already rendered")
    say(lo + span, "Synthesising chop soundtrack")
    wav = run / "soundtrack.wav"
    sound.make_wav(info, wav, tools["ffmpeg"])
    mp4 = run / f"{slug(info['object'])}_{info['cuts']}cuts.mp4"
    say(lo + span + (hi - lo) * 0.05, "Exporting MP4 (H.264 + AAC)")
    fps = str(info["fps"])
    r = subprocess.run([tools["ffmpeg"], "-y", "-v", "error", "-framerate", fps, "-start_number", "1",
                        "-i", str(frames / "%04d.png"), "-i", str(wav), "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", fps,
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-shortest", "-movflags", "+faststart",
                        str(mp4)], capture_output=True, text=True)
    if r.returncode:
        raise StudioError(f"ffmpeg export failed: {r.stderr.strip()[-800:]}")
    say(lo + span + (hi - lo) * 0.1, "Checking MP4 with FFprobe and a full decode")
    checks = verify(tools, mp4, info, cfg)
    result = {"run": str(run), "mp4": str(mp4), "blend": str(run / "scene.blend"), "wav": str(wav),
              "previews": {p.stem: str(p) for p in sorted((run / "previews").glob("*.png"))},
              "object": info["object"], "label": info["label"], "cuts": info["cuts"], "pieces": info["pieces"],
              "pattern": info["pattern"], "duration": info["duration"], "checks": checks}
    (run / "result.json").write_text(json.dumps(result, indent=2), "utf-8")
    say(hi, f"Done: {mp4}")
    return result


def latest_run() -> Path:
    runs = sorted(p for p in RUNS.glob("*_*") if (p / "impacts.json").exists()) if RUNS.exists() else []
    if not runs:
        raise StudioError("No preview run yet. Run: python studio.py preview")
    return runs[-1]


def check(cfg):
    tools = find_tools(cfg)
    vers = tool_versions(tools)
    for k in ("blender", "ffmpeg", "ffprobe"):
        print(f"{k:8} {tools[k] or 'NOT FOUND'}\n         {vers[k] or '-'}")
    tools = need_tools(cfg)
    out = RUNS / "_setup_check"
    out.mkdir(parents=True, exist_ok=True)
    blender(tools, ["--python-exit-code", "1", "--python", str(SCENE), "--", "--mode", "test", "--run", str(out)], out / "blender.log")
    if not png_ok(out / "preview.png"):
        raise StudioError("preview.png was not written")
    print(f"preview  {out / 'preview.png'}\nCheckpoint OK: three versions + preview.png")


def main(argv=None):
    global PROGRESS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "preview", "render", "make", "tools"])
    ap.add_argument("--object")
    ap.add_argument("--cuts")
    ap.add_argument("--watermark")
    ap.add_argument("--samples", type=int)
    ap.add_argument("--engine", choices=["eevee", "cycles"])
    ap.add_argument("--seed", type=int, help="rebuild an earlier run's exact angles (see its config.json)")
    ap.add_argument("--run", help="run folder for 'render' (default: latest)")
    ap.add_argument("--progress", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    PROGRESS = a.progress
    try:
        cfg = load_config(a)
        if a.command == "tools":
            tools = find_tools(cfg)
            out = {"tools": tools, "versions": tool_versions(tools), "presets": sorted(plan.PRESETS), "config": cfg}
            print(json.dumps(out, indent=None if a.json else 2))
        elif a.command == "check":
            check(cfg)
        elif a.command == "preview":
            preview(cfg, need_tools(cfg))
        elif a.command == "render":
            res = render(Path(a.run) if a.run else latest_run(), need_tools(cfg))
            print("RESULT " + json.dumps(res))
        else:
            tools = need_tools(cfg)
            run = preview(cfg, tools, 0.0, 0.1)
            print("RESULT " + json.dumps(render(run, tools, 0.1, 1.0)))
    except (StudioError, ValueError) as e:
        print(f"ERROR {e}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
