from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one anchor, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "app/services/patient_auth_service.py",
    """from app.services.patient_session_authority import (\n    PatientSessionAuthorityUnavailable,\n    create_patient_session,\n    get_or_create_patient_session_epoch,\n    resolve_patient_session_id,\n)\n""",
    """from app.services.patient_session_authority import (\n    PatientSessionAuthorityUnavailable,\n    _epoch_key,\n    _session_key,\n    create_patient_session,\n    get_or_create_patient_session_epoch,\n    resolve_patient_session_id,\n)\n""",
)

replace_once(
    "app/services/patient_auth_service.py",
    '''async def finalize_device_enrollment_token(token: str, claim_id: str) -> bool:\n    redis = get_redis_client()\n    key = _token_key(token)\n    claim_key = _CLAIM_PREFIX + key\n    if hasattr(redis, "eval"):\n        script = """\n        if redis.call('GET', KEYS[2]) == ARGV[1] then\n            redis.call('DEL', KEYS[1], KEYS[2])\n            return 1\n        end\n        return 0\n        """\n        try:\n            consumed = await _maybe_await(\n                redis.eval(script, 2, key, claim_key, claim_id)\n            )\n        except Exception as exc:\n            raise PatientSessionAuthorityUnavailable(\n                "Device enrollment authority store is unavailable"\n            ) from exc\n        return bool(consumed)\n    try:\n        current = await _maybe_await(redis.get(claim_key))\n    except Exception as exc:\n        raise PatientSessionAuthorityUnavailable(\n            "Device enrollment authority store is unavailable"\n        ) from exc\n    if isinstance(current, bytes):\n        current = current.decode()\n    if current != claim_id:\n        return False\n    try:\n        await _maybe_await(redis.delete(key, claim_key))\n    except Exception as exc:\n        raise PatientSessionAuthorityUnavailable(\n            "Device enrollment authority store is unavailable"\n        ) from exc\n    return True\n''',
    '''async def finalize_device_enrollment_token(\n    token: str,\n    claim_id: str,\n    *,\n    patient_id: str,\n    auth_session_id: str,\n) -> bool:\n    """Consume one reserved grant only while its exact session is still current.\n\n    Real Redis uses one Lua linearization point across the grant, claim, exact\n    session row, and patient-wide session epoch. A logout/revoke that wins before\n    finalization therefore makes finalization fail closed and burns the stale\n    reservation instead of allowing a later PostgreSQL authority mutation.\n    """\n\n    redis = get_redis_client()\n    key = _token_key(token)\n    claim_key = _CLAIM_PREFIX + key\n    if hasattr(redis, "eval"):\n        script = """\n        local grant_raw = redis.call('GET', KEYS[1])\n        if not grant_raw then return 0 end\n        if redis.call('GET', KEYS[2]) ~= ARGV[1] then return 0 end\n\n        local grant_ok, grant = pcall(cjson.decode, grant_raw)\n        if not grant_ok\n           or grant['scope'] ~= 'device_enrollment'\n           or grant['patient_id'] ~= ARGV[2]\n           or grant['auth_session_id'] ~= ARGV[3] then\n            return 0\n        end\n\n        local session_raw = redis.call('GET', KEYS[3])\n        local current_epoch = redis.call('GET', KEYS[4])\n        if not session_raw or not current_epoch then\n            redis.call('DEL', KEYS[1], KEYS[2])\n            return 0\n        end\n        local session_ok, session = pcall(cjson.decode, session_raw)\n        if not session_ok\n           or session['status'] ~= 'active'\n           or session['patient_id'] ~= ARGV[2]\n           or tostring(session['session_epoch']) ~= tostring(current_epoch) then\n            redis.call('DEL', KEYS[1], KEYS[2])\n            return 0\n        end\n\n        redis.call('DEL', KEYS[1], KEYS[2])\n        return 1\n        """\n        try:\n            consumed = await _maybe_await(\n                redis.eval(\n                    script,\n                    4,\n                    key,\n                    claim_key,\n                    _session_key(auth_session_id),\n                    _epoch_key(patient_id),\n                    claim_id,\n                    patient_id,\n                    auth_session_id,\n                )\n            )\n        except Exception as exc:\n            raise PatientSessionAuthorityUnavailable(\n                "Device enrollment authority store is unavailable"\n            ) from exc\n        return bool(consumed)\n\n    # Test/local Redis doubles may not implement Lua. Revalidate immediately\n    # before deletion; production qualification requires the atomic Lua branch.\n    session = await resolve_patient_session_id(\n        patient_id=patient_id, session_id=auth_session_id\n    )\n    if session is None:\n        return False\n    try:\n        raw = await _maybe_await(redis.get(key))\n        current = await _maybe_await(redis.get(claim_key))\n    except Exception as exc:\n        raise PatientSessionAuthorityUnavailable(\n            "Device enrollment authority store is unavailable"\n        ) from exc\n    if isinstance(raw, bytes):\n        raw = raw.decode()\n    if isinstance(current, bytes):\n        current = current.decode()\n    try:\n        grant = json.loads(raw) if raw else None\n    except (TypeError, json.JSONDecodeError):\n        grant = None\n    if (\n        current != claim_id\n        or not isinstance(grant, dict)\n        or grant.get("scope") != "device_enrollment"\n        or grant.get("patient_id") != patient_id\n        or grant.get("auth_session_id") != auth_session_id\n    ):\n        return False\n    try:\n        await _maybe_await(redis.delete(key, claim_key))\n    except Exception as exc:\n        raise PatientSessionAuthorityUnavailable(\n            "Device enrollment authority store is unavailable"\n        ) from exc\n    return True\n''',
)

