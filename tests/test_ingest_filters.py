"""IngestFilter: deterministic business-meaning filtering (spec 2026-07-06)."""

from pathlib import Path

from opendomainmcp.ingest.filters import IngestFilter, _parse_exclude_spec


def test_default_excludes_match_test_and_lock_files(tmp_path):
    flt = IngestFilter()
    root = tmp_path
    assert flt.exclusion_reason(root / "src" / "test_billing.py", root) == "test_*.py"
    assert flt.exclusion_reason(root / "app" / "billing.spec.ts", root) == "*.spec.ts"
    assert (
        flt.exclusion_reason(root / "package-lock.json", root) == "package-lock.json"
    )
    assert flt.exclusion_reason(root / "static" / "app.min.js", root) == "*.min.js"


def test_directory_patterns_match_any_segment(tmp_path):
    flt = IngestFilter()
    root = tmp_path
    assert flt.exclusion_reason(root / "tests" / "helper.py", root) == "tests/"
    assert flt.exclusion_reason(root / "pkg" / "vendor" / "lib.go", root) == "vendor/"
    assert flt.exclusion_reason(root / "db" / "migrations" / "0001.sql", root) == "migrations/"
    # A *file* named like a dir pattern is not a directory match.
    assert flt.exclusion_reason(root / "src" / "vendor", root) is None


def test_business_files_pass(tmp_path):
    flt = IngestFilter()
    root = tmp_path
    for rel in ("src/billing.py", "src/OrderService.java", "docs/pricing.md",
                "frontend/checkout.ts"):
        assert flt.exclusion_reason(root / Path(rel), root) is None


def test_extra_excludes_layer_over_defaults(tmp_path):
    flt = IngestFilter(extra_excludes=("*.sql", "legacy/"))
    root = tmp_path
    assert flt.exclusion_reason(root / "proc" / "billing.sql", root) == "*.sql"
    assert flt.exclusion_reason(root / "legacy" / "old.java", root) == "legacy/"
    # defaults still apply
    assert flt.exclusion_reason(root / "test_x.py", root) == "test_*.py"


def test_no_defaults_mode_keeps_only_user_rules(tmp_path):
    flt = IngestFilter(extra_excludes=("*.sql",), use_defaults=False)
    root = tmp_path
    assert flt.exclusion_reason(root / "test_x.py", root) is None
    assert flt.exclusion_reason(root / "a.sql", root) == "*.sql"


def test_parse_exclude_spec_splits_commas_and_newlines():
    assert _parse_exclude_spec("*.sql, legacy/\n*.tmp\n\n") == [
        "*.sql", "legacy/", "*.tmp"
    ]
    assert _parse_exclude_spec("") == []
