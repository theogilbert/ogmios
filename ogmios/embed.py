"""Sentence embeddings with ONNX Runtime.

The model and its tokenizer are downloaded from Hugging Face on first use, at a pinned
revision so that cached vectors always match the model, then only ever read from disk.
Loading them is most of a search's run time, the tokenizer above all: its 250k-word
vocabulary covers ~100 languages. It is pruned once to the Latin script (see
`prune_tokenizer`), which more than halves its load time and memory.
"""

import json
import os
import shutil
import sys
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    import onnxruntime
    import tokenizers

# ONNX Runtime >= 1.31 queues usage events (OS, CPU, process path, a device id) for
# Microsoft's collector. `disable_telemetry_events()` does not stop it; this does, as
# long as it is set before onnxruntime is imported.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

BATCH_SIZE = 16
PAD_ID = 1  # XLM-RoBERTa's <pad>


@dataclass(frozen=True)
class ModelSpec:
    repo: str
    revision: str
    model_file: str
    max_tokens: int = 512
    query_prefix: str = "query: "
    passage_prefix: str = "passage: "


MODELS = {
    "e5-small-int8": ModelSpec(
        "Xenova/multilingual-e5-small",
        "761b726dd34fb83930e26aab4e9ac3899aa1fa78",
        "onnx/model_int8.onnx",
    ),
    "e5-small": ModelSpec(
        "intfloat/multilingual-e5-small",
        "614241f622f53c4eeff9890bdc4f31cfecc418b3",
        "onnx/model.onnx",
    ),
    "e5-base-int8": ModelSpec(
        "Xenova/multilingual-e5-base",
        "1ec9243030a27d1a115d5c340572074c125b58b2",
        "onnx/model_int8.onnx",
    ),
}
DEFAULT_MODEL = "e5-small-int8"


class Embedder:
    def __init__(self, name: str, cache_dir: Path) -> None:
        if name not in MODELS:
            raise ValueError(
                f"unknown model {name!r}, expected one of: {', '.join(MODELS)}"
            )
        self.name = name
        self.spec = MODELS[name]
        self.dir = cache_dir / self.spec.repo.replace("/", "--") / self.spec.revision
        self._loaded: (
            tuple[tokenizers.Tokenizer, np.ndarray, onnxruntime.InferenceSession] | None
        ) = None

    @property
    def downloaded(self) -> bool:
        return (self.dir / self.spec.model_file).exists()

    def passages(self, texts: Iterable[str]) -> "np.ndarray":
        prefix = self.spec.passage_prefix
        return self._embed([prefix + t for t in texts])

    def query(self, text: str) -> "np.ndarray":
        return self._embed([self.spec.query_prefix + text])[0]

    def _embed(self, texts: list[str]) -> "np.ndarray":
        import numpy as np

        # Not in a background thread: the tokenizer holds the GIL while it loads.
        if self._loaded is None:
            self._loaded = (*self._load_tokenizer(), self._load_session())
        tokenizer, original_ids, session = self._loaded
        wanted = {i.name for i in session.get_inputs()}

        # Batching texts of similar length keeps padding, and so time and memory, down.
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        result = np.zeros((len(texts), session.get_outputs()[0].shape[-1]), np.float32)
        for start in range(0, len(order), BATCH_SIZE):
            batch = order[start : start + BATCH_SIZE]
            encodings = tokenizer.encode_batch([texts[i] for i in batch])
            ids = original_ids[np.array([e.ids for e in encodings], dtype=np.int64)]
            mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in wanted:
                feeds["token_type_ids"] = np.zeros_like(ids)
            hidden = np.asarray(session.run(None, feeds)[0])
            # Mean pooling over the real tokens, then L2 normalisation.
            pooled = (hidden * mask[..., None]).sum(axis=1) / mask.sum(
                axis=1, keepdims=True
            )
            result[batch] = pooled / np.linalg.norm(pooled, axis=1, keepdims=True)
        return result

    def _load_tokenizer(self) -> "tuple[tokenizers.Tokenizer, np.ndarray]":
        """The pruned tokenizer, and the model's id for each of its token ids."""
        import numpy as np
        import tokenizers

        pruned, ids = (
            self.dir / "tokenizer.latin.json",
            self.dir / "tokenizer.latin.ids.npy",
        )
        if not pruned.exists() or not ids.exists():
            prune_tokenizer(self._fetch("tokenizer.json"), pruned, ids)
        tokenizer = tokenizers.Tokenizer.from_file(str(pruned))
        tokenizer.enable_truncation(max_length=self.spec.max_tokens)
        tokenizer.enable_padding(pad_id=PAD_ID, pad_token="<pad>")
        return tokenizer, np.load(ids)

    def _load_session(self) -> "onnxruntime.InferenceSession":
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3  # errors only
        return onnxruntime.InferenceSession(
            str(self._fetch(self.spec.model_file)),
            options,
            providers=["CPUExecutionProvider"],
        )

    def _fetch(self, filename: str) -> Path:
        path = self.dir / filename
        if path.exists():
            return path
        url = f"https://huggingface.co/{self.spec.repo}/resolve/{self.spec.revision}/{filename}"
        print(f"ogmios: downloading {url}", file=sys.stderr)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        with (
            urllib.request.urlopen(url, timeout=30) as response,
            partial.open("wb") as f,
        ):
            shutil.copyfileobj(response, f, 1 << 20)
        partial.replace(path)
        return path


def _latin(char: str) -> bool:
    """Latin letters and diacritics, punctuation, symbols and emoji."""
    code = ord(char)
    return (
        code < 0x0250
        or 0x1E00 <= code < 0x1F00
        or 0x2000 <= code < 0x2C00
        or code >= 0x1F000
    )


def prune_tokenizer(source: Path, target: Path, ids_target: Path) -> None:
    """Drop the multi-character vocabulary entries of non-Latin scripts.

    A unigram tokenizer only ever matches an entry against text holding the same
    characters, so text in Latin script is tokenized exactly as before. Every single
    character is kept, so text in other scripts still tokenizes, character by character.
    Token ids shift; `ids_target` maps them back to the model's.
    """
    import numpy as np

    data = json.loads(source.read_text(encoding="utf-8"))
    vocab = data["model"]["vocab"]
    keep = [
        i
        for i, (piece, _) in enumerate(vocab)
        # The first four are the special tokens, kept at their ids: <s> <pad> </s> <unk>
        if i < 4 or len(piece.lstrip("▁")) <= 1 or all(_latin(c) for c in piece)
    ]
    data["model"]["vocab"] = [vocab[i] for i in keep]
    data["added_tokens"] = [t for t in data["added_tokens"] if t["id"] < 4]

    partial = target.with_name(target.name + ".part")
    partial.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    with ids_target.open("wb") as f:
        np.save(f, np.array(keep, dtype=np.int64))
    partial.replace(target)
