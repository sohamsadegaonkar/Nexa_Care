#!/bin/bash
set -euo pipefail

# Environment defaults for verification
export DATABASE_URL=${DATABASE_URL:-"postgresql+asyncpg://user:pass@localhost/db"}
export REDIS_URL=${REDIS_URL:-"redis://localhost:6379/0"}
export API_BASE_URL=${API_BASE_URL:-"http://localhost:8000"}
: "${KEK_ROOT_SECRET:?Set KEK_ROOT_SECRET in the test environment}"

echo "=== Running full integration suite ==="

echo "=== Step 1: Lint ==="
ruff check .

echo "=== Step 2: Unit tests ==="
# Ignore integration directory to keep this step focused on fast unit checks
python -m pytest tests/ -q --ignore=tests/integration/

echo "=== Step 3: Integration tests ==="
python -m pytest tests/integration/ -v --tb=long

echo "=== Step 4: Audit chain verification ==="
# Note: This requires a live/test database connection
python scripts/verify_audit_chain.py

echo "=== Step 5: Encryption round-trip verification ==="
# This script runs a full E2E flow against the local instance
python scripts/verify_encryption_e2e.py

echo "=== All checks passed ==="
