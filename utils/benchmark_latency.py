#!/usr/bin/env python3
"""
Measure real STT + LLM-cleanup latency on this machine.

Usage:
    .venv/bin/python utils/benchmark_latency.py [--wav FILE] [--models small,distil-large-v3] [--runs 3]

Without --wav it records 5 seconds from the default PipeWire source
(speak a sentence when prompted). Results print as a markdown table.
"""

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'lib' / 'src'))

MODELS_DIR = Path.home() / '.local/share/pywhispercpp/models'


def record_with_timeout(seconds: float) -> Path:
    out = Path(tempfile.mkstemp(suffix='.wav')[1])
    print(f"Recording {seconds:.0f}s from default mic — speak now...")
    proc = subprocess.Popen(['pw-record', '--rate', '16000', '--channels', '1', str(out)])
    time.sleep(seconds)
    proc.terminate()
    proc.wait()
    print("Recording done.")
    return out


def bench_stt(wav: Path, model_name: str, runs: int):
    from pywhispercpp.model import Model
    model_path = MODELS_DIR / f'ggml-{model_name}.bin'
    if not model_path.exists():
        print(f"  SKIP {model_name}: {model_path} missing")
        return None
    t0 = time.time()
    model = Model(str(model_path), print_realtime=False, print_progress=False)
    load_s = time.time() - t0

    times, text = [], ''
    for _ in range(runs):
        t0 = time.time()
        segments = model.transcribe(str(wav))
        times.append(time.time() - t0)
        text = ' '.join(s.text.strip() for s in segments)
    return {'load_s': load_s, 'times': times, 'text': text}


def bench_cleanup(text: str, model: str, runs: int):
    from llm_cleanup import LLMCleanup

    class Cfg:
        def get_setting(self, k, d=None):
            return {'llm_cleanup': True, 'llm_cleanup_model': model}.get(k, d)

    c = LLMCleanup(Cfg())
    out = c.cleanup(text)  # first call may include model load
    times = []
    for _ in range(runs):
        t0 = time.time()
        out = c.cleanup(text)
        times.append(time.time() - t0)
    return {'times': times, 'text': out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--wav', type=Path)
    ap.add_argument('--models', default='small,distil-large-v3')
    ap.add_argument('--llm', default='gemma3:4b')
    ap.add_argument('--runs', type=int, default=3)
    ap.add_argument('--record-seconds', type=float, default=5.0)
    args = ap.parse_args()

    wav = args.wav or record_with_timeout(args.record_seconds)

    print(f"\n## STT ({wav.name}, {args.runs} runs each)\n")
    print("| model | load | median transcribe | transcript |")
    print("|---|---|---|---|")
    best_text = ''
    for name in args.models.split(','):
        r = bench_stt(wav, name.strip(), args.runs)
        if not r:
            continue
        med = sorted(r['times'])[len(r['times']) // 2]
        print(f"| {name} | {r['load_s']:.2f}s | {med:.2f}s | {r['text'][:80]} |")
        best_text = best_text or r['text']

    if best_text:
        print(f"\n## LLM cleanup ({args.llm}, hot, {args.runs} runs)\n")
        r = bench_cleanup(best_text, args.llm, args.runs)
        med = sorted(r['times'])[len(r['times']) // 2]
        print(f"median: {med:.2f}s")
        print(f"cleaned: {r['text'][:120]}")


if __name__ == '__main__':
    main()
