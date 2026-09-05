"""Tests for nginx_manager — status, config, sites, server blocks, backup.

All file operations run against a temp nginx layout and the nginx binary
is faked via _run, so the suite works on CI without nginx installed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nginx_manager as mgr  # noqa: E402

CONF = """user www-data;
worker_processes auto;
events {{ worker_connections 768; }}
http {{ include /etc/nginx/conf.d/*.conf; }}
"""

SITE = """server {
    listen 80;
    server_name example.test;
    location / { proxy_pass http://127.0.0.1:3000; }
}
"""


@pytest.fixture()
def conf(tmp_path, monkeypatch):
    base = tmp_path / "etc-nginx"
    (base / "sites-available").mkdir(parents=True)
    (base / "sites-enabled").mkdir(parents=True)
    (base / "conf.d").mkdir()
    (base / "nginx.conf").write_text(CONF.format())
    (base / "sites-available" / "example.test").write_text(SITE)
    (base / "conf.d" / "compression.conf").write_text("gzip on;")
    pidfile = tmp_path / "nginx.pid"
    pidfile.write_text("1234\n")
    monkeypatch.setattr(mgr, "NGINX_CONF_DIR", str(base))
    monkeypatch.setattr(mgr, "NGINX_CONF", str(base / "nginx.conf"))
    monkeypatch.setattr(mgr, "NGINX_PIDFILE", str(pidfile))
    monkeypatch.setattr(mgr, "_IS_ROOT", True)
    return base


def fake_run(status_canned=None):
    def _run(cmd, timeout=10):
        if "nginx -v" in cmd:
            return "", "nginx version: nginx/1.24.0 (Ubuntu)", 0
        if "nginx -T" in cmd:
            return "configuration file: /etc/nginx/nginx.conf", "", 0
        if "kill -0" in cmd:
            return "", "", 0
        if "nginx -t" in cmd:
            return status_canned or ("syntax is ok", "", 0)
        return "", "", 0
    return _run


def test_status(conf, monkeypatch):
    monkeypatch.setattr(mgr, "_run", fake_run())
    s = mgr.status()
    assert s["running"] is True
    assert s["version"] == "1.24.0 (Ubuntu)"
    assert s["master_pid"] == "1234"
    assert s["workers"] == 0
    assert s["conf_dir"] == str(conf)


def test_status_not_running(conf, monkeypatch):
    def _run(cmd, timeout=10):
        if "kill -0" in cmd:
            return "", "", 1
        if "nginx -v" in cmd:
            return "", "nginx version: nginx/1.24.0", 0
        return "", "", 0
    monkeypatch.setattr(mgr, "_run", _run)
    assert mgr.status()["running"] is False


def test_check_config_valid(conf, monkeypatch):
    monkeypatch.setattr(mgr, "_run", fake_run(("syntax is ok\n", "", 0)))
    assert mgr.check_config()["valid"] is True


def test_check_config_invalid(conf, monkeypatch):
    monkeypatch.setattr(mgr, "_run", fake_run(("", "syntax error", 1)))
    assert mgr.check_config()["valid"] is False


def test_config_files_lists_entries_and_dirs(conf):
    files = mgr.config_files()
    assert "nginx.conf" in files
    assert "conf.d/compression.conf" in files


def test_read_write_config_roundtrip(conf):
    mgr.write_config("conf.d/extra.conf", "client_max_body_size 25m;")
    out = mgr.read_config("conf.d/extra.conf")
    assert out["content"] == "client_max_body_size 25m;"


def test_read_config_rejects_traversal(conf):
    with pytest.raises(RuntimeError):
        mgr.read_config("../nginx.conf")


def test_write_config_rejects_traversal(conf):
    with pytest.raises(RuntimeError):
        mgr.write_config("../../etc/passwd", "x")


def test_sites_list(conf):
    sites = mgr.sites()
    assert len(sites) == 1
    assert sites[0]["name"] == "example.test"
    assert sites[0]["enabled"] is False


def test_toggle_site_enable_disable(conf):
    assert mgr.toggle_site("example.test")["enabled"] is True
    assert (conf / "sites-enabled" / "example.test").exists()
    assert mgr.toggle_site("example.test")["enabled"] is False


def test_site_crud(conf):
    mgr.write_site("other.test", SITE)
    assert mgr.read_site("other.test")["name"] == "other.test"
    mgr.delete_site("other.test")
    with pytest.raises(RuntimeError):
        mgr.read_site("other.test")


def test_site_name_safety(conf):
    with pytest.raises(RuntimeError):
        mgr.write_site("../evil", SITE)


def test_create_site_generates_server_block(conf):
    r = mgr.create_site("api", "api.example.com", "http://127.0.0.1:8080")
    content = mgr.read_site("api")["content"]
    assert "server_name api.example.com;" in content
    assert "proxy_pass http://127.0.0.1:8080;" in content
    assert r["enabled"] is False


def test_create_site_websocket(conf):
    mgr.create_site("ws", "ws.example.com", "http://127.0.0.1:3000", websocket=True)
    content = mgr.read_site("ws")["content"]
    assert "proxy_set_header Upgrade $http_upgrade;" in content


def test_create_site_duplicate(conf):
    mgr.create_site("api", "api.example.com", "http://x")
    with pytest.raises(RuntimeError):
        mgr.create_site("api", "api.example.com", "http://y")
    r = mgr.create_site("api", "api.example.com", "http://y", overwrite=True)
    assert r["name"] == "api"


def test_make_server_block_sanity():
    body = mgr.make_server_block("Example.COM", "http://10.0.0.1:9000", port=443)
    assert "server_name example.com;" in body
    assert "listen 443" in body


def test_backup_restore_roundtrip(conf):
    blob = mgr.backup_data()
    assert blob.startswith(b"\x1f\x8b")
    (conf / "nginx.conf").write_text("user nobody;\n")
    (conf / "sites-available" / "post-backup.test").write_text(SITE)
    mgr.restore_backup(blob, apply_check=False)
    assert (conf / "nginx.conf").read_text() == CONF.format()
    assert (conf / "sites-available" / "post-backup.test").exists()


def test_restore_traversal_ignored(conf):
    blob = mgr.backup_data()
    from tarfile import TarInfo
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = TarInfo("nginx-config/../../escape.txt")
        info.size = 4
        tf.addfile(info, io.BytesIO(b"evil"))
    outside = tmp = conf.parent / "escape.txt"
    if outside.exists():
        outside.unlink()
    mgr.restore_backup(buf.getvalue(), apply_check=False)
    assert not outside.exists()