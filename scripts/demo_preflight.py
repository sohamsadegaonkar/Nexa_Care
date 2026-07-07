import os
import asyncio
import httpx
import sys
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
import redis.asyncio as redis_async

# Add root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import get_database_config, get_redis_config

# Colors
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"

async def check_redis():
    print("Checking Redis connection... ", end="")
    try:
        cfg = get_redis_config()
        r = redis_async.from_url(cfg.url)
        await r.ping()
        print(f"{GREEN}OK{RESET}")
        return True
    except Exception as e:
        print(f"{RED}FAILED: {e}{RESET}")
        return False

async def check_postgres():
    print("Checking Postgres connection... ", end="")
    try:
        cfg = get_database_config()
        engine = create_async_engine(cfg.url)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        print(f"{GREEN}OK{RESET}")
        return True
    except Exception as e:
        print(f"{RED}FAILED: {e}{RESET}")
        return False

async def check_expo():
    print("Checking Expo Push API reachability... ", end="")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://exp.host/--/api/v2/push/send", timeout=5.0)
            # GET on this URL should return 405 Method Not Allowed or similar, but connectivity is what matters
            if resp.status_code in [200, 405]:
                print(f"{GREEN}OK{RESET}")
                return True
            else:
                print(f"{RED}FAILED (HTTP {resp.status_code}){RESET}")
                return False
    except Exception as e:
        print(f"{RED}FAILED: {e}{RESET}")
        return False

async def check_test_data():
    print("Checking test data presence... ", end="")
    try:
        cfg = get_database_config()
        engine = create_async_engine(cfg.url)
        async with engine.connect() as conn:
            # Check provider
            p = await conn.execute(text("SELECT id FROM provider_identity LIMIT 1"))
            if not p.first():
                print(f"{RED}FAILED (No provider data){RESET}")
                return False
            
            # Check patient/nfc card
            c = await conn.execute(text("SELECT card_uid FROM nfc_card_registry LIMIT 1"))
            if not c.first():
                print(f"{RED}FAILED (No NFC card data){RESET}")
                return False
                
        print(f"{GREEN}OK{RESET}")
        return True
    except Exception as e:
        print(f"{RED}FAILED: {e}{RESET}")
        return False

async def main():
    print(f"{BOLD}Nexa Care Demo Pre-flight Checks{RESET}\n")
    
    results = [
        await check_redis(),
        await check_postgres(),
        await check_expo(),
        await check_test_data()
    ]
    
    print("\n" + "=" * 40)
    if all(results):
        print(f"{BOLD}{GREEN}RESULT: GO! System is ready for demo.{RESET}")
    else:
        print(f"{BOLD}{RED}RESULT: NO-GO! Please fix the errors above.{RESET}")
    print("=" * 40 + "\n")

if __name__ == "__main__":
    asyncio.run(main())
