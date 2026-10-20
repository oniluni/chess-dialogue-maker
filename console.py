"""Readable logs for people: one short line per event, in the terminal and the web UI.

    10:42:07 ▶ Транскрибация (faster-whisper small)
    10:42:19   Распознано реплик: 17, язык: en
    10:42:31 ⚠ Gemini перегружен (503), повтор через 10 с…
    10:43:02 ✓ Видео готово: jobs/…/final.mp4 (1:14)

Stage headers start with "▶", results with "✓" (both written by the pipeline);
warnings get "⚠", errors "✗". Tracebacks are only shown with `-v`.
In the terminal a long step (render) shows a live progress bar that log lines
never tear apart. `RunLog` collects the lines of one background run for the UI.
"""
from __future__ import annotations

import collections
import logging
import sys
import threading
import time

NOISY = (
    "httpx", "httpx2", "httpcore", "huggingface_hub", "faster_whisper", "urllib3", "PIL", "gradio",
    "onnxruntime", "google_genai", "google", "asyncio", "multipart", "uvicorn", "uvicorn.access",
    "fastapi", "matplotlib", "py.warnings",
)
_lock = threading.RLock()
_bar = ""  # current progress line (terminal only)


def _tty() -> bool:
    return sys.stderr.isatty()


class HumanFormatter(logging.Formatter):
    COLORS = {"dim": "\033[2m", "yellow": "\033[33m", "red": "\033[31m", "green": "\033[32m", "bold": "\033[1m", "off": "\033[0m"}

    def __init__(self, color: bool = False, verbose: bool = False, clock: bool = True):
        super().__init__()
        self.color, self.verbose, self.clock = color, verbose, clock

    def _c(self, name: str, text: str) -> str:
        return f"{self.COLORS[name]}{text}{self.COLORS['off']}" if self.color else text

    def format(self, r: logging.LogRecord) -> str:
        msg = r.getMessage()
        if r.levelno >= logging.ERROR:
            line = self._c("red", f"✗ {msg}")
        elif r.levelno >= logging.WARNING:
            line = self._c("yellow", f"⚠ {msg}")
        elif msg.startswith("▶"):
            line = self._c("bold", msg)
        elif msg.startswith("✓"):
            line = self._c("green", msg)
        elif r.levelno <= logging.DEBUG:
            line = self._c("dim", f"  {msg}")
        else:
            line = f"  {msg}"
        if self.verbose:
            line += self._c("dim", f"  [{r.name}]")
        if self.clock:
            line = self._c("dim", time.strftime("%H:%M:%S", time.localtime(r.created))) + " " + line
        if r.exc_info and self.verbose:
            line += "\n" + self.formatException(r.exc_info)
        return line


class ConsoleHandler(logging.StreamHandler):
    """Prints records above the live progress bar instead of through it."""

    def emit(self, record: logging.LogRecord) -> None:
        with _lock:
            if _bar and _tty():
                self.stream.write("\r\033[K")
            super().emit(record)
            if _bar and _tty():
                self.stream.write(_bar)
                self.stream.flush()


def setup(verbose: bool = False) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = ConsoleHandler(sys.stderr)
    handler.setFormatter(HumanFormatter(color=_tty(), verbose=verbose))
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    logging.captureWarnings(True)  # library warnings go through logging (hidden unless -v)
    for name in NOISY:
        logging.getLogger(name).setLevel(logging.INFO if verbose and name != "py.warnings" else logging.WARNING)
    if not verbose:
        logging.getLogger("py.warnings").setLevel(logging.ERROR)


def progress_bar(frac: float, desc: str) -> None:
    """CLI progress callback: a single self-updating line (only in a real terminal)."""
    global _bar
    if not _tty():
        return
    with _lock:
        if frac >= 1.0 or not desc:
            if _bar:
                sys.stderr.write("\r\033[K")
            _bar = ""
        else:
            filled = int(frac * 24)
            _bar = f"         {desc}  [{'█' * filled}{'░' * (24 - filled)}] {frac * 100:3.0f}%"
            sys.stderr.write("\r\033[K" + _bar)
        sys.stderr.flush()


def end_progress() -> None:
    progress_bar(1.0, "")


class RunLog(logging.Handler):
    """Collects the human-readable log lines emitted by one worker thread (web UI journal)."""

    def __init__(self, thread_id: int, keep: int = 200):
        super().__init__(logging.INFO)
        self.thread_id = thread_id
        self.lines: collections.deque[str] = collections.deque(maxlen=keep)
        self.setFormatter(HumanFormatter(color=False, verbose=False))

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread == self.thread_id and not record.name.startswith(NOISY):
            self.lines.append(self.format(record))

    def text(self, last: int = 60) -> str:
        return "\n".join(list(self.lines)[-last:])
