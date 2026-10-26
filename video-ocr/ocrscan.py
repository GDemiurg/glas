#!/usr/bin/env python3
"""Scan a video for overlay text: parallel 1fps frame decode + masked OCR.

Usage:
  ocrscan.py samples VIDEO OUTDIR          # dump masked sample frames to verify HUD masks
  ocrscan.py scan VIDEO OUTDIR [--fps 1] [--workers N] [--chunk 300]
  ocrscan.py rank OUTDIR [--top 40]        # rank hits so far (safe to run mid-scan)

Masks are fractions of frame size so they work at any resolution; edit MASKS below.
"""
import argparse, json, os, re, subprocess, sys, time
from multiprocessing import Pool
os.environ["OMP_THREAD_LIMIT"] = "1"  # one tesseract thread per worker; avoids oversubscription
import numpy as np, cv2, pytesseract
cv2.setNumThreads(1)
from wordfreq import zipf_frequency

# (x0, y0, x1, y1) as fractions of width/height -- regions blanked before OCR
MASKS = [
    (0.80, 0.00, 1.00, 0.40),   # minimap + coordinates, top-right
    (0.28, 0.85, 0.72, 1.00),   # hotbar + item counts, bottom-center
]
OCR_TIMEOUT = 8
OCR_W = 960  # downscale width for OCR (speed); overlay text is usually large


def duration(video):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "csv=p=0", video])
    return float(out)


def dims(video):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                   "stream=width,height", "-of", "csv=p=0", video]).decode().strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def apply_masks(img):
    h, w = img.shape[:2]
    for x0, y0, x1, y1 in MASKS:
        img[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] = 0
    return img


def fmt(t):
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}"


def is_word(tok):
    w = re.sub(r"[^A-Za-z']", "", tok)
    if len(w) < 2 or len(w) != len(re.sub(r"[^A-Za-z0-9'!?.,:-]", "", tok)):
        return 0.0
    z = zipf_frequency(w.lower(), "en")
    if len(w) == 2 and z < 5.5:
        return 0.0
    return z if z >= 2.5 else 0.0


