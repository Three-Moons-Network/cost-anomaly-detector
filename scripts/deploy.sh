#!/usr/bin/env bash
set -euo pipefail

# Build Lambda deployment packages locally.
# Usage: ./scripts/deploy.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> Cleaning previous builds"
rm -rf "$PROJECT_DIR/dist"
mkdir -p "$PROJECT_DIR/dist/analyzer-pkg"
mkdir -p "$PROJECT_DIR/dist/query-pkg"

echo "==> Installing analyzer dependencies"
pip install -r "$PROJECT_DIR/requirements.txt" -t "$PROJECT_DIR/dist/analyzer-pkg/" --quiet

echo "==> Installing query dependencies"
pip install -r "$PROJECT_DIR/requirements.txt" -t "$PROJECT_DIR/dist/query-pkg/" --quiet

echo "==> Copying analyzer handler"
cp "$PROJECT_DIR/src/analyzer.py" "$PROJECT_DIR/dist/analyzer-pkg/"

echo "==> Copying query handler"
cp "$PROJECT_DIR/src/query.py" "$PROJECT_DIR/dist/query-pkg/"

echo "==> Creating analyzer zip"
cd "$PROJECT_DIR/dist/analyzer-pkg"
zip -r "$PROJECT_DIR/dist/analyzer.zip" . -q

echo "==> Creating query zip"
cd "$PROJECT_DIR/dist/query-pkg"
zip -r "$PROJECT_DIR/dist/query.zip" . -q

ANALYZER_SIZE=$(du -h "$PROJECT_DIR/dist/analyzer.zip" | cut -f1)
QUERY_SIZE=$(du -h "$PROJECT_DIR/dist/query.zip" | cut -f1)

echo "==> Done:"
echo "    dist/analyzer.zip ($ANALYZER_SIZE)"
echo "    dist/query.zip ($QUERY_SIZE)"
echo ""
echo "Next steps:"
echo "  cd terraform && terraform plan -out=tfplan"
echo "  terraform apply tfplan"
