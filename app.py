"""API-only nginx management backend.

Serves a JSON API for nginx status, config editing, server-block wizard,
sites, validation (nginx -t), reload/restart, logs, and backup/restore.
No templates, no static files — the webui facade talks to this over HTTP.
"""
import base64
import binascii
import json
import os
import secrets
import time

from flask import Flask, Response, jsonify, request

import metrics
import nginx_manager as mgr

WEBUI_PASSWORD = os.environ.get("WEBUI_PASSWORD", "").strip()
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()
PORT = int(os.environ.get("NGINX_WEBUI_PORT", "8400"))

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY or secrets.token_hex(32)

metrics.set_ready(True)
OPEN_PATHS = {"/healthz", "/readyz"}
_login_failures = {}
_login_locked_until = {}
MAX_ATTEMPTS = int(os.environ.get("MAX_LOGIN_ATTEMPTS", "5"))
LOCK_TIME = int(os.environ.get("LOCK_TIME", "900"))


@app.before_request
def protect_endpoints():
    metrics.record_request_started()
    if not WEBUI_PASSWORD:
        return None
    if request.path in OPEN_PATHS:
        return None
    if request.endpoint in ("api_login", "api_session"):
        return None
    if not is_authenticated():
        return _err("Unauthorized", 401)
    return None


@app.after_request
def tally(resp):
    metrics.record_request_finished(resp.status_code)
    return resp


# ── Auth (shared: same WEBUI_PASSWORD as webui + other backends) ───────────

def _client():
    return request.remote_addr or "local"


def _login_locked(cid):
    until = _login_locked_until.get(cid, 0)
    if until > time.time():
        return int(until - time.time())
    return 0


def _session_cookie_ok():
    tok = request.cookies.get("session")
    return bool(tok) and tok == app.config.get("SESSION_TOKEN")


def session_auth():
    """Return True when a valid session cookie is present."""
    return _session_cookie_ok()


def is_authenticated():
    return session_auth()


@app.route("/api/session", methods=["GET"])
def api_session():
    return _ok({"auth": is_authenticated()})


@app.route("/api/login", methods=["POST"])
def api_login():
    cid = _client()
    remaining = _login_locked(cid)
    if remaining > 0:
        return _err(f"Too many attempts, retry in {remaining}s", 429)
    body = request.get_json(silent=True) or {}
    if secrets.compare_digest(request.headers.get("X-Realm-Password", body.get("password", "")), WEBUI_PASSWORD):
        _login_failures.pop(cid, None)
        token = app.config.get("SESSION_TOKEN") or secrets.token_hex(32)
        app.config["SESSION_TOKEN"] = token
        resp = _ok({"auth": True, "remember": bool(body.get("remember"))})
        resp.set_cookie("session", token, max_age=1800, samesite="Lax", httponly=True)
        return resp
    _login_failures[cid] = _login_failures.get(cid, 0) + 1
    if _login_failures[cid] >= MAX_ATTEMPTS:
        _login_locked_until[cid] = time.time() + LOCK_TIME
        _login_failures.pop(cid, None)
        return _err(f"Too many failed attempts, retry in {LOCK_TIME}s", 429)
    return _err("Unauthorized", 401)


@app.route("/api/logout", methods=["POST"])
def api_logout():
    resp = _ok({"auth": False})
    resp.delete_cookie("session")
    return resp


# ── Probes ──────────────────────────────────────────────────────────────────

@app.route("/healthz")
def healthz():
    metrics.set_healthy(True)
    return Response("OK", content_type="text/plain")


@app.route("/readyz")
def readyz():
    metrics.set_ready(True)
    return Response("OK", content_type="text/plain")


@app.route("/metrics")
def api_metrics():
    return Response(metrics.metrics_text(), content_type="text/plain; version=0.0.4; charset=utf-8")


@app.route("/api/metrics/portal", methods=["GET"])
def api_metrics_portal():
    return _ok(metrics.summarize(metrics.metrics_text()))


# ── nginx API ───────────────────────────────────────────────────────────────

@app.route("/api/status", methods=["GET"])
def api_status():
    return _ok(mgr.status())


