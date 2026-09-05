"""Operate an nginx installation from Python: status, config, sites,
validation (nginx -t), reload, logs, backup/restore, and server-block
generation. API-only backend — no UI served here.
"""
import io
import os
import re
import shlex
import subprocess
import tarfile
import tempfile
from pathlib import Path

NGINX_CONF_DIR = os.environ.get("NGINX_CONF_DIR", "/etc/nginx")
NGINX_PIDFILE = "/run/nginx.pid"
LOG_FILE = os.environ.get("LOG_FILE", "").strip()
TIMEOUT = int(os.environ.get("NGINX_TIMEOUT", "10"))

NGINX_CONF = f"{NGINX_CONF_DIR}/nginx.conf"
_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def _sudo():
    return "" if _IS_ROOT else "sudo "


def _run(cmd, timeout=TIMEOUT):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        raise RuntimeError("Command timed out")


def _read_file(path):
    try:
        return Path(path).read_text()
    except FileNotFoundError:
        return ""
    except PermissionError:
        out, _, _ = _run(f"{_sudo()}cat {path}")
        return out


def _write_file(path, content):
    tmp = f"/tmp/nginx_ui_{os.getpid()}.conf"
    Path(tmp).write_text(content)
    try:
        Path(path).write_text(content)
    except PermissionError:
        _run(f"{_sudo()}cp {tmp} {path}", timeout=15)
        _run(f"{_sudo()}chmod 644 {path}", timeout=15)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


# ── Status ──────────────────────────────────────────────────────────────────

def status():
    version = ""
    out, err, _ = _run("nginx -v")
    version = (err or out).strip().replace("nginx version:", "").replace("nginx/", "", 1).strip()

    pid_path = Path(NGINX_PIDFILE)
    master_pid = ""
    running = False
    if pid_path.exists():
        running = True
        try:
            master_pid = pid_path.read_text().strip()
        except Exception:
            pass
    if pid_path.exists():
        try:
            _run(f"kill -0 {shlex.quote(master_pid)}")
        except Exception:
            pass
        if _run(f"kill -0 {shlex.quote(master_pid)}")[2] != 0:
            running = False

    workers = 0
    try:
        workers = int(_run("pgrep -fc 'nginx: worker'")[0] or 0)
    except (ValueError, RuntimeError):
        workers = 0

    config = ""
    out, _, _ = _run("nginx -T 2>/dev/null")
    m = re.search(r"configuration file: (\S+)", out)
    if m:
        config = m.group(1)

    return {
        "version": version or "(unknown)",
        "running": running,
        "master_pid": master_pid,
        "workers": workers,
        "config_file": config or NGINX_CONF,
        "conf_dir": NGINX_CONF_DIR,
    }


def check_config():
    out, err, rc = _run(f"{_sudo()}nginx -t -c {shlex.quote(NGINX_CONF)}")
    return {"valid": rc == 0, "output": out, "error": err}


def reload():
    out, err, rc = _run(f"{_sudo()}nginx -s reload")
    if rc != 0:
        raise RuntimeError(err or out or "nginx reload failed")
    return {"output": out, "message": "nginx reloaded"}


def restart():
    out, err, rc = _run(f"{_sudo()}systemctl restart nginx")
    if rc != 0:
        raise RuntimeError(err or out or "nginx restart failed")
    return {"output": out, "message": "nginx restarted"}


# ── Config files ────────────────────────────────────────────────────────────

CONF_ROOTS = ("nginx.conf", "conf.d", "snippets", "modules-available", "modules-enabled")


def config_files():
    """List editable files as relative paths under NGINX_CONF_DIR."""
    files = []
    for root in CONF_ROOTS:
        base = Path(NGINX_CONF_DIR) / root
        if base.is_file():
            files.append(root)
        elif base.is_dir():
            for p in sorted(base.rglob("*.conf")):
                files.append(str(p.relative_to(NGINX_CONF_DIR)))
    return files


def _resolve(relative):
    base = Path(NGINX_CONF_DIR).resolve()
    target = (base / relative).resolve()
    if base != target and base not in target.parents:
        raise RuntimeError("Path escapes the nginx config directory")
    return target


def read_config(relative):
    path = _resolve(relative)
    if not path.exists():
        raise RuntimeError(f"{relative} does not exist")
    return {"name": relative, "path": str(path), "content": _read_file(path)}


def write_config(relative, content):
    path = _resolve(relative)
    _write_file(path, content)
    return {"name": relative, "path": str(path)}


# ── Sites (server blocks) ───────────────────────────────────────────────────

def _sites_available_dir():
    d = Path(NGINX_CONF_DIR) / "sites-available"
    if not d.exists():
        d.mkdir(parents=True)
    return d


def _sites_enabled_dir():
    d = Path(NGINX_CONF_DIR) / "sites-enabled"
    if not d.exists():
        d.mkdir(parents=True)
    return d


def _safe_site_name(name):
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise RuntimeError("Invalid site name")
    return name


def sites():
    available = _sites_available_dir()
    enabled = _sites_enabled_dir()
    out = []
    for p in sorted(available.glob("*")):
        if not p.is_file():
            continue
        link = enabled / p.name
        out.append({
            "name": p.name,
            "path": str(p),
            "enabled": link.exists(),
            "enabled_path": str(link),
        })
    return out


def read_site(name):
    name = _safe_site_name(name)
    path = _sites_available_dir() / name
    if not path.exists():
        raise RuntimeError(f"Site {name} not found")
    enabled = (_sites_enabled_dir() / name).exists()
    return {"name": name, "content": _read_file(path), "enabled": enabled, "path": str(path)}


