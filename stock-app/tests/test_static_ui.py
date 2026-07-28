from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_built_angular_assets_and_spa_routes_are_served(tmp_path: Path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>smallFish</html>")
    (static / "main.js").write_text("console.log('smallFish')")
    monkeypatch.setenv("SFP_STATIC_DIR", str(static))
    client = TestClient(app)

    assert client.get("/").text == "<html>smallFish</html>"
    assert client.get("/momentum").text == "<html>smallFish</html>"
    assert client.get("/studies").text == "<html>smallFish</html>"
    assert "console.log" in client.get("/main.js").text


def test_static_asset_path_cannot_escape_the_built_ui_root(tmp_path: Path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>smallFish</html>", encoding="utf-8")
    secret = tmp_path / "outside.txt"
    secret.write_text("must not be served", encoding="utf-8")
    monkeypatch.setenv("SFP_STATIC_DIR", str(static))
    client = TestClient(app)

    response = client.get("/%2e%2e/outside.txt")

    assert "must not be served" not in response.text


# ---------------------------------------------- SPA / API route collisions

def test_browser_navigation_to_a_colliding_route_serves_the_app(tmp_path, monkeypatch):
    """/options and /portfolios are both Angular routes and API routes.

    In single-server mode the API router matches first, so before this a user
    who typed the URL or refreshed the page got raw JSON instead of the
    dashboard.
    """
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<app-root></app-root>", encoding="utf-8")
    monkeypatch.setenv("SFP_STATIC_DIR", str(static))
    client = TestClient(app)

    for path in ("/options", "/portfolios"):
        # What a browser sends when navigating.
        response = client.get(path, headers={
            "sec-fetch-dest": "document",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
        assert response.status_code == 200, path
        assert "<app-root>" in response.text, path
        assert response.headers["vary"] == "Sec-Fetch-Dest", path


def test_the_api_still_answers_json_clients_on_those_paths(tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<app-root></app-root>", encoding="utf-8")
    monkeypatch.setenv("SFP_STATIC_DIR", str(static))
    client = TestClient(app)

    for path in ("/options", "/portfolios"):
        # A browser fetch/XHR. Angular's HttpClient sets no Accept header, so
        # the request must be identified by Sec-Fetch-Dest, not by Accept.
        response = client.get(path, headers={"sec-fetch-dest": "empty"})
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("application/json"), path
        assert response.headers["vary"] == "Sec-Fetch-Dest", path


def test_a_script_fetch_that_sends_no_accept_header_still_reaches_the_api(tmp_path, monkeypatch):
    """Angular's HttpClient sends no Accept header at all.

    An Accept-based rule served the dashboard's own XHR the HTML page, which
    surfaced as 'Http failure during parsing' on the Portfolios view.
    """
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<app-root></app-root>", encoding="utf-8")
    monkeypatch.setenv("SFP_STATIC_DIR", str(static))
    client = TestClient(app)

    for path in ("/options", "/portfolios"):
        response = client.get(path, headers={"sec-fetch-dest": "empty", "accept": "*/*"})
        assert response.headers["content-type"].startswith("application/json"), path


def test_a_client_sending_neither_header_is_treated_as_an_api_client(tmp_path, monkeypatch):
    """curl and scripts must keep working exactly as before."""
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<app-root></app-root>", encoding="utf-8")
    monkeypatch.setenv("SFP_STATIC_DIR", str(static))
    client = TestClient(app)

    response = client.get("/portfolios")
    assert response.headers["content-type"].startswith("application/json")


def test_a_colliding_route_still_serves_the_api_when_no_ui_is_built(tmp_path, monkeypatch):
    """A backend-only deployment must not start 404ing its own API."""
    monkeypatch.setenv("SFP_STATIC_DIR", str(tmp_path / "absent"))
    client = TestClient(app)
    response = client.get("/options", headers={"sec-fetch-dest": "document"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
