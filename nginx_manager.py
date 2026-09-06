"""Operate an nginx installation from Python: status, config, sites,
validation (nginx -t), reload, logs, backup/restore, and server-block
generation. API-only backend — no UI served here.
"""
import io
import json
import os
import re
import shlex
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

NGINX_CONF_DIR = os.environ.get("NGINX_CONF_DIR", "/etc/nginx")
NGINX_PIDFILE = "/run/nginx.pid"
LOG_FILE = os.environ.get("LOG_FILE", "").strip()
TIMEOUT = int(os.environ.get("NGINX_TIMEOUT", "10"))
# Set this to an HTTP endpoint nginx itself serves (e.g. stub_status at
# /nginx_status) to detect "running" over HTTP — required when nginx-webui
# runs in a container and cannot see the host's /proc or /run/nginx.pid.
NGINX_STATUS_URL = os.environ.get("NGINX_STATUS_URL", "").strip()
# Set this to the host-side control agent (repo: host_agent.py) so status,
# `nginx -t`, reload and restart act on the HOST nginx — required when
# nginx-webui runs in a container (it cannot signal the host master itself).
NGINX_CTL_URL = os.environ.get("NGINX_CTL_URL", "").strip()

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

def _process_alive(pid):
    """Liveness without signalling: /proc entry exists for any owner; fall
    back to signal 0 for cooperative processes."""
    if not pid:
        return False
    if os.path.exists(f"/proc/{pid}"):
        return True
    _, _, rc = _run(f"kill -0 {shlex.quote(pid)}")
    return rc == 0


def _host_probe(action, method="POST", timeout=20):
    """Talk to the host-side control agent (NGINX_CTL_URL)."""
    url = f"{NGINX_CTL_URL.rstrip('/')}/{action}"
    if method == "GET":
        req = urllib.request.Request(url, method="GET")
    else:
        req = urllib.request.Request(url, method="POST", data=b"{}")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except OSError as e:
        raise RuntimeError(f"nginx control agent unreachable at {NGINX_CTL_URL}: {e}")


def status():
    if NGINX_CTL_URL:
        p = _host_probe("status", method="GET")
        if not isinstance(p, dict):
            raise RuntimeError("nginx control agent returned an invalid status")
        return {
            "version": p.get("version") or "(unknown)",
            "running": bool(p.get("running")),
            "master_pid": p.get("master_pid", ""),
            "workers": p.get("workers", 0),
            "config_file": p.get("config_file") or NGINX_CONF,
            "conf_dir": p.get("conf_dir") or NGINX_CONF_DIR,
            "status_source": "agent",
        }
    if NGINX_STATUS_URL:
        return _status_http()
    return _status_local()


def _status_local():
    version = ""
    out, err, _ = _run("nginx -v")
    version = (err or out).strip().replace("nginx version:", "").replace("nginx/", "", 1).strip()

    pid_path = Path(NGINX_PIDFILE)
    master_pid = ""
    running = False
    if pid_path.exists():
        try:
            master_pid = pid_path.read_text().strip()
        except Exception:
            pass
        running = pid_path.exists() and _process_alive(master_pid)

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


