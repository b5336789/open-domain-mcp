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


def test_download_rejects_path_traversal(client):
    """Reject path traversal attacks like ..%2fsecret or ../secret."""
    tc, _, tmp_path = client
    # Create a secret file outside the exports dir to ensure it can't be accessed
    (tmp_path / "secret.txt").write_text("secret data")

    # Try to access it via traversal in the URL
    resp = tc.get("/api/export/..%2fsecret/download")
    assert resp.status_code == 404

    # Also test the non-URL-encoded version (Starlette will decode it)
    resp = tc.get("/api/export/../secret/download")
    assert resp.status_code == 404


def test_download_rejects_dots_in_id(client):
    """Reject IDs with dots that could be used in traversal attempts."""
    tc, _, _ = client
    # IDs with dots should be rejected
    resp = tc.get("/api/export/has.dot.dots/download")
    assert resp.status_code == 404


def test_download_allows_valid_hex_id(client):
    """Ensure valid uuid4().hex format IDs (32 lowercase hex) still work."""
    import zipfile

    tc, _, tmp_path = client
    exports = tmp_path / "exports"
    exports.mkdir(parents=True)

    # Create a valid hex-format ID zip
    valid_hex_id = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
    with zipfile.ZipFile(exports / f"{valid_hex_id}.zip", "w") as z:
        z.writestr("index.md", "# valid")

    resp = tc.get(f"/api/export/{valid_hex_id}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
