from opendomainmcp.config import Settings


def test_overrides_roundtrip(tmp_path):
    s = Settings(data_dir=tmp_path)
    assert s.chunk_size == 1200
    updated = s.save_overrides({"chunk_size": 500, "extract_knowledge": False})
    assert updated.chunk_size == 500
    # A freshly loaded Settings picks the override back up.
    reloaded = Settings(data_dir=tmp_path).apply_overrides()
    assert reloaded.chunk_size == 500
    assert reloaded.extract_knowledge is False


def test_save_overrides_rejects_non_editable(tmp_path):
    s = Settings(data_dir=tmp_path)
    try:
        s.save_overrides({"data_dir": "/etc"})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_retrieve_include_articles_defaults_on_and_is_editable(tmp_path):
    s = Settings(data_dir=tmp_path)
    assert s.retrieve_include_articles is True
    # runtime-editable: update_editable must accept it without "Not editable"
    updated = s.save_overrides({"retrieve_include_articles": False})
    assert updated.retrieve_include_articles is False
