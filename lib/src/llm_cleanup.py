"""
LLM transcript cleanup via a local Ollama server.

Optional post-processing stage between transcription and injection:
fixes punctuation/capitalization and strips filler words using a small
local model, without ever rephrasing the content. Disabled by default
(`llm_cleanup: false`); any error, timeout, or suspicious output falls
back to the original text — cleanup must never eat a dictation.
"""

import json
import threading
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

DEFAULT_PROMPT = (
    "You clean up speech-to-text transcripts. Fix punctuation and "
    "capitalization. Remove filler words (um, uh, you know, like — only "
    "when used as filler). Never rephrase, "
    "never add or remove content, never answer questions that appear in "
    "the text. Output only the cleaned transcript, nothing else."
)


class LLMCleanup:
    """Ollama-backed transcript cleanup stage."""

    def __init__(self, config_manager=None):
        self.config_manager = config_manager
        self._warmed = False

    # ------------------------ Config ------------------------

    def _get(self, key, default):
        if not self.config_manager:
            return default
        value = self.config_manager.get_setting(key, default)
        return default if value is None else value

    @property
    def enabled(self) -> bool:
        return bool(self._get('llm_cleanup', False))

    @property
    def model(self) -> str:
        return str(self._get('llm_cleanup_model', 'gemma3:4b'))

    @property
    def base_url(self) -> str:
        return str(self._get('llm_cleanup_url', 'http://localhost:11434')).rstrip('/')

    @property
    def timeout(self) -> float:
        return float(self._get('llm_cleanup_timeout', 8.0))

    @property
    def keep_alive(self) -> str:
        return str(self._get('llm_cleanup_keep_alive', '30m'))

    @property
    def instruction(self) -> str:
        return str(self._get('llm_cleanup_prompt', DEFAULT_PROMPT))

    # ------------------------ Warm-up ------------------------

    def warm(self):
        """Preload the model into Ollama (cold load can take 20s+).

        Fire-and-forget background thread; safe to call unconditionally —
        no-op when cleanup is disabled or the server is unreachable.
        """
        if not self.enabled or self._warmed:
            return
        self._warmed = True
        threading.Thread(target=self._warm_request, daemon=True,
                         name='llm-cleanup-warmup').start()

    def _warm_request(self):
        try:
            self._generate('', num_predict=1, timeout=120.0)
            print(f"LLM cleanup: model '{self.model}' warmed", flush=True)
        except Exception as e:
            print(f"LLM cleanup: warm-up failed ({e}) — "
                  f"first dictation may be slow or fall back to raw", flush=True)

    # ------------------------ Cleanup ------------------------

    def cleanup(self, text: str) -> str:
        """Return the cleaned transcript, or the original text on any failure.

        Newlines (from "new line" voice commands) are structural: small
        models reliably eat them, so each line is cleaned as a separate
        request (in parallel) and the line structure is reassembled verbatim.
        """
        if not self.enabled or not text or not text.strip():
            return text

        lines = text.split('\n')
        if len(lines) == 1:
            return self._cleanup_segment(text)
        with ThreadPoolExecutor(max_workers=min(4, len(lines))) as pool:
            return '\n'.join(pool.map(self._cleanup_segment, lines))

    def _cleanup_segment(self, text: str) -> str:
        if not text.strip():
            return text
        try:
            cleaned = self._generate(
                f"{self.instruction}\n\nTranscript: {text}\n\nCleaned:",
                num_predict=max(128, len(text.split()) * 4),
                timeout=self.timeout,
            ).strip()
        except Exception as e:
            print(f"LLM cleanup failed ({e}) — using raw transcript", flush=True)
            return text

        if not self._sane(text, cleaned):
            print("LLM cleanup output rejected by sanity check — using raw transcript",
                  flush=True)
            return text
        return cleaned

    @staticmethod
    def _sane(original: str, cleaned: str) -> bool:
        """Reject empty or wildly-resized output (likely hallucination)."""
        if not cleaned:
            return False
        orig_len = len(original.strip())
        ratio = len(cleaned) / max(orig_len, 1)
        # Cleanup only removes filler and adjusts punctuation: output should
        # never balloon, and shrinking to under a third means content was eaten
        # (short inputs get slack — "um yes" → "Yes." is a big relative change).
        if orig_len > 40 and not (0.3 <= ratio <= 1.5):
            return False
        return True

    # ------------------------ Ollama API ------------------------

    def _generate(self, prompt: str, num_predict: int, timeout: float) -> str:
        payload = json.dumps({
            'model': self.model,
            'prompt': prompt,
            'stream': False,
            'keep_alive': self.keep_alive,
            'options': {'temperature': 0, 'num_predict': num_predict},
        }).encode('utf-8')
        req = urllib.request.Request(
            f'{self.base_url}/api/generate',
            data=payload,
            headers={'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8')).get('response', '')