def write_site(name, content):
    name = _safe_site_name(name)
    path = _sites_available_dir() / name
    _write_file(path, content)
    return {"name": name, "path": str(path)}


def delete_site(name):
    name = _safe_site_name(name)
    link = _sites_enabled_dir() / name
    path = _sites_available_dir() / name
    if link.exists():
        _unlink(link)
    if path.exists():
        _unlink(path)
    return {"name": name}


def toggle_site(name, enable=None):
    name = _safe_site_name(name)
    available = _sites_available_dir() / name
    enabled = _sites_enabled_dir() / name
    if not available.exists():
        raise RuntimeError(f"Site {name} not found in sites-available")
    want = (not enabled.exists()) if enable is None else bool(enable)
    if want and not enabled.exists():
        _link(available, enabled)
        return {"name": name, "enabled": True}
    if not want and enabled.exists():
        _unlink(enabled)
        return {"name": name, "enabled": False}
    return {"name": name, "enabled": enabled.exists()}


def _link(src, dst):
    try:
        os.symlink(src, dst)
    except PermissionError:
        _run(f"{_sudo()}ln -s {shlex.quote(str(src))} {shlex.quote(str(dst))}")
    except FileExistsError:
        pass


def _unlink(path):
    try:
        os.unlink(path)
    except PermissionError:
        _run(f"{_sudo()}rm -f {shlex.quote(str(path))}")


# ── Server-block generation (reverse proxy wizard) ──────────────────────────

_PROXY_TEMPLATE = """# Auto-generated by nginx-webui — do not edit by hand.
server {{
    listen {port};
    listen [::]:{port};

    server_name {domain};

    location / {{
        proxy_pass {upstream};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
{upgrade}
    }}
}}
"""


def make_server_block(domain, upstream, port=80, websocket=False):
    domain = (domain or "").strip().lower()
    if not domain or re.search(r"\s", domain):
        raise RuntimeError("Invalid domain")
    upgrade = "        proxy_set_header Upgrade $http_upgrade;\n        proxy_set_header Connection 'upgrade';" if websocket else ""
    try:
        body = _PROXY_TEMPLATE.format(
            domain=domain, upstream=upstream or "http://127.0.0.1:3000",
            port=int(port) or 80, upgrade=upgrade,
        )
    except (ValueError, KeyError) as e:
        raise RuntimeError(f"Invalid server-block parameters: {e}")
    return body


def create_site(name, domain, upstream, port=80, websocket=False, overwrite=False):
    name = _safe_site_name(name or domain)
    path = _sites_available_dir() / name
    if path.exists() and not overwrite:
        raise RuntimeError(f"Site {name} already exists")
    body = make_server_block(domain, upstream, port=port, websocket=websocket)
    _write_file(path, body)
    return {"name": name, "path": str(path), "enabled": False}


# ── Logs ────────────────────────────────────────────────────────────────────

def get_logs(lines=100, query=""):
    out = ""
    candidates = []
    if LOG_FILE:
        candidates = [("LOG_FILE", f"tail -n {lines} {LOG_FILE} 2>/dev/null")]
    else:
        candidates = [
            ("error.log", f"tail -n {lines} /var/log/nginx/error.log 2>/dev/null"),
            ("access.log", f"tail -n {lines} /var/log/nginx/access.log 2>/dev/null"),
        ]
    for name, cmd in candidates:
        o, _, _ = _run(cmd)
        if o:
            out = o
            break
    if query:
        out = "\n".join(ln for ln in out.splitlines() if query.lower() in ln.lower())
    if not out:
        out = "No nginx logs found. If running in a container, set LOG_FILE to a mounted log path."
    return out


# ── Backup / Restore ────────────────────────────────────────────────────────

def backup_data():
    buf = io.BytesIO()
    base = Path(NGINX_CONF_DIR).resolve()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix in (".so", ".log") or p.stat().st_size > 2 * 1024 * 1024:
                continue
            rel = p.relative_to(base)
            data = p.read_bytes()
            info = tarfile.TarInfo(name=f"nginx-config/{rel}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def restore_backup(data, apply_check=True):
    """Apply a backup tarball; validate with nginx -t and roll back on failure."""
    base = Path(NGINX_CONF_DIR).resolve()
    originals = {}
    written = []

    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except tarfile.TarError as e:
        raise RuntimeError(f"Invalid backup archive: {e}")
    with tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            rel = member.name
            if rel.startswith("nginx-config/"):
                rel = rel[len("nginx-config/"):]
            rel = rel.lstrip("/")
            if not rel or ".." in rel:
                continue
            target = (base / rel).resolve()
            if base != target and base not in target.parents:
                continue
            content = tf.extractfile(member).read().decode("utf-8", errors="replace")
            if target.exists() and rel not in originals:
                originals[rel] = Path(target).read_text()
            _write_file(target, content)
            written.append(rel)

    if apply_check:
        check = check_config()
        if not check["valid"]:
            for rel, content in originals.items():
                try:
                    _write_file((base / rel).resolve(), content)
                except Exception:
                    pass
            raise RuntimeError(f"Restore failed nginx -t; previous config restored. {check['error'] or check['output']}")

    return {"files": len(written), "check": "skipped" if not apply_check else "ok"}


_applied = []


def _write_restored(target, content):
    _write_file(target, content)
    _applied.append(str(target))


def _applied_count():
    n = len(_applied)
    _applied.clear()
    return n