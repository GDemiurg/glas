#!/usr/bin/env python3
"""Download the video, dump HUD-mask samples, then scan with periodic re-ranking.

Usage: python run.py [URL] [--fps 1] [--workers N]
Output goes to ./work next to this script: work/out/ranked.txt and work/out/frames/.
"""
import os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
W = os.path.join(HERE, "work")
OCR = os.path.join(HERE, "ocrscan.py")
url = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "https://youtu.be/EHlqDs4SCfs"
extra = [a for a in sys.argv[1:] if a != url]
video = os.path.join(W, "video.mp4")
out = os.path.join(W, "out")
os.makedirs(out, exist_ok=True)

if not os.path.exists(video):
    subprocess.run([sys.executable, "-m", "yt_dlp", "-f", "bv*[height<=720][ext=mp4]/bv*[height<=720]",
                    "--concurrent-fragments", "8", "-o", video, url], check=True)
subprocess.run([sys.executable, OCR, "samples", video, os.path.join(out, "samples")], check=True)
scan = subprocess.Popen([sys.executable, OCR, "scan", video, out] + extra)
rank = [sys.executable, OCR, "rank", out, "--video", video, "--top", "50", "--save", "30"]
while scan.poll() is None:
    time.sleep(120)
    if os.path.exists(os.path.join(out, "hits.jsonl")):
        subprocess.run(rank, stdout=subprocess.DEVNULL)
subprocess.run(rank)
