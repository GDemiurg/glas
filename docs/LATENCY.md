# Measured latency — RTX 3060 Ti (8 GB), CUDA 13.3, CachyOS

Test clip: `jfk.wav` (11 s speech, 16 kHz mono). 3 runs each, median.
Regenerate: `.venv/bin/python utils/benchmark_latency.py --wav <file>`
(or without `--wav` to record 5 s from the mic).

## STT (pywhispercpp, CUDA, model resident in VRAM)

| model | load (once) | transcribe 11 s clip |
|---|---|---|
| small | 0.61 s | **0.11 s** |
| distil-large-v3 | 0.40 s | **0.17 s** |

## LLM cleanup (Ollama gemma3:4b, 100% GPU)

| state | latency |
|---|---|
| hot (kept via `keep_alive: 30m`, warmed at daemon start) | **0.68 s** |
| cold load (first call, if not warmed) | ~25 s |

## End-to-end feel (release hotkey → text at cursor)

- cleanup ON: ≈ 0.9–1.0 s (cleanup dominates; STT is nearly free)
- cleanup OFF: ≈ 0.2–0.3 s

## VRAM budget (8 GB card)

gemma3:4b resident ≈ 2.7 GB + distil-large-v3 ≈ 2.5 GB + desktop ≈ 1.5 GB.
Fits, but a second whisper instance (e.g. the benchmark while the daemon
runs) OOMs the CUDA VMM pool — stop the daemon before benchmarking.

## Tuning notes

- distil-large-v3 chosen as default: near-small speed, better accuracy;
  **English only** — for Bulgarian switch `model` to `small` (multilingual)
  and expect ~same latency.
- Cleanup cost scales with transcript length (~4 tokens/word generated).
  For long dictations expect 1–2 s.
- Multi-line dictations run one cleanup request per line in parallel;
  Ollama serializes them, so each extra line adds ~0.3–0.7 s worst case.
