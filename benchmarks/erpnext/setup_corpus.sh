#!/usr/bin/env bash
# Materialize the pinned ERPNext accounting corpus and ingest it into a clean
# 'erpnext' collection. Idempotent: safe to re-run. Re-establishes the exact
# basis the benchmark questions were written against.
#
# Usage:  benchmarks/erpnext/setup_corpus.sh [--no-ingest]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
MANIFEST="$HERE/manifest.json"

SHA="$(grep -o '"pinned_commit": *"[0-9a-f]*"' "$MANIFEST" | grep -o '[0-9a-f]\{40\}')"
CORPUS_DIR="$HERE/.corpus/accounting"
WORK="$HERE/.corpus/.erpnext-src"

echo "==> Pinned ERPNext commit: $SHA"
mkdir -p "$CORPUS_DIR"

# Fetch only the pinned commit, sparse, blobless (GitHub allows fetching a
# reachable SHA). Falls back to the develop branch with a loud warning.
if [ ! -d "$WORK/.git" ]; then
  git init -q "$WORK"
  git -C "$WORK" remote add origin https://github.com/frappe/erpnext.git
fi
git -C "$WORK" config core.sparseCheckout true
git -C "$WORK" sparse-checkout init --cone >/dev/null 2>&1 || true
git -C "$WORK" sparse-checkout set \
  erpnext/controllers \
  erpnext/accounts/doctype/pricing_rule \
  erpnext/accounts/doctype/tax_rule >/dev/null

if git -C "$WORK" fetch --depth 1 --filter=blob:none origin "$SHA" 2>/dev/null; then
  git -C "$WORK" checkout -q FETCH_HEAD
else
  echo "!! WARNING: could not fetch pinned SHA $SHA directly; falling back to develop."
  echo "!! Corpus may differ from the one the questions were authored against."
  git -C "$WORK" fetch --depth 1 --filter=blob:none origin develop
  git -C "$WORK" checkout -q FETCH_HEAD
fi

# Stage the four files under the names the benchmark expects.
cp "$WORK/erpnext/controllers/taxes_and_totals.py"                "$CORPUS_DIR/taxes_and_totals.py"
cp "$WORK/erpnext/accounts/doctype/pricing_rule/pricing_rule.py"  "$CORPUS_DIR/pricing_rule.py"
cp "$WORK/erpnext/accounts/doctype/pricing_rule/utils.py"         "$CORPUS_DIR/pricing_rule_utils.py"
cp "$WORK/erpnext/accounts/doctype/tax_rule/tax_rule.py"          "$CORPUS_DIR/tax_rule.py"

echo "==> Staged corpus:"
( cd "$CORPUS_DIR" && shasum -a 256 ./*.py )
echo "    (compare against sha256 in manifest.json to confirm the pin held)"

if [ "${1:-}" = "--no-ingest" ]; then
  echo "==> --no-ingest: skipping ingestion."
  exit 0
fi

echo "==> Ingesting into the 'erpnext' collection (clean, isolated)..."
# IMPORTANT: pass --collection as a flag. An inline ODM_COLLECTION_NAME would be
# clobbered by run.sh sourcing .env (the flag is honoured by build_context).
"$REPO/run.sh" ingest "$CORPUS_DIR" --collection erpnext --sync
echo "==> Done. Run the benchmark with:"
echo "    $REPO/run.sh stats --collection erpnext   # sanity"
echo "    .venv/bin/python benchmarks/erpnext/run_benchmark.py --collection erpnext"
