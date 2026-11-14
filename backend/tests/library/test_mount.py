from backend.app import app


def test_library_routes_mounted_beside_day7():
    paths = app.openapi()["paths"]
    assert paths["/api/library/documents"]["get"]["tags"] == ["Library"]
    assert paths["/api/library/chat"]["post"]["tags"] == ["Library"]
    assert paths["/api/documents"]["get"]["tags"] == ["Documents"]   # day7's own, until stage 6 unifies them
    assert "/api/chat" in paths and "Library" not in paths["/api/chat"]["post"]["tags"]