replace_once(
    "app/api/v2/device_routes.py",
    '''        finalized = await finalize_device_enrollment_token(\n            payload.device_enrollment_token, claim_id\n        )\n''',
    '''        finalized = await finalize_device_enrollment_token(\n            payload.device_enrollment_token,\n            claim_id,\n            patient_id=patient_id,\n            auth_session_id=patient.session_id,\n        )\n''',
)

replace_once(
    "tests/test_patient_otp_auth.py",
    "assert await finalize_device_enrollment_token(token, claim)",
    "assert await finalize_device_enrollment_token(\n            token, claim, patient_id=\"patient-1\", auth_session_id=SESSION_A\n        )",
)

replace_once(
    "tests/integration/test_patient_enrollment_session_redis.py",
    "assert await finalize_device_enrollment_token(grant, claim) is True",
    "assert (\n                await finalize_device_enrollment_token(\n                    grant,\n                    claim,\n                    patient_id=patient_a,\n                    auth_session_id=session_a,\n                )\n                is True\n            )",
)
replace_once(
    "tests/integration/test_patient_enrollment_session_redis.py",
    "assert await finalize_device_enrollment_token(grant, winners[0]) is True",
    "assert (\n                await finalize_device_enrollment_token(\n                    grant,\n                    winners[0],\n                    patient_id=patient,\n                    auth_session_id=session_id,\n                )\n                is True\n            )",
)

replace_once(
    "tests/test_device_consent.py",
    'finalize.assert_awaited_once_with("e" * 43, "claim-1")',
    'finalize.assert_awaited_once_with(\n                "e" * 43,\n                "claim-1",\n                patient_id=mock_scoped_session,\n                auth_session_id="patient-session-device-consent-1234567890",\n            )',
)

# Add a real-Redis race proving logout-all wins over a reserved grant before finalize.
p = Path("tests/integration/test_patient_enrollment_session_redis.py")
text = p.read_text(encoding="utf-8")
anchor = "\n\nclass _UnavailableRedis:\n"
if text.count(anchor) != 1:
    raise SystemExit("redis integration insertion anchor mismatch")
race_test = r'''

@pytest.mark.asyncio
async def test_logout_all_between_claim_and_finalize_burns_reserved_grant(real_redis):
    patient = str(uuid.uuid4())
    session_id = f"session-{uuid.uuid4().hex}"
    tokens: list[str] = []
    await _create_live_session(real_redis, patient, "subject-a", session_id)
    with (
        patch("app.services.patient_auth_service.get_redis_client", return_value=real_redis),
        patch(
            "app.services.patient_session_authority.get_redis_client", return_value=real_redis
        ),
    ):
        try:
            grant = await issue_device_enrollment_token(patient, session_id)
            tokens.append(grant)
            claim = await claim_device_enrollment_token(grant, patient, session_id)
            assert claim is not None

            await revoke_all_patient_sessions(patient)

            assert (
                await finalize_device_enrollment_token(
                    grant,
                    claim,
                    patient_id=patient,
                    auth_session_id=session_id,
                )
                is False
            )
            assert await claim_device_enrollment_token(grant, patient, session_id) is None
        finally:
            await _cleanup(real_redis, [patient], [session_id], tokens)
'''
p.write_text(text.replace(anchor, race_test + anchor, 1), encoding="utf-8")
