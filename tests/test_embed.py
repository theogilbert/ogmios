"""Vocabulary pruning, on a tiny unigram tokenizer rather than the real model's."""

import numpy as np
import tokenizers
from tokenizers import models, pre_tokenizers

from ogmios.embed import prune_tokenizer

VOCAB = [
    ("<s>", 0.0),
    ("<pad>", 0.0),
    ("</s>", 0.0),
    ("<unk>", 0.0),
    ("▁", -2.0),
    ("▁ré", -3.0),
    ("union", -3.0),
    ("▁réunion", -1.0),
    ("▁при", -1.0),  # Cyrillic: dropped
    ("при", -2.0),  # Cyrillic: dropped
    ("п", -5.0),
    ("р", -5.0),
    ("и", -5.0),
    ("é", -5.0),
    ("r", -5.0),
] + [(c, -5.0) for c in "eunio"]


def _tokenizer(tmp_path):
    tokenizer = tokenizers.Tokenizer(models.Unigram(VOCAB, unk_id=3))
    tokenizer.pre_tokenizer = pre_tokenizers.Metaspace()
    path = tmp_path / "tokenizer.json"
    tokenizer.save(str(path))
    return path


def test_latin_text_tokenizes_identically(tmp_path):
    source = _tokenizer(tmp_path)
    pruned, ids = tmp_path / "pruned.json", tmp_path / "ids.npy"
    prune_tokenizer(source, pruned, ids)

    full = tokenizers.Tokenizer.from_file(str(source))
    small = tokenizers.Tokenizer.from_file(str(pruned))
    mapping = np.load(ids)
    assert small.get_vocab_size() == len(VOCAB) - 2
    for text in ["réunion", "union réunion", "ré union"]:
        assert mapping[small.encode(text).ids].tolist() == full.encode(text).ids


def test_other_scripts_fall_back_to_characters(tmp_path):
    source = _tokenizer(tmp_path)
    pruned, ids = tmp_path / "pruned.json", tmp_path / "ids.npy"
    prune_tokenizer(source, pruned, ids)

    tokens = tokenizers.Tokenizer.from_file(str(pruned)).encode("при").tokens
    assert tokens == ["▁", "п", "р", "и"]
