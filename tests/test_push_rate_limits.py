import asyncio

import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.core.dependencies import get_current_provider, get_db_session
from app.core.rate_limiter import ConcurrentPushLimiter

class FakeRedis:
    def __init__(self):
        self.data = {}
    async def get(self, k):
        return self.data.get(k)
    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.data:
            return False
        self.data[k] = v
        return True
    async def setex(self, k, t, v):
        self.data[k] = v
        return True
    async def incr(self, k):
        val = int(self.data.get(k, 0)) + 1
        self.data[k] = str(val)
        return val
    async def expire(self, k, t):
        return True
    async def delete(self, k):
        return self.data.pop(k, None) is not None
    async def close(self):
        pass

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def mock_redis():
    return FakeRedis()

@pytest.fixture
def mock_db():
    db = AsyncMock()
    # Mocking the execute -> result -> scalar_one_or_none() chain
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=mock_result)
    return db


@pytest.mark.asyncio
async def test_atomic_concurrent_acquire_allows_exactly_one(mock_redis):
    patient_id = str(uuid.uuid4())
    provider_id = str(uuid.uuid4())
    limiter = ConcurrentPushLimiter(redis_client=mock_redis)

    async def attempt():
        try:
            await limiter.check_and_acquire(patient_id, provider_id)
            return True
        except Exception:
            return False

    results = await asyncio.gather(attempt(), attempt())
    assert results.count(True) == 1
    assert results.count(False) == 1


@pytest.mark.asyncio
async def test_lock_released_when_rate_limit_fails(mock_redis):
    patient_id = str(uuid.uuid4())
    provider_id = str(uuid.uuid4())
    await mock_redis.setex(f"nexa:push_rate:provider:{provider_id}", 300, "10")
    limiter = ConcurrentPushLimiter(redis_client=mock_redis)

    with pytest.raises(Exception):
        await limiter.check_and_acquire(patient_id, provider_id)

    assert await mock_redis.get(f"nexa:push_concurrent:{patient_id}") is None

@pytest.mark.asyncio
async def test_concurrent_request_blocked(client, mock_redis, mock_db):
    patient_id = str(uuid.uuid4())
    provider_id = str(uuid.uuid4())
    
    # Pre-populate pending request
    await mock_redis.setex(f"nexa:push_concurrent:{patient_id}", 100, "1")
    
    app.dependency_overrides[get_current_provider] = lambda: MagicMock(actor_uid=provider_id)
    app.dependency_overrides[get_db_session] = lambda: mock_db
    
    with patch("app.core.rate_limiter.get_redis_config"), \
         patch("redis.asyncio.from_url", return_value=mock_redis):
        
        payload = {"patient_id": patient_id, "provider_id": provider_id, "purpose": "t", "scope": "s"}
        response = client.post("/api/v2/push/request", json=payload, headers={"Authorization": "Bearer doc"})
        
        assert response.status_code == 429
        assert "already pending" in response.json()["detail"]
    
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_provider_rate_limit_exceeded(client, mock_redis, mock_db):
    patient_id = "p1"
    provider_id = "d1"
    
    # Set counter to 10
    await mock_redis.setex(f"nexa:push_rate:provider:{provider_id}", 300, "10")
    
    app.dependency_overrides[get_current_provider] = lambda: MagicMock(actor_uid=provider_id)
    app.dependency_overrides[get_db_session] = lambda: mock_db
    
    with patch("app.core.rate_limiter.get_redis_config"), \
         patch("redis.asyncio.from_url", return_value=mock_redis):
        
        payload = {"patient_id": patient_id, "provider_id": provider_id, "purpose": "t", "scope": "s"}
        response = client.post("/api/v2/push/request", json=payload, headers={"Authorization": "Bearer doc"})
        
        assert response.status_code == 429
        assert "Rate limit exceeded for provider" in response.json()["detail"]
    
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_patient_hourly_limit_exceeded(client, mock_redis, mock_db):
    patient_id = "p1"
    provider_id = "d1"
    
    # Set counter to 5
    await mock_redis.setex(f"nexa:push_rate:patient:{patient_id}", 3600, "5")
    
    app.dependency_overrides[get_current_provider] = lambda: MagicMock(actor_uid=provider_id)
    app.dependency_overrides[get_db_session] = lambda: mock_db
    
    with patch("app.core.rate_limiter.get_redis_config"), \
         patch("redis.asyncio.from_url", return_value=mock_redis):
        
        payload = {"patient_id": patient_id, "provider_id": provider_id, "purpose": "t", "scope": "s"}
        response = client.post("/api/v2/push/request", json=payload, headers={"Authorization": "Bearer doc"})
        
        assert response.status_code == 429
        assert "Rate limit exceeded for patient" in response.json()["detail"]
    
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_fail_closed_on_redis_failure(client, mock_db, mock_redis):
    app.dependency_overrides[get_current_provider] = lambda: MagicMock(actor_uid="d1")
    app.dependency_overrides[get_db_session] = lambda: mock_db
    
    with patch("redis.asyncio.from_url", side_effect=Exception("Redis down")), \
         patch("app.api.v2.assurance_routes.get_redis_client", return_value=mock_redis), \
         patch("app.services.assurance_service.AssuranceService.create_push_request", return_value={"status": "pending", "request_id": "req-1"}):
        
        payload = {"patient_id": "p1", "provider_id": "d1", "purpose": "t", "scope": "s"}
        response = client.post("/api/v2/push/request", json=payload, headers={"Authorization": "Bearer doc"})
        
        assert response.status_code == 503
    
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_release_clears_concurrency_lock(mock_redis):
    patient_id = "p1"
    await mock_redis.setex(f"nexa:push_concurrent:{patient_id}", 100, "1")
    from app.core.rate_limiter import ConcurrentPushLimiter
    await ConcurrentPushLimiter(redis_client=mock_redis).release(patient_id)
    assert await mock_redis.get(f"nexa:push_concurrent:{patient_id}") is None

@pytest.mark.asyncio
async def test_release_on_timeout(client, mock_redis, mock_db):
    patient_id = "p1"
    request_id = str(uuid.uuid4())
    
    await mock_redis.setex(f"nexa:push_concurrent:{patient_id}", 100, "1")
    
    app.dependency_overrides[get_current_provider] = lambda: MagicMock()
    app.dependency_overrides[get_db_session] = lambda: mock_db
    
    # Mock status check returns timeout
    with patch("app.core.rate_limiter.get_redis_config"), \
         patch("redis.asyncio.from_url", return_value=mock_redis), \
         patch("app.api.v2.assurance_routes.get_redis_client", return_value=mock_redis), \
         patch("app.services.assurance_service.AssuranceService.get_push_status", return_value={"status": "timeout", "patient_id": patient_id}):
        
        client.get(f"/api/v2/push/{request_id}/status")
        
        # Verify lock was deleted
        assert await mock_redis.get(f"nexa:push_concurrent:{patient_id}") is None
        
    app.dependency_overrides.clear()
