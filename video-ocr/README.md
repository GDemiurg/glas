# video-ocr

Finds overlay text in a long video: 720p download, 1 fps frames, HUD regions masked,
parallel tesseract OCR, English-word filter, ranked by persistence across frames.

Setup:
- `pip install yt-dlp opencv-python-headless pytesseract numpy wordfreq`
- ffmpeg and tesseract on PATH (Windows: `winget install Gyan.FFmpeg UB-Mannheim.TesseractOCR`;
  Mac: `brew install ffmpeg tesseract`; Linux: `apt install ffmpeg tesseract-ocr`)

Run: `python run.py` (defaults to the contest video). Check `work/out/samples/*_mask.png` first:
red boxes must cover the minimap and hotbar; adjust `MASKS` in `ocrscan.py` if not, then re-run.
Results: `work/out/ranked.txt` (refreshed every 2 min during the scan) and `work/out/frames/`.
`python ocrscan.py rank work/out --video work/video.mp4` re-ranks at any time.
