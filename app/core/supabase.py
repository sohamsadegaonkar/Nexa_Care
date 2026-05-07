"""Supabase client factory for Nexa Care.

Centralizing client creation helps with:
- keeping credentials in one place
- easier mocking/testing later
"""

from supabase import Client, create_client

from app.core.config import get_supabase_config


def get_supabase_client() -> Client:
    cfg = get_supabase_config()
    return create_client(cfg.url, cfg.key)