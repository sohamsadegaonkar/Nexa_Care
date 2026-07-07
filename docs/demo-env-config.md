# Nexa Care Alpha Demo — Live Environment Configuration (`demo-env`)

**Milestone:** Alpha Demo (`v2.0.0-alpha`)  
**Target Host:** `https://demo-api.nexacare.ai`  
**Status:** LOCKED (No localhost or dummy placeholders permitted)

---

## 1. Required Runtime Environment Variables

Configure these exact variables in the live demo container runtime (Render / AWS / Docker ECS). Direct localhost URLs (`http://localhost:*` or `127.0.0.1`) are strictly rejected by security startup checks.

```bash
# ── Application Routing & CORS ────────────────────────────────────────────────
ENVIRONMENT="staging"
NEXT_PUBLIC_API_URL="https://demo-api.nexacare.ai"
CORS_ALLOWED_ORIGINS="https://demo.nexacare.ai,https://provider.demo.nexacare.ai"
TRUSTED_HOSTS="demo-api.nexacare.ai,demo.nexacare.ai"
MAX_UPLOAD_BYTES="20971520"  # 20 MB hard cap

# ── Primary Database (Postgres / Supabase Sharded Storage) ────────────────────
DATABASE_URL="postgresql+asyncpg://<username>:<password_from_secret_manager>@<db_host>:5432/<db_name>"
SUPABASE_URL="https://<project_id>.supabase.co"
SUPABASE_SERVICE_ROLE_KEY="<service_role_key_from_secret_manager>"

# ── Redis Store (Upstash TLS Consent & Concurrency Cache) ─────────────────────
UPSTASH_REDIS_URL="rediss://default:<token_from_secret_manager>@<upstash_host>:6379"

# ── Cryptographic Key Management & Sharding (Envelope DEK/KEK) ────────────────
ENCRYPTION_BACKEND="local"
KEK_ROOT_SECRET="<stored_in_render_or_github_secret_manager>"
PEPPER_SECRET="<stored_in_render_or_github_secret_manager>"

# ── Mobile Push Notification Service (Expo Cloud API) ─────────────────────────
EXPO_PUSH_API_URL="https://exp.host/--/api/v2/push/send"
PUSH_STATUS_TRANSPORT="poll"
```

---

## 2. Infrastructure Preflight Verification Check

Before routing traffic to the demo environment, operators must execute the automated setup verification script:
```bash
./scripts/setup_demo_env.sh
```
This script confirms TLS database connectivity, verifies Alembic schema migration health, registers demo patient identities, and outputs an explicit **GO / NO-GO** certification.