@app.route("/api/check", methods=["GET", "POST"])
def api_check():
    try:
        result = mgr.check_config()
    except RuntimeError as e:
        return _err(str(e), 500)
    return _ok(result)


@app.route("/api/system", methods=["GET"])
def api_system():
    return _ok({"service": "nginx-webui"})


@app.route("/api/control/reload", methods=["POST"])
def api_reload():
    try:
        return _ok(mgr.reload())
    except RuntimeError as e:
        return _err(str(e), 500)


@app.route("/api/control/restart", methods=["POST"])
def api_restart():
    try:
        return _ok(mgr.restart())
    except RuntimeError as e:
        return _err(str(e), 500)


@app.route("/api/config/files", methods=["GET"])
def api_config_files():
    return _ok(mgr.config_files())


@app.route("/api/config/file/<path:name>", methods=["GET", "PUT"])
def api_config_file(name=""):
    if request.method == "GET":
        try:
            return _ok(mgr.read_config(name))
        except RuntimeError as e:
            return _err(str(e), 404)
    body = request.get_json(silent=True) or {}
    try:
        return _ok(mgr.write_config(name, body.get("content", "")))
    except RuntimeError as e:
        return _err(str(e), 400)


@app.route("/api/sites", methods=["GET"])
def api_sites():
    return _ok(mgr.sites())


@app.route("/api/site/<name>", methods=["GET", "PUT", "DELETE"])
def api_site(name=""):
    if request.method == "GET":
        try:
            return _ok(mgr.read_site(name))
        except RuntimeError as e:
            return _err(str(e), 404)
    if request.method == "DELETE":
        try:
            return _ok(mgr.delete_site(name))
        except RuntimeError as e:
            return _err(str(e), 400)
    body = request.get_json(silent=True) or {}
    try:
        if "content" in body:
            result = mgr.update_site(name, content=body.get("content", ""))
        else:
            result = mgr.update_site(
                name,
                domain=body.get("domain"),
                upstream=body.get("upstream"),
                port=body.get("port", 80),
                websocket=bool(body.get("websocket")),
            )
        return _ok(result)
    except RuntimeError as e:
        return _err(str(e), 400)


@app.route("/api/site/<name>/toggle", methods=["POST"])
def api_site_toggle(name=""):
    try:
        return _ok(mgr.toggle_site(name))
    except RuntimeError as e:
        return _err(str(e), 400)


@app.route("/api/site", methods=["POST"])
def api_create_site():
    body = request.get_json(silent=True) or {}
    try:
        result = mgr.create_site(
            body.get("name") or body.get("domain", "").split(".")[0],
            body.get("domain", ""),
            body.get("upstream", ""),
            port=body.get("port", 80),
            websocket=bool(body.get("websocket")),
            overwrite=bool(body.get("overwrite")),
        )
        return _ok(result)
    except RuntimeError as e:
        return _err(str(e), 400)


@app.route("/api/logs", methods=["GET"])
def api_logs():
    lines = int(request.args.get("lines", "100"))
    return _ok(mgr.get_logs(lines=lines, query=request.args.get("query", "")))


@app.route("/api/backup", methods=["GET"])
def api_backup():
    data = mgr.backup_data()
    return Response(json.dumps({"data": base64.b64encode(data).decode()}), content_type="application/json")


@app.route("/api/restore", methods=["POST"])
def api_restore():
    body = request.get_json(silent=True) or {}
    try:
        data = base64.b64decode(body.get("data", ""))
        return _ok(mgr.restore_backup(data, apply_check=bool(body.get("check", True))))
    except (ValueError, RuntimeError, binascii.Error) as e:
        return _err(str(e), 400)


# ── JSON helpers ────────────────────────────────────────────────────────────

def _ok(data):
    return jsonify({"ok": True, "data": data})


def _err(error, code=400):
    resp = jsonify({"ok": False, "error": error})
    resp.status_code = code
    return resp


if __name__ == "__main__":
    print(f"nginx-webui API on :{PORT} (WEBUI_PASSWORD{' set' if WEBUI_PASSWORD else ' NOT set — auth disabled'})")
    app.run(host="0.0.0.0", port=PORT)