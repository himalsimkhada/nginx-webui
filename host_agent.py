#!/usr/bin/env python3
"""Host-side nginx control agent for nginx-webui.

nginx-webui running in a container edits the host's /etc/nginx (mounted), but
it cannot signal or restart the host nginx master itself. Run this agent ON
THE HOST and point nginx-webui at it via NGINX_CTL_URL — then status, `nginx -t`,
reload and restart all act on the *host* nginx.

Run as root, or give the service account passwordless sudo for nginx and
systemctl (see README). Example:

  sudo python3 host_agent.py --bind 127.0.0.1 --port 9401

API (JSON):
  GET  /status    host nginx version / running / pid / workers / config
  POST /check     nginx -t
  POST /reload    nginx -s reload
  POST /restart   systemctl restart nginx
"""
import json
import os
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

TIMEOUT = 30
NGINX_PIDFILE = "/run/nginx.pid"


def _run(cmd, timeout=TIMEOUT):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "command timed out", 124


def _sudo(cmd):
    return cmd if os.geteuid() == 0 else f"sudo {cmd}"


def _status():
    version = ""
    out, err, _ = _run("nginx -v")
    version = (err or out).strip().replace("nginx version:", "").replace("nginx/", "", 1).strip()

    master_pid = ""
    if os.path.exists(NGINX_PIDFILE):
        try:
            master_pid = open(NGINX_PIDFILE).read().strip()
        except OSError:
            pass
    running = bool(master_pid and os.path.exists(f"/proc/{master_pid}"))

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
        "config_file": config or "/etc/nginx/nginx.conf",
        "conf_dir": "/etc/nginx",
        "status_source": "agent",
    }


ACTIONS = {
    "check": "nginx -t",
    "reload": "nginx -s reload",
    "restart": "systemctl restart nginx",
}


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == "/status":
            self._json(_status())
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        action = urlparse(self.path).path.strip("/")
        if action not in ACTIONS:
            self._json({"ok": False, "error": f"unknown action '{action}'"}, 404)
            return
        out, err, rc = _run(_sudo(ACTIONS[action]))
        self._json({"ok": rc == 0, "output": out, "error": err, "valid": rc == 0})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))


def main():
    bind, port = "127.0.0.1", 9401
    args = sys.argv[1:]
    if "--bind" in args:
        bind = args[args.index("--bind") + 1]
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    print(f"nginx host control agent on http://{bind}:{port} (pid {os.getpid()})", flush=True)
    ThreadingHTTPServer((bind, port), Handler).serve_forever()


if __name__ == "__main__":
    main()