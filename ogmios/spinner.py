"""A status line on stderr, redrawn with a spinner and the seconds spent on the current
step, while `ask` searches, loads the model and waits for it to read the notes.

It is drawn from a thread: llama.cpp and ONNX Runtime release the GIL while they compute,
so it keeps turning during the slow steps. It writes to its own copy of the stderr file
descriptor: llama-cpp-python points descriptor 2 at /dev/null while it loads a model.
"""

import os
import sys
import threading
import time
from typing import Self, TextIO

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
CLEAR = "\r\033[K"


class Spinner:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self._text = ""
        self._since = time.monotonic()
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self._out: TextIO = sys.stderr

    def __enter__(self) -> Self:
        if self.enabled:
            self._out = open(os.dup(sys.stderr.fileno()), "w", encoding="utf-8")
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def status(self, text: str) -> None:
        with self._lock:
            self._text, self._since = text, time.monotonic()
        self._draw(0)

    def print(self, message: str) -> None:
        """A line of its own above the status, which is redrawn on the next frame."""
        with self._lock:
            print((CLEAR if self.enabled else "") + message, file=self._out, flush=True)

    def stop(self) -> None:
        """Clear the status line, for the answer to take its place."""
        if self._thread is None:
            return
        self._done.set()
        self._thread.join()
        self._thread = None
        self._out.write(CLEAR)
        self._out.close()
        self._out = sys.stderr

    def _run(self) -> None:
        frame = 1
        while not self._done.wait(0.1):
            self._draw(frame)
            frame += 1

    def _draw(self, frame: int) -> None:
        if not self.enabled or self._done.is_set():
            return
        with self._lock:
            seconds = int(time.monotonic() - self._since)
            elapsed = f"  {seconds}s" if seconds else ""
            line = f"{FRAMES[frame % len(FRAMES)]} {self._text}{elapsed}"
            self._out.write(CLEAR + line)
            self._out.flush()
