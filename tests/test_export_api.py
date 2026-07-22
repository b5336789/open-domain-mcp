"""Export task runner + download endpoint."""
from pathlib import Path

from tests.test_api import client  # noqa: F401 - reused fixture

from opendomainmcp.tasks.runners import RUNNERS, run_export


class FakeTask:
    id = "t1"
    params = {"no_llm": True}


class RecordingStore:
    def __init__(self):
        self.updates = []

    def update(self, task_id, throttle=False, **kw):
        self.updates.append(kw)


def test_export_registered_in_runners():
    assert RUNNERS["export"] is run_export


def test_run_export_writes_zip_and_result(tmp_path):
    from tests.test_export_documents import FakeCtx
    from tests.test_export_collect import FakeGraph, FakeStore, _article_item, _rule_item

    ctx = FakeCtx(FakeStore([_article_item(1), _rule_item(1)]),
                  FakeGraph({}), tmp_path)
    store = RecordingStore()
    run_export(ctx, store, FakeTask(), is_cancelled=lambda: False)
    zip_path = Path(tmp_path) / "exports" / "t1.zip"
    assert zip_path.exists()
    result = store.updates[-1]["result"]
    assert result["counts"]["articles"] == 1
    assert result["zip_path"] == str(zip_path)


def test_download_404_before_export(client):
    tc, _, _ = client
    assert tc.get("/api/export/nope/download").status_code == 404


def test_download_serves_zip(client):
    import zipfile

    tc, _, tmp_path = client            # Settings(data_dir=tmp_path)
    exports = tmp_path / "exports"
    exports.mkdir(parents=True)
    with zipfile.ZipFile(exports / "t9.zip", "w") as z:
        z.writestr("index.md", "# hi")
    resp = tc.get("/api/export/t9/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
