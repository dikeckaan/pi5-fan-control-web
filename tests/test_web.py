import pytest

from pifan.web import create_app


@pytest.fixture
def client(controller, store):
    app = create_app(controller, store)
    controller.tick()
    return app.test_client()


def test_status(client):
    data = client.get("/api/status").get_json()
    assert data["max_level"] == 4 and "alarms" in data


def test_security_headers(client):
    resp = client.get("/api/status")
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Frame-Options"] == "DENY"


def test_mode_and_level(client, store):
    assert client.post("/api/mode", json={"mode": "manual"}).status_code == 200
    assert client.post("/api/manual-level", json={"level": 3}).status_code == 200
    assert store.get()["mode"] == "manual" and store.get()["manual_level"] == 3
    resp = client.post("/api/manual-level", json={"level": 7})
    assert resp.status_code == 400 and resp.get_json()["error"] == "invalid_level"


def test_rejects_form_posts_and_foreign_origin(client):
    assert client.post("/api/mode", data={"mode": "auto"}).status_code == 415
    resp = client.post("/api/mode", json={"mode": "auto"},
                       headers={"Origin": "http://evil.example"})
    assert resp.status_code == 403


def test_boost_bounds(client):
    assert client.post("/api/boost", json={"seconds": 9999}).status_code == 400
    assert client.post("/api/boost", json={"seconds": 10}).status_code == 200
    assert client.delete("/api/boost", json={}).get_json()["cancelled"] is True


def test_config_patch_and_import(client, store):
    resp = client.patch("/api/config", json={"auto_thresholds": {"0": 0, "60": 4}})
    assert resp.status_code == 200
    resp = client.patch("/api/config", json={"auto_thresholds": {"0": 3, "60": 1}})
    assert resp.get_json()["error"] == "curve_not_monotonic"
    exported = client.get("/api/config/export")
    assert "attachment" in exported.headers["Content-Disposition"]
    cfg = exported.get_json()
    cfg["mode"] = "manual"
    assert client.post("/api/config/import", json=cfg).status_code == 200
    assert store.get()["mode"] == "manual"


def test_password_protection(controller, store):
    client = create_app(controller, store, password="hunter2").test_client()
    assert client.get("/api/status").status_code == 401
    assert client.get("/").status_code == 302
    assert client.get("/healthz").status_code in (200, 503)
    assert client.post("/api/login", json={"password": "nope"}).status_code == 401
    assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200
    assert client.get("/api/status").status_code == 200


def test_login_lockout(controller, store):
    client = create_app(controller, store, password="hunter2").test_client()
    for _ in range(5):
        client.post("/api/login", json={"password": "x"})
    resp = client.post("/api/login", json={"password": "hunter2"})
    assert resp.status_code == 429


def test_pages_render(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="curve-chart"' in html and "logout-btn" not in html
    assert client.get("/static/i18n/tr.json").status_code == 200
