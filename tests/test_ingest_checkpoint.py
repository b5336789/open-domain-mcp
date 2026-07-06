"""Async + resumable ingest (Feature 1): checkpoint persistence, file-level
resume, signature invalidation, cancellation, and the async job endpoints."""

import time

import pytest
from fastapi.testclient import TestClient

from opendomainmcp.api.app import create_app
from opendomainmcp.config import Settings
from opendomainmcp.context import Context
from opendomainmcp.ingest.checkpoint import Checkpoint, extractor_signature


def _corpus(root):
    root.mkdir(exist_ok=True, parents=True)
    (root / "a.py").write_text("def a():\n    return 1\n")
    (root / "b.md").write_text("Beta documents the billing flow.\n")


# --- checkpoint persistence ------------------------------------------------

def test_checkpoint_atomic_round_trip(tmp_path):
    cp = Checkpoint.new(tmp_path, "job1", "/x", False, "sig")
    cp.mark_file_done("/x/a.py")
    cp.files_indexed = 1
    cp.status = "running"
    cp.save()

    assert cp.path.exists()
    # the temp file used for the atomic write must not linger
    assert not cp.path.with_name(cp.path.name + ".tmp").exists()

    reloaded = Checkpoint.load(cp.path)
    assert reloaded.is_file_done("/x/a.py")
    assert reloaded.files_indexed == 1
    assert reloaded.signature == "sig"
    assert reloaded.status == "running"


# --- pipeline resume behavior ----------------------------------------------

def test_resume_skips_completed_files(tmp_path, pipeline, fake_extractor):
    src = tmp_path / "src"
    _corpus(src)
    sig = extractor_signature(pipeline._settings)
    cp = Checkpoint.new(tmp_path, "j", src, False, sig)

    r1 = pipeline.ingest_path(str(src), checkpoint=cp)
    assert r1.files_indexed == 2
    calls_after_first = fake_extractor.calls
    assert calls_after_first > 0

    # Re-run with the SAME checkpoint: every file already done -> all skipped,
    # no new extraction (the expensive stage) happens.
    r2 = pipeline.ingest_path(str(src), checkpoint=cp)
    assert r2.files_indexed == 0
    assert fake_extractor.calls == calls_after_first


def test_signature_change_reextracts_everything(tmp_path, pipeline, fake_extractor):
    src = tmp_path / "src"
    _corpus(src)
    # A checkpoint whose files are "done" but under a stale extraction signature.
    cp = Checkpoint.new(tmp_path, "j", src, False, "STALE-SIGNATURE")
    cp.mark_file_done(str(src / "a.py"))
    cp.mark_file_done(str(src / "b.md"))

    r = pipeline.ingest_path(str(src), checkpoint=cp)
    # signature mismatch resets completed_files -> both files re-extracted
    assert r.files_indexed == 2
    assert fake_extractor.calls > 0


def test_cancel_stops_before_processing(tmp_path, pipeline):
    src = tmp_path / "src"
    _corpus(src)
    sig = extractor_signature(pipeline._settings)
    cp = Checkpoint.new(tmp_path, "j", src, False, sig)
    cp.request_cancel()

    r = pipeline.ingest_path(str(src), checkpoint=cp)
    assert r.files_indexed == 0  # cancelled at the top of the file loop


# --- async job endpoints ---------------------------------------------------

@pytest.fixture
def client(store, pipeline, fake_graph, tmp_path):
    settings = Settings(data_dir=tmp_path)
    ctx = Context(settings=settings, store=store, pipeline=pipeline, graph=fake_graph)
    app = create_app(context=ctx, context_factory=lambda: ctx)
    return TestClient(app), ctx, tmp_path


def _poll_until_terminal(tc, job_id, tries=200):
    for _ in range(tries):
        st = tc.get(f"/api/ingest/jobs/{job_id}").json()
        if st["status"] in ("done", "error", "cancelled"):
            return st
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never finished: {st}")


def test_async_ingest_runs_and_reports(client):
    tc, _, tmp_path = client
    src = tmp_path / "corpus"
    _corpus(src)

    resp = tc.post("/api/ingest/async", params={"path": str(src)})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    status = _poll_until_terminal(tc, job_id)
    assert status["status"] == "done"
    assert status["report"]["files_indexed"] == 2

    # the content is searchable once the background job finished
    results = tc.post("/api/search", json={"query": "billing flow"}).json()
    assert isinstance(results, list) and len(results) >= 1


def _job_id_for(ctx, path):
    import hashlib
    collection = ctx.store.stats().get("collection", "")
    return hashlib.sha256(f"{collection}\n{path}".encode()).hexdigest()[:32]


def test_same_path_yields_stable_job_id(client):
    tc, ctx, tmp_path = client
    src = tmp_path / "c2"
    _corpus(src)
    j1 = tc.post("/api/ingest/async", params={"path": str(src)}).json()["job_id"]
    _poll_until_terminal(tc, j1)
    # resubmitting the same path maps to the same job id (collection+path keyed)
    j2 = tc.post("/api/ingest/async", params={"path": str(src)}).json()["job_id"]
    assert j1 == j2 == _job_id_for(ctx, str(src))


def test_resume_interrupted_job_skips_completed_file(client):
    tc, ctx, tmp_path = client
    src = tmp_path / "c3"
    _corpus(src)

    # Simulate an interrupted run: a checkpoint that finished a.py then errored.
    job_id = _job_id_for(ctx, str(src))
    cp = Checkpoint.new(ctx.settings.data_dir, job_id, str(src), False,
                        extractor_signature(ctx.settings))
    cp.mark_file_done(str(src / "a.py"))
    cp.status = "error"
    cp.save()

    resp = tc.post("/api/ingest/async", params={"path": str(src)})
    assert resp.json()["job_id"] == job_id
    status = _poll_until_terminal(tc, job_id)
    assert status["status"] == "done"
    # a.py was already done -> only b.md is newly ingested this run
    assert status["report"]["files_indexed"] == 1
    assert status["progress"]["completed_files"] == 2


def test_job_status_unknown_is_404(client):
    tc, _, _ = client
    assert tc.get("/api/ingest/jobs/does-not-exist").status_code == 404


def test_cancel_unknown_job_is_404(client):
    tc, _, _ = client
    assert tc.delete("/api/ingest/jobs/does-not-exist").status_code == 404


def test_extractor_signature_includes_codegraph_mode():
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.checkpoint import extractor_signature

    on = extractor_signature(Settings(codegraph_extract=True))
    off = extractor_signature(Settings(codegraph_extract=False))
    assert on != off
