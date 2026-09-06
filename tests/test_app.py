"""Tests for the nginx-webui Flask API: auth, probes, metrics, routes."""
import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as a  # noqa: E402
import nginx_manager as mgr  # noqa: E402

PASSWORD = "pw-123"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    a.WEBUI_PASSWORD = PASSWORD
    a.app.config.pop("SESSION_TOKEN", None)
    a._login_failures.clear()
    a._login_locked_until.clear()
    a.app.config["TESTING"] = True
    (tmp_path / "sites-available").mkdir(parents=True)
    (tmp_path / "sites-enabled").mkdir()
    (tmp_path / "nginx.conf").write_text("user www-data;\n")
    monkeypatch.setattr(mgr, "NGINX_CONF_DIR", str(tmp_path))
    monkeypatch.setattr(mgr, "NGINX_CONF", str(tmp_path / "nginx.conf"))
    monkeypatch.setattr(mgr, "_IS_ROOT", True)
    return a.app.test_client()


def _login(client, password=PASSWORD):
    return client.post("/api/login", json={"password": password})


# ── Auth ───────────────────────────────────────────────────────────────

def test_api_requires_auth(client):
    assert client.get("/api/status").status_code == 401
    assert client.get("/metrics").status_code == 401


def test_probes_open(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_login_and_session(client):
    assert _login(client).status_code == 200
    assert client.get("/api/session").get_json()["data"]["auth"] is True


def test_lockout(client):
    for _ in range(5):
        _login(client, "bad")
    assert _login(client, PASSWORD).status_code == 429


def test_metrics_after_login(client):
    _login(client)
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "nginx-webui" in r.get_data(as_text=True)
    assert "process_resident_memory_bytes" in r.get_data(as_text=True)


# ── nginx routes ────────────────────────────────────────────────────────

def test_status_returns_dict(client, monkeypatch):
    monkeypatch.setattr(mgr, "status", lambda: {"running": True, "version": "1.24"})
    _login(client)
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.get_json()["data"]["running"] is True


def test_check_ok(client, monkeypatch):
    monkeypatch.setattr(mgr, "check_config", lambda: {"valid": True, "output": "ok"})
    _login(client)
    assert client.get("/api/check").get_json()["data"]["valid"] is True


def test_reload_error_maps_to_500(client, monkeypatch):
    def boom():
        raise RuntimeError("no master running")
    monkeypatch.setattr(mgr, "reload", boom)
    _login(client)
    r = client.post("/api/control/reload")
    assert r.status_code == 500
    assert "no master" in r.get_json()["error"]


def test_site_read_write_flow(client):
    _login(client)
    r = client.put("/api/site/new.test", json={"content": "server {}"})
    assert r.status_code == 200
    assert client.get("/api/site/new.test").get_json()["data"]["content"] == "server {}"
    assert client.get("/api/sites").get_json()["data"][0]["name"] == "new.test"


def test_site_toggle(client):
    _login(client)
    client.put("/api/site/a.test", json={"content": "server {}"})
    assert client.post("/api/site/a.test/toggle").get_json()["data"]["enabled"] is True


def test_unknown_site_404(client):
    _login(client)
    assert client.get("/api/site/missing.test").status_code == 404


def test_create_site_route(client):
    _login(client)
    r = client.post("/api/site", json={"name": "api", "domain": "api.x.com", "upstream": "http://127.0.0.1:1"})
    assert r.status_code == 200
    assert "server_name api.x.com;" in client.get("/api/site/api").get_json()["data"]["content"]


def test_create_site_route_tls(client):
    _login(client)
    r = client.post("/api/site", json={
        "name": "secure", "domain": "secure.x.com", "upstream": "http://127.0.0.1:1",
        "port": 443, "tls": True, "cert": "/etc/ssl/f.pem", "key": "/etc/ssl/k.pem",
        "redirect_http": True, "client_max_body_size": "25m",
    })
    assert r.status_code == 200
    d = client.get("/api/site/secure").get_json()["data"]
    assert "listen 443 ssl;" in d["content"]
    assert d["fields"]["redirect_http"] is True
    assert d["fields"]["client_max_body_size"] == "25m"


def test_site_update_from_fields(client):
    _login(client)
    client.post("/api/site", json={"name": "api", "domain": "api.x.com", "upstream": "http://127.0.0.1:1"})
    r = client.put("/api/site/api", json={"domain": "api2.x.com", "upstream": "http://10.0.0.9:9000", "port": 443})
    assert r.status_code == 200
    d = client.get("/api/site/api").get_json()["data"]
    assert "server_name api2.x.com;" in d["content"]
    assert d["fields"]["listen"] == 443
    assert d["fields"]["proxy_pass"] == "http://10.0.0.9:9000"


def test_site_update_requires_valid_state(client):
    _login(client)
    r = client.put("/api/site/ghost", json={"domain": "x.test", "upstream": "http://x"})
    assert r.status_code == 400


def test_site_rename_via_put(client):
    _login(client)
    client.post("/api/site", json={"name": "api", "domain": "api.x.com", "upstream": "http://127.0.0.1:1"})
    r = client.put("/api/site/api", json={"domain": "api.x.com", "upstream": "http://10.0.0.9:9000", "new_name": "api2"})
    assert r.status_code == 200
    assert r.get_json()["data"]["name"] == "api2"
    assert client.get("/api/site/api").status_code == 404
    assert "proxy_pass http://10.0.0.9:9000;" in client.get("/api/site/api2").get_json()["data"]["content"]


def test_backup_and_restore_roundtrip(client):
    _login(client)
    client.put("/api/site/a.test", json={"content": "server {}"})
    b = client.get("/api/backup").get_json()["data"]
    assert base64.b64decode(b).startswith(b"\x1f\x8b")
    r = client.post("/api/restore", json={"data": b, "check": False})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_restore_rejects_garbage(client):
    _login(client)
    r = client.post("/api/restore", json={"data": "not-base64!!", "check": False})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False