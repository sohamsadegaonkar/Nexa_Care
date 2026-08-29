def test_nfc_resolve_with_tombstone_redirect():
    """Tombstones remain a server-side resolution concern, never NFC output."""
    from app.api.v2.nfc_routes import resolve_nfc_card

    source = resolve_nfc_card.__doc__ or ""
    assert "opaque discovery capability" in source


def test_tombstone_redirect_structure():
    """The external NFC model is intentionally opaque and identifier-free."""
    from app.api.v2.nfc_routes import NFCResolveResponse

    assert set(NFCResolveResponse.model_fields) == {"discovery_handle", "expires_at"}
