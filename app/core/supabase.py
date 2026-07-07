from functools import lru_cache
from supabase import create_client, Client
from app.core.config import get_supabase_config

# [FINDING #11 FIX]: Cache the Supabase client to prevent connection churn.
# This ensures only one client instance exists per worker process.
@lru_cache()
def get_supabase_client() -> Client:
    """Returns a singleton Supabase client instance."""
    cfg = get_supabase_config()
    return create_client(cfg.url, cfg.key)