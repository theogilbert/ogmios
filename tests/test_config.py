import pytest

from ogmios import config


def test_add_and_remove_sources_keep_comments(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('# my notes\n\n[[sources]]\npath = "~/a.md"  # work\n')
    config.add_source(path, {"path": str(tmp_path), "delimiters": [r"^## \d{4}"]})
    config.add_source(path, {"path": "~/b.md", "day_first": False})
    sources = config.load(path).sources
    assert [s.options.delimiters[0].pattern for s in sources[1:2]] == [r"^## \d{4}"]
    assert sources[2].options.day_first is False
    assert r"delimiters = ['^## \d{4}']" in path.read_text()

    config.remove_source(path, tmp_path)
    assert path.read_text() == (
        '# my notes\n\n[[sources]]\npath = "~/a.md"  # work\n\n'
        '[[sources]]\npath = "~/b.md"\nday_first = false\n'
    )
    with pytest.raises(ValueError, match="not a configured source"):
        config.remove_source(path, tmp_path)
    with pytest.raises(ValueError, match="already a source"):
        config.add_source(path, {"path": "~/b.md"})


def test_set_model_before_the_first_table(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('# my notes\n[[sources]]\npath = "~/a.md"\n')
    config.set_model(path, "e5-small")
    config.set_model(path, "e5-small-int8")
    assert path.read_text() == (
        '# my notes\nmodel = "e5-small-int8"\n\n[[sources]]\npath = "~/a.md"\n'
    )


def test_invalid_edits_are_not_written(tmp_path):
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="invalid split"):
        config.add_source(path, {"path": "~/a.md", "split": "weekly"})
    with pytest.raises(ValueError, match="unknown model"):
        config.set_model(path, "gpt")
    assert not path.exists()
