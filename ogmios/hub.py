"""Model files from Hugging Face, at pinned revisions so that cached vectors always match
the model.

Only `ogmios install` downloads them. Every other command reads what it installed, and
fails, pointing at it, when a file is missing.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DOWNLOAD_ATTEMPTS = 5
HUB = "https://huggingface.co"


def model_dir(cache_dir: Path, repo: str, revision: str) -> Path:
    return cache_dir / repo.replace("/", "--") / revision


def installed(path: Path | None, what: str) -> Path:
    """`path`, if the file is there; None is a model not pinned to a revision yet."""
    if path is None or not path.exists():
        raise ValueError(f"{what} is not installed, run `ogmios install`")
    return path


def url(repo: str, revision: str, filename: str) -> str:
    return f"{HUB}/{repo}/resolve/{revision}/{filename}"


def latest_revision(repo: str) -> str:
    """The commit a repository's main branch is at, to pin a model to."""
    try:
        api = f"{HUB}/api/models/{repo}/revision/main"
        with urllib.request.urlopen(api, timeout=30) as response:
            return json.load(response)["sha"]
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            raise ValueError(
                f"no public repository {repo} on Hugging Face"
                " (gated and private ones are not supported)"
            ) from None
        raise


def remote_size(repo: str, revision: str, filename: str) -> int | None:
    request = urllib.request.Request(url(repo, revision, filename), method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.headers["Content-Length"])
    except (OSError, KeyError, ValueError, TypeError):
        return None


def download(repo: str, revision: str, filename: str, directory: Path) -> Path:
    """Download a file into `directory`, showing progress on a terminal.

    The LLM is gigabytes: a dropped connection resumes where it stopped, even in a later
    run, rather than starting over.
    """
    path = directory / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    for attempt in range(DOWNLOAD_ATTEMPTS):
        done = partial.stat().st_size if partial.exists() else 0
        request = urllib.request.Request(
            url(repo, revision, filename), headers={"Range": f"bytes={done}-"}
        )
        try:
            # 200 rather than 206: the server sends the whole file again.
            with (
                urllib.request.urlopen(request, timeout=30) as response,
                partial.open("ab" if response.status == 206 else "wb") as f,
            ):
                written = done if response.status == 206 else 0
                total = written + int(response.headers.get("Content-Length", 0))
                while block := response.read(1 << 20):
                    f.write(block)
                    written += len(block)
                    _progress(filename, written, total)
            break
        except urllib.error.HTTPError as e:
            if e.code != 416:  # nothing left to send: the file is complete
                raise
            break
        except OSError as e:
            if attempt == DOWNLOAD_ATTEMPTS - 1:
                raise
            wait = 2 ** (attempt + 1)
            print(
                f"\nogmios: download interrupted ({e}), resuming in {wait}s",
                file=sys.stderr,
            )
            time.sleep(wait)
    if sys.stderr.isatty():
        print(file=sys.stderr)
    partial.replace(path)
    return path


def _progress(filename: str, written: int, total: int) -> None:
    if sys.stderr.isatty():
        print(
            f"\r  {filename}  {human(written)} / {human(total)}",
            end="",
            file=sys.stderr,
            flush=True,
        )


def human(size: float) -> str:
    for unit in ("B", "kB", "MB"):
        if size < 1000:
            return f"{size:.0f} {unit}"
        size /= 1000
    return f"{size:.1f} GB"
