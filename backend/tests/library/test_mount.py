from backend.app import app


def test_library_routes_mounted_beside_day7():
    paths = app.openapi()["paths"]
    assert paths["/api/library/documents"]["get"]["tags"] == ["Library"]
    assert paths["/api/library/documents/{doc_id}/rename"]["post"]["tags"] == ["Library"]
    assert paths["/api/documents"]["get"]["tags"] == ["Documents"]
    # the renamer's own chat went in stage 7: one chat, at /api/chat
    assert not any(p.startswith(("/api/library/chat", "/api/library/sessions")) for p in paths)
    assert "/api/chat" in paths and "Library" not in paths["/api/chat"]["post"]["tags"]
