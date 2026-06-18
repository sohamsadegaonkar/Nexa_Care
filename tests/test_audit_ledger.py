from app.observability.audit_ledger import _calculate_hash, _is_chain_conflict


def test_calculate_hash_is_order_independent():
    """json.dumps(..., sort_keys=True) means insertion order shouldn't
    change the hash -- if it did, the chain would be fragile to something
    as incidental as dict construction order."""
    payload_a = {"b": 1, "a": 2}
    payload_b = {"a": 2, "b": 1}
    assert _calculate_hash(payload_a, "GENESIS") == _calculate_hash(payload_b, "GENESIS")


def test_calculate_hash_changes_with_previous_hash():
    """This is the whole point of a hash chain: the same event payload
    must hash differently depending on what it's chained after."""
    payload = {"event": "x"}
    assert _calculate_hash(payload, "GENESIS") != _calculate_hash(payload, "some-other-hash")


def test_calculate_hash_changes_with_payload():
    assert _calculate_hash({"event": "x"}, "GENESIS") != _calculate_hash({"event": "y"}, "GENESIS")


def test_is_chain_conflict_detects_unique_violation_dict_shape():
    error = {"code": "23505", "message": "duplicate key value violates unique constraint \"uq_system_audit_previous_hash\""}
    assert _is_chain_conflict(error) is True


def test_is_chain_conflict_detects_unique_violation_object_shape():
    class FakePostgrestError(Exception):
        code = "23505"

    assert _is_chain_conflict(FakePostgrestError("duplicate key")) is True


def test_is_chain_conflict_ignores_unrelated_errors():
    assert _is_chain_conflict({"code": "08006", "message": "connection failure"}) is False
    assert _is_chain_conflict(ConnectionError("could not connect to host")) is False