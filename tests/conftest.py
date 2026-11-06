"""Tests use 4 CPU threads unless told otherwise, like scripts/eval.py."""

import os

os.environ.setdefault("OGMIOS_THREADS", "4")
