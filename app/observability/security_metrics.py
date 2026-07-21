"""
Security & Performance Metrics for sensitive endpoints
"""

from prometheus_client import Counter, Histogram

# Policy updates
POLICY_UPDATES = Counter(
    "nexa_policy_updates_total",
    "Total patient policy updates",
    ["status", "role", "via_simulator"],
)

# Assurance requests
ASSURANCE_REQUESTS = Counter(
    "nexa_assurance_requests_total", "Total push/biometric requests", ["type", "status"]
)

# Break-glass usage
BREAK_GLASS_REQUESTS = Counter(
    "nexa_break_glass_requests_total", "Total break-glass requests", ["status"]
)

# NFC resolution
NFC_RESOLVES = Counter(
    "nexa_nfc_resolves_total", "Total NFC card resolutions", ["status", "redirected"]
)

# Request latency for sensitive endpoints
SENSITIVE_ENDPOINT_LATENCY = Histogram(
    "nexa_sensitive_endpoint_duration_seconds",
    "Latency of sensitive endpoints",
    ["endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0],
)

AUDIT_LEDGER_INTEGRITY_FAILURES = Counter(
    "nexa_audit_ledger_integrity_failures_total",
    "Detected audit ledger forks or integrity violations",
    ["chain_scope", "reason"],
)