def ocr_variants(img):
    """Overlay text is typically bright-on-dark or dark-on-bright with an outline;
    OCR grayscale plus white-text and dark-text thresholds."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # median kills fine block-texture detail but keeps thick overlay strokes
    _, white = cv2.threshold(cv2.medianBlur(g, 5), 215, 255, cv2.THRESH_BINARY_INV)
    yield white
    yield cv2.medianBlur(g, 3)


def ocr_frame(img):
    best = []
    for v in ocr_variants(img):
        try:
            d = pytesseract.image_to_data(v, lang="eng", config="--oem 1 --psm 11",
                                          output_type=pytesseract.Output.DICT, timeout=OCR_TIMEOUT)
        except RuntimeError:  # tesseract timeout on pathological texture
            continue
        words = []
        for txt, conf, x, y, w, h in zip(d["text"], d["conf"], d["left"], d["top"], d["width"], d["height"]):
            txt = txt.strip()
            if not txt or float(conf) < 60:
                continue
            z = is_word(txt)
            if z:
                words.append({"t": txt, "c": int(float(conf)), "z": round(z, 2), "box": [x, y, w, h]})
        if sum(len(w["t"]) for w in words) > sum(len(w["t"]) for w in best):
            best = words
    return best


def scan_chunk(args):
    video, outdir, start, dur, fps, w, h = args
    sw, sh = OCR_W, int(round(h * OCR_W / w / 2) * 2)
    cmd = ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(dur), "-i", video,
           "-vf", f"fps={fps},scale={sw}:{sh}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=sw * sh * 3 * 4)
    fsz = sw * sh * 3
    res, i = [], 0
    while True:
        buf = p.stdout.read(fsz)
        if len(buf) < fsz:
            break
        t = start + i / fps
        i += 1
        img = apply_masks(np.frombuffer(buf, np.uint8).reshape(sh, sw, 3).copy())
        words = ocr_frame(img)
        if words:
            res.append({"t": round(t, 2), "words": words})
    p.wait()
    with open(os.path.join(outdir, "hits.jsonl"), "a") as f:
        for r in res:
            f.write(json.dumps(r) + "\n")
    with open(os.path.join(outdir, "done.txt"), "a") as f:
        f.write(f"{start}\n")
    return start, i, len(res)


def cmd_samples(a):
    os.makedirs(a.outdir, exist_ok=True)
    D = duration(a.video)
    for k, t in enumerate(np.linspace(30, D - 30, 8)):
        raw = os.path.join(a.outdir, f"sample_{k}_{fmt(t).replace(':', '-')}.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", a.video, "-frames:v", "1", raw])
        img = cv2.imread(raw)
        h, w = img.shape[:2]
        for x0, y0, x1, y1 in MASKS:
            cv2.rectangle(img, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)), (0, 0, 255), 2)
        cv2.imwrite(raw.replace(".png", "_mask.png"), img)
    print("wrote samples to", a.outdir)


def cmd_scan(a):
    os.makedirs(a.outdir, exist_ok=True)
    D = duration(a.video)
    w, h = dims(a.video)
    done = set()
    dp = os.path.join(a.outdir, "done.txt")
    if os.path.exists(dp):
        done = {float(x) for x in open(dp).read().split()}
    starts = [s for s in np.arange(0, D, a.chunk) if float(s) not in done]
    jobs = [(a.video, a.outdir, float(s), a.chunk, a.fps, w, h) for s in starts]
    print(f"{len(jobs)} chunks of {a.chunk}s, {a.workers} workers, video {fmt(D)}", flush=True)
    t0, n = time.time(), 0
    with Pool(a.workers) as pool:
        for start, frames, hits in pool.imap_unordered(scan_chunk, jobs):
            n += 1
            print(f"[{fmt(time.time() - t0)}] chunk {fmt(start)} done: {frames} frames, {hits} w/ words "
                  f"({n}/{len(jobs)})", flush=True)


def cmd_rank(a):
    rows = [json.loads(l) for l in open(os.path.join(a.outdir, "hits.jsonl"))]
    rows.sort(key=lambda r: r["t"])
    # group frames into runs: consecutive (<=2.5s apart) frames sharing at least one word
    runs = []
    for r in rows:
        ws = {w["t"].lower().strip(".,!?:") for w in r["words"]}
        if runs and r["t"] - runs[-1]["end"] <= 2.5 and ws & runs[-1]["last"]:
            g = runs[-1]
            g["end"], g["last"] = r["t"], ws
            g["frames"].append(r)
        else:
            runs.append({"start": r["t"], "end": r["t"], "last": ws, "frames": [r]})
    scored = []
    for g in runs:
        # count words that recur in >=2 frames of the run (persistent), weight by length/frequency/conf
        cnt = {}
        for fr in g["frames"]:
            for w in {(x["t"].lower().strip(".,!?:"), x["z"], x["c"]) for x in fr["words"]}:
                k = w[0]
                c = cnt.setdefault(k, [0, w[1], 0])
                c[0] += 1
                c[2] = max(c[2], w[2])
        n = len(g["frames"])
        persistent = {k: v for k, v in cnt.items() if v[0] >= 2} if n >= 2 else cnt
        score = sum(min(v[0], 10) * len(k) * (v[2] / 100) for k, v in persistent.items())
        if n == 1:
            score *= 0.3
        if len(persistent) >= 2:
            score *= 1.5
        best = max(g["frames"], key=lambda f: sum(len(w["t"]) for w in f["words"]))
        text = " ".join(w["t"] for w in sorted(best["words"], key=lambda w: (w["box"][1] // 20, w["box"][0])))
        scored.append((score, g["start"], g["end"], n, text, best["t"]))
    scored.sort(reverse=True)
    os.makedirs(os.path.join(a.outdir, "frames"), exist_ok=True)
    out = []
    for rank, (s, st, en, n, text, bt) in enumerate(scored[:a.top], 1):
        line = f"{rank:3d}. {fmt(st)}-{fmt(en)}  score={s:6.1f}  frames={n:3d}  | {text}"
        out.append(line)
        if a.video and rank <= a.save:
            p = os.path.join(a.outdir, "frames", f"{rank:02d}_{fmt(bt).replace(':', '-')}.jpg")
            if not os.path.exists(p):
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(bt), "-i", a.video,
                                "-frames:v", "1", "-q:v", "2", p])
    open(os.path.join(a.outdir, "ranked.txt"), "w").write("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("samples"); s.add_argument("video"); s.add_argument("outdir")
    s = sp.add_parser("scan"); s.add_argument("video"); s.add_argument("outdir")
    s.add_argument("--fps", type=float, default=1.0); s.add_argument("--workers", type=int, default=os.cpu_count())
    s.add_argument("--chunk", type=float, default=300)
    s = sp.add_parser("rank"); s.add_argument("outdir"); s.add_argument("--top", type=int, default=40)
    s.add_argument("--video"); s.add_argument("--save", type=int, default=20)
    a = ap.parse_args()
    {"samples": cmd_samples, "scan": cmd_scan, "rank": cmd_rank}[a.cmd](a)