def _status_http():
    """Status via an HTTP probe of nginx itself (works from inside a container).

    Point NGINX_STATUS_URL at a stub_status endpoint, e.g.
    NGINX_STATUS_URL=http://host.docker.internal:8080/nginx_status
    """
    info = {
        "version": "(unknown)", "running": False, "master_pid": "", "workers": 0,
        "config_file": NGINX_CONF, "conf_dir": NGINX_CONF_DIR, "status_source": "http",
    }
    try:
        req = urllib.request.Request(NGINX_STATUS_URL, headers={"User-Agent": "nginx-webui/1"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = resp.read(4096).decode("utf-8", errors="replace")
            server = resp.headers.get("Server", "")
    except OSError as e:
        info["error"] = f"status probe failed: {e}"
        return info
    m = re.search(r"nginx/([\d.]+)", server or "")
    if m:
        info["version"] = m.group(1)
    m = re.search(r"Active connections:\s*(\d+)", body)
    if m:
        info["active_connections"] = int(m.group(1))
    info["running"] = True
    return info


def check_config():
    if NGINX_CTL_URL:
        p = _host_probe("check")
        return {"valid": bool(p.get("ok") or p.get("valid")), "output": p.get("output", ""), "error": p.get("error", "")}
    out, err, rc = _run(f"{_sudo()}nginx -t -c {shlex.quote(NGINX_CONF)}")
    return {"valid": rc == 0, "output": out, "error": err}


def reload():
    if NGINX_CTL_URL:
        p = _host_probe("reload")
        if not p.get("ok"):
            raise RuntimeError(p.get("error") or "nginx reload failed on host")
        return {"output": p.get("output", ""), "message": "nginx reloaded via host agent"}
    out, err, rc = _run(f"{_sudo()}nginx -s reload")
    if rc != 0:
        raise RuntimeError(err or out or "nginx reload failed")
    return {"output": out, "message": "nginx reloaded"}


def restart():
    if NGINX_CTL_URL:
        p = _host_probe("restart")
        if not p.get("ok"):
            raise RuntimeError(p.get("error") or "nginx restart failed on host")
        return {"output": p.get("output", ""), "message": "nginx restarted via host agent"}
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


def _site_listen_ports(content):
    """Collect (port, ssl) pairs from all listen directives in a config."""
    out = []
    for m in re.finditer(r"listen\s+([^;]+);", content):
        directive = m.group(1).strip()
        ssl = bool(re.search(r"\bssl\b", directive))
        bare = directive.split()[0].replace("[::]:", "").replace("*:", "").strip()
        if bare.isdigit():
            out.append((int(bare), ssl))
    return out


def _site_fields(content):
    """Best-effort parse of the fields the wizard manages."""
    def _one(pat):
        m = re.search(pat, content)
        return m.group(1).strip() if m else ""
    names = _one(r"server_name\s+([^;]+);")
    ssl = bool(re.search(r"listen\s+\d+\s*ssl\b", content)) or \
        bool(re.search(r"^\s*ssl_certificate\b", content, re.M))
    ports = _site_listen_ports(content)
    ssl_ports = [p for p, is_ssl in ports if is_ssl]
    plain_ports = [p for p, is_ssl in ports if not is_ssl]
    listen = (ssl_ports or plain_ports or [None])[0]
    if plain_ports:
        http_listen = plain_ports[0]
    elif ssl_ports:
        http_listen = None
    else:
        http_listen = listen
    return {
        "server_name": names.split()[0] if names else "",
        "listen": listen,
        "http_listen": http_listen,
        "proxy_pass": _one(r"proxy_pass\s+([^\s;]+);"),
        "websocket": "upgrade" in content,
        "ssl": ssl,
        "ssl_certificate": _one(r"ssl_certificate\s+([^\s;]+);"),
        "ssl_certificate_key": _one(r"ssl_certificate_key\s+([^\s;]+);"),
        "redirect_http": bool(re.search(r"return\s+301\s+https://", content)),
        "client_max_body_size": _one(r"client_max_body_size\s+([^;]+);"),
        "proxy_read_timeout": _one(r"proxy_read_timeout\s+([^;]+);"),
    }


def read_site(name):
    name = _safe_site_name(name)
    path = _sites_available_dir() / name
    if not path.exists():
        raise RuntimeError(f"Site {name} not found")
    enabled = (_sites_enabled_dir() / name).exists()
    content = _read_file(path)
    return {"name": name, "content": content, "enabled": enabled, "path": str(path),
            "fields": _site_fields(content)}


def write_site(name, content):
    name = _safe_site_name(name)
    path = _sites_available_dir() / name
    _write_file(path, content)
    return {"name": name, "path": str(path)}


def update_site(name, content=None, domain=None, upstream=None, port=80, http_port=80, websocket=False,
                new_name=None, tls=None, cert=None, key=None, redirect_http=None,
                client_max_body_size=None, proxy_read_timeout=None):
    """Update a site from raw content or wizard fields; optionally rename the file."""
    new_name = _safe_site_name(new_name) if new_name not in (None, "") else None
    if new_name and new_name != name:
        if (_sites_available_dir() / new_name).exists():
            raise RuntimeError(f"Site {new_name} already exists")
    if content is not None:
        result = write_site(name, content)
    else:
        path = _sites_available_dir() / name
        if not path.exists():
            raise RuntimeError(f"Site {name} not found")
        cur = read_site(name)
        fields = cur["fields"]
        new_domain = (domain if domain not in (None, "") else fields.get("server_name")) or ""
        new_upstream = (upstream if upstream not in (None, "") else fields.get("proxy_pass")) or "http://127.0.0.1:3000"
        new_port = int(port) if port not in (None, "") else (fields.get("listen") or 80)
        new_http_port = int(http_port) if http_port not in (None, "") else (fields.get("http_listen") or 80)
        if not new_domain:
            raise RuntimeError("Provide a domain to edit this site from the form")
        new_tls = bool(tls) if tls is not None else bool(fields.get("ssl"))
        new_cert = cert if cert is not None else (fields.get("ssl_certificate") if new_tls else None)
        new_key = key if key is not None else (fields.get("ssl_certificate_key") if new_tls else None)
        new_redirect = bool(redirect_http) if redirect_http is not None else bool(fields.get("redirect_http"))
        new_cmb = client_max_body_size if client_max_body_size is not None \
            else (fields.get("client_max_body_size") or None)
        new_timeout = proxy_read_timeout if proxy_read_timeout is not None \
            else (fields.get("proxy_read_timeout") or None)
        body = make_server_block(
            new_domain, new_upstream, port=new_port, http_port=new_http_port, websocket=bool(websocket),
            tls=new_tls, cert=new_cert, key=new_key, redirect_http=new_redirect,
            client_max_body_size=new_cmb, proxy_read_timeout=new_timeout,
        )
        _write_file(path, body)
        result = {"name": name, "path": str(path)}
    if new_name and new_name != name:
        _rename_site(name, new_name)
        result["name"] = new_name
        result["path"] = str(_sites_available_dir() / new_name)
    return result


def _rename_site(old, new):
    available = _sites_available_dir()
    enabled = _sites_enabled_dir()
    old_path, new_path = available / old, available / new
    try:
        old_path.rename(new_path)
    except PermissionError:
        _run(f"{_sudo()}mv {shlex.quote(str(old_path))} {shlex.quote(str(new_path))}")
    old_link, new_link = enabled / old, enabled / new
    if old_link.exists() or old_link.is_symlink():
        _unlink(old_link)
        _link(new_path, new_link)


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
    listen {port}{ssl};
    listen [::]:{port}{ssl};

    server_name {domain};
{body}

    location / {{
        proxy_pass {upstream};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
{timeout}{upgrade}
    }}
}}
"""

_REDIRECT_TEMPLATE = """
server {{
    listen {port};
    listen [::]:{port};

    server_name {domain};

    return 301 https://$host$request_uri;
}}
"""


def make_server_block(domain, upstream, port=80, http_port=80, websocket=False, tls=False,
                      cert=None, key=None, redirect_http=False,
                      client_max_body_size=None, proxy_read_timeout=None):
    domain = (domain or "").strip().lower()
    if not domain or re.search(r"\s", domain):
        raise RuntimeError("Invalid domain")
    tls = bool(tls)
    if tls:
        cert = (cert or "").strip()
        key = (key or "").strip()
        if not cert or not key:
            raise RuntimeError("Certificate and key paths are required for TLS")
    upgrade = ("        proxy_set_header Upgrade $http_upgrade;\n"
               "        proxy_set_header Connection 'upgrade';") if websocket else ""
    timeout = (f"        proxy_read_timeout {proxy_read_timeout};") if proxy_read_timeout else ""
    cmb = (f"    client_max_body_size {client_max_body_size};") if client_max_body_size else ""
    ssl_extra = (f"    ssl_certificate {cert};\n    ssl_certificate_key {key};") if tls else ""
    inline = "\n".join(x for x in (ssl_extra, cmb) if x)
    try:
        body = _PROXY_TEMPLATE.format(
            domain=domain, upstream=upstream or "http://127.0.0.1:3000",
            port=int(port) or 80, ssl=" ssl" if tls else "",
            body=inline, timeout=timeout, upgrade=upgrade,
        )
    except (ValueError, KeyError) as e:
        raise RuntimeError(f"Invalid server-block parameters: {e}")
    if tls and redirect_http:
        body = _REDIRECT_TEMPLATE.format(domain=domain, port=int(http_port) or 80) + "\n" + body
    return body


def create_site(name, domain, upstream, port=80, http_port=80, websocket=False, overwrite=False,
                tls=False, cert=None, key=None, redirect_http=False,
                client_max_body_size=None, proxy_read_timeout=None):
    name = _safe_site_name(name or domain)
    path = _sites_available_dir() / name
    if path.exists() and not overwrite:
        raise RuntimeError(f"Site {name} already exists")
    body = make_server_block(domain, upstream, port=port, http_port=http_port, websocket=websocket,
                             tls=tls, cert=cert, key=key, redirect_http=redirect_http,
                             client_max_body_size=client_max_body_size,
                             proxy_read_timeout=proxy_read_timeout)
    _write_file(path, body)
    return {"name": name, "path": str(path), "enabled": False}


# ── Saved SSL certificates (named cert/key pairs) ────────────────────────────
# Sites reference these in the UI; only the paths matter to the generated
# config, so storing them here just avoids retyping long letsencrypt paths.
# An entry is either a pair of paths ("path" mode) or pasted PEM content
# ("content" mode) that is written to SSL_CERTS_DIR so both the host nginx
# master and this service (container-mounted /etc/nginx) can read it.

SSL_STORE_FILE = os.environ.get("SSL_STORE_FILE", f"{NGINX_CONF_DIR}/webui-ssl-store.json")
SSL_CERTS_DIR = os.environ.get("SSL_CERTS_DIR", f"{NGINX_CONF_DIR}/ssl")

_PEM_CERT_RE = re.compile(r"-----BEGIN [A-Z ]*CERTIFICATE-----")
_PEM_KEY_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")


def _ssl_name_ok(name):
    return bool(name) and name != "." and name != ".." and "/" not in name and "\\" not in name


def _mkdir_p(path):
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
    except PermissionError:
        _run(f"{_sudo()}mkdir -p {shlex.quote(str(path))}")


def _rmtree(path):
    try:
        import shutil
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except PermissionError:
        _run(f"{_sudo()}rm -rf {shlex.quote(str(path))}")


def _load_ssl_store():
    try:
        data = json.loads(Path(SSL_STORE_FILE).read_text())
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if isinstance(v, dict)}
        if isinstance(data, list):
            return {e["name"]: e for e in data
                    if isinstance(e, dict) and e.get("name") and isinstance(e.get("cert"), str)}
    except (OSError, ValueError):
        pass
    return {}


def _save_ssl_store(entries):
    payload = json.dumps(entries, indent=2)
    dirpath = Path(SSL_STORE_FILE).parent
    dirpath.mkdir(parents=True, exist_ok=True)
    if _IS_ROOT:
        tmp = dirpath / f".ssl.{os.getpid()}.tmp"
        tmp.write_text(payload)
        tmp.replace(SSL_STORE_FILE)
    else:
        _write_file(SSL_STORE_FILE, payload)


def _ssl_from_content(name, cert_content, key_content):
    """Write pasted PEM to SSL_CERTS_DIR/<name>/ and return the stored entry."""
    cert_content = (cert_content or "").strip()
    key_content = (key_content or "").strip()
    if not cert_content or not key_content:
        raise RuntimeError("Both certificate and key content are required")
    if not _PEM_CERT_RE.search(cert_content):
        raise RuntimeError("Certificate content does not look like a PEM certificate")
    if not _PEM_KEY_RE.search(key_content):
        raise RuntimeError("Key content does not look like a PEM private key")
    d = Path(SSL_CERTS_DIR) / name
    _mkdir_p(d)
    cert_path = d / "fullchain.pem"
    key_path = d / "privkey.pem"
    _write_file(str(cert_path), cert_content + "\n")
    _write_file(str(key_path), key_content + "\n")
    return {
        "cert": str(cert_path), "key": str(key_path),
        "mode": "content", "cert_content": cert_content, "key_content": key_content,
    }


def ssl_certs():
    return [{"name": name, **entry} for name, entry in sorted(_load_ssl_store().items())]


def get_ssl(name):
    entry = _load_ssl_store().get(name)
    if not entry:
        raise RuntimeError(f"Certificate '{name}' not found")
    return {"name": name, **entry}


def add_ssl(name, cert=None, key=None, cert_content=None, key_content=None):
    name = (name or "").strip()
    if not _ssl_name_ok(name):
        raise RuntimeError("Certificate name may not be empty, '.', '..' or contain '/' or '\\\\'")
    use_content = bool((cert_content or "").strip() or (key_content or "").strip())
    if use_content:
        entry = _ssl_from_content(name, cert_content, key_content)
    else:
        cert = (cert or "").strip()
        key = (key or "").strip()
        if not cert or not key:
            raise RuntimeError("Provide certificate/key paths, or paste the PEM content")
        entry = {"cert": cert, "key": key}
    store = _load_ssl_store()
    if name in store:
        raise RuntimeError(f"Certificate '{name}' already exists")
    store[name] = entry
    _save_ssl_store(store)
    return {"name": name, **entry}


def update_ssl(name, cert=None, key=None, cert_content=None, key_content=None):
    store = _load_ssl_store()
    if name not in store:
        raise RuntimeError(f"Certificate '{name}' not found")
    use_content = bool((cert_content or "").strip() or (key_content or "").strip())
    if use_content:
        entry = _ssl_from_content(name, cert_content, key_content)
    else:
        cert = (cert or "").strip()
        key = (key or "").strip()
        if not cert or not key:
            raise RuntimeError("Certificate and key paths are required")
        entry = {"cert": cert, "key": key}
    store[name] = entry
    _save_ssl_store(store)
    return {"name": name, **entry}


def delete_ssl(name):
    store = _load_ssl_store()
    if name not in store:
        raise RuntimeError(f"Certificate '{name}' not found")
    entry = store.pop(name)
    _save_ssl_store(store)
    if entry.get("mode") == "content":
        _rmtree(Path(SSL_CERTS_DIR) / name)
    return {"name": name}


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