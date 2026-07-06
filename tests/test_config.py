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


def test_retrieve_include_graph_defaults_off_and_is_editable():
    from opendomainmcp.config import EDITABLE_FIELDS, Settings
    assert Settings().retrieve_include_graph is False
    assert Settings(retrieve_include_graph=True).retrieve_include_graph is True
    assert "retrieve_include_graph" in EDITABLE_FIELDS


def test_extract_batch_defaults_off_and_is_editable():
    from opendomainmcp.config import EDITABLE_FIELDS, Settings
    s = Settings()
    assert s.extract_batch is False
    assert "extract_batch" in EDITABLE_FIELDS


def test_codegraph_context_chars_registered_and_env_only():
    from opendomainmcp.config import EDITABLE_FIELDS, Settings
    assert Settings().codegraph_context_chars == 16000
    assert Settings(codegraph_context_chars=8000).codegraph_context_chars == 8000
    assert "codegraph_context_chars" not in EDITABLE_FIELDS
