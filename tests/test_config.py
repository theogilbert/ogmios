import pytest

from ogmios import config


def test_add_and_remove_sources_keep_comments(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('# my notes\n\n[[sources]]\npath = "~/a.md"  # work\n')
    config.add_source(
        path, {"name": "dir", "path": str(tmp_path), "delimiters": [r"^## \d{4}"]}
    )
    config.add_source(path, {"path": "~/b.md", "day_first": False})
    sources = config.load(path).sources
    assert [s.name for s in sources] == ["a", "dir", "b"]
    assert [s.options.delimiters[0].pattern for s in sources[1:2]] == [r"^## \d{4}"]
    assert sources[2].options.day_first is False
    assert r"delimiters = ['^## \d{4}']" in path.read_text()

    assert config.remove_source(path, "dir").path == tmp_path
    assert path.read_text() == (
        '# my notes\n\n[[sources]]\npath = "~/a.md"  # work\n\n'
        '[[sources]]\npath = "~/b.md"\nday_first = false\n'
    )
    with pytest.raises(ValueError, match="no source named 'dir' \\(sources: a, b\\)"):
        config.remove_source(path, "dir")
    with pytest.raises(ValueError, match="already source 'b'"):
        config.add_source(path, {"name": "other", "path": "~/b.md"})
    with pytest.raises(ValueError, match="already a source named 'b'"):
        config.add_source(path, {"path": "~/elsewhere/b.md"})


def test_gitlab_issues_source(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    path = tmp_path / "config.toml"
    url = "https://gitlab.example.com:8443/group/sub/proj"
    config.add_source(
        path,
        {
            "type": "gitlab-issues",
            "url": "https://gitlab.example.com:8443/group/sub/proj.git",
        },
    )
    (source,) = config.load(path).sources
    assert (source.name, source.type, source.url) == ("proj", "gitlab-issues", url)
    assert source.fetched
    assert source.path == (
        tmp_path / "data/ogmios/gitlab-issues/gitlab.example.com_8443/group/sub/proj"
    )
    with pytest.raises(ValueError, match="already source 'proj'"):
        config.add_source(
            path, {"name": "x", "type": "gitlab-issues", "url": url + "/-/issues"}
        )
    assert config.remove_source(path, "proj").url == url
    assert path.read_text() == ""


def test_source_names(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[[sources]]\npath = "~/a/done.md"\n[[sources]]\npath = "~/b/done.md"\n'
    )
    with pytest.raises(ValueError, match="two sources named 'done'"):
        config.load(path)
    with pytest.raises(ValueError, match="invalid source name"):
        config.add_source(tmp_path / "other.toml", {"name": "a b", "path": "~/c.md"})
    with pytest.raises(ValueError, match="unknown source type"):
        config.add_source(tmp_path / "other.toml", {"type": "jira", "url": "x"})
    with pytest.raises(ValueError, match="not a GitLab project URL"):
        config.add_source(
            tmp_path / "other.toml", {"type": "gitlab-issues", "url": "gitlab.com/a/b"}
        )


def test_set_values_before_the_first_table(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('# my notes\n[[sources]]\npath = "~/a.md"\n')
    config.set_value(path, "model", "e5-small")
    config.set_value(path, "llm", "gemma-4-e2b")
    config.set_value(path, "model", "e5-small-int8")
    assert path.read_text() == (
        '# my notes\nmodel = "e5-small-int8"\nllm = "gemma-4-e2b"\n'
        '\n[[sources]]\npath = "~/a.md"\n'
    )


def test_invalid_edits_are_not_written(tmp_path):
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="invalid split"):
        config.add_source(path, {"path": "~/a.md", "split": "weekly"})
    with pytest.raises(ValueError, match="unknown model"):
        config.set_value(path, "model", "gpt")
    with pytest.raises(ValueError, match="unknown llm"):
        config.set_value(path, "llm", "gpt")
    assert not path.exists()


def test_any_gguf_file_as_llm(tmp_path):
    path = tmp_path / "config.toml"
    config.set_value(path, "llm", "org/repo/model.gguf")
    assert config.load(path).llm == "org/repo/model.gguf"
    path.write_text("[llm]\nrepo = 'org/repo'\n")
    with pytest.raises(ValueError, match="llm must be a string"):
        config.load(path)


def test_threads(tmp_path, monkeypatch):
    monkeypatch.delenv("OGMIOS_THREADS")
    path = tmp_path / "config.toml"
    assert config.load(path).threads is None
    config.set_value(path, "threads", 3)
    assert path.read_text() == "threads = 3\n"
    assert config.load(path).threads == 3
    monkeypatch.setenv("OGMIOS_THREADS", "2")
    assert config.load(path).threads == 2
    monkeypatch.setenv("OGMIOS_THREADS", "0")
    assert config.load(path).threads is None  # no cap
    monkeypatch.setenv("OGMIOS_THREADS", "many")
    with pytest.raises(ValueError, match="invalid threads"):
        config.load(path)
