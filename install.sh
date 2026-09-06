#!/usr/bin/env bash
#
# nginx-webui — interactive installer.
#
# Offers two deployment modes:
#   1. Docker + host control agent : web-UI container managing nginx installed
#      on this machine (status/check/reload/restart via the host agent)
#   2. Manual (all on host)        : nginx and the web UI installed directly
#      on this machine (systemd + venv)
#
# Usage:  sudo ./install.sh   (or: ./install.sh --check | --help)
# One-liner:  curl -fsSL https://raw.githubusercontent.com/himalsimkhada/nginx-webui/main/install.sh | bash

set -euo pipefail

REPO_URL="https://github.com/himalsimkhada/nginx-webui.git"

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# ── Output helpers ───────────────────────────────────────────────────────

if [ -t 1 ]; then
  C_RESET=$'\e[0m'; C_GREEN=$'\e[32m'; C_YELLOW=$'\e[33m'; C_RED=$'\e[31m'; C_BOLD=$'\e[1m'
else
  C_RESET=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BOLD=""
fi

info()  { printf '%s\n' "${C_BOLD}==>${C_RESET} $*"; }
ok()    { printf '%s%s%s\n' "${C_GREEN}    $*${C_RESET}"; }
warn()  { printf '%s%s%s\n' "${C_YELLOW}!!  $*${C_RESET}"; }
die()   { printf '%s%s%s\n' "${C_RED}FATAL:$*${C_RESET}" >&2; exit 1; }

has_cmd() { command -v "$1" >/dev/null 2>&1; }

# ── Self-bootstrap ────────────────────────────────────────────────────────
# Support one-liner installs (curl ... | bash): when the script is streamed
# there is no repo checkout in $DIR, so fetch the repository first and then
# re-run this installer from inside it (finishes with the user's chosen mode).

if [ ! -f "$DIR/app.py" ] || [ ! -f "$DIR/docker-compose.yml" ]; then
  echo "==> One-liner install: no repo checkout in \"$DIR\"."
  has_cmd git || die "git is required for the one-liner install (curl | bash)."
  has_cmd curl || has_cmd wget || warn "Neither curl nor wget found; check the URL you piped."

  default_target="$HOME/nginx-webui"
  target=""
  read -r -p "Install the project into [$default_target]: " target
  target="${target:-$default_target}"

  mkdir -p "$(dirname "$target")"
  if [ -d "$target" ] && [ -f "$target/app.py" ]; then
    info "Updating existing checkout at $target"
    (cd "$target" && git pull --ff-only) >/dev/null 2>&1 || true
  else
    info "Cloning $REPO_URL into $target"
    git clone --quiet --depth 1 "$REPO_URL" "$target"
  fi
  cd "$target"
  exec bash "$target/install.sh" "$@"
fi

# ── System detection ─────────────────────────────────────────────────────

OS_ID="unknown"
OS_NAME="unknown"
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  OS_ID="${ID:-unknown}"
  OS_NAME="${NAME:-$OS_ID}"
fi

is_deb() { case "$OS_ID" in debian|ubuntu|linuxmint|pop|elementary|kali|raspbian) return 0;; *) return 1;; esac; }
is_rpm() { case "$OS_ID" in rhel|fedora|centos|almalinux|rocky|ol|amazon) return 0;; *) return 1;; esac; }
is_arch() { case "$OS_ID" in arch|manjaro|endeavouros) return 0;; *) return 1;; esac; }

pkg_install() {
  if is_deb; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq "$@"
  elif is_rpm; then
    if command -v dnf >/dev/null 2>&1; then sudo dnf install -y "$@"
    else sudo yum install -y "$@"; fi
  elif is_arch; then
    sudo pacman -S --noconfirm --needed "$@"
  else
    die "Unsupported distro ($OS_NAME). Please install dependencies manually."
  fi
}

# ── Generic helpers ──────────────────────────────────────────────────────

random_secret() {
  if has_cmd openssl; then
    openssl rand -hex 32
  else
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
  fi
}

ask_password() {
  # Prompts until a non-empty password is given; stores in WEBUI_PASSWORD.
  WEBUI_PASSWORD=""
  while [ -z "$WEBUI_PASSWORD" ]; do
    read -r -s -p "    Web UI password (used to log in): " WEBUI_PASSWORD
    echo ""
    if [ -z "$WEBUI_PASSWORD" ]; then
      warn "Password cannot be empty. Leaving it blank is not supported."
    else
      read -r -s -p "    Confirm password: " WEBUI_PASSWORD_CONFIRM
      echo ""
      if [ "$WEBUI_PASSWORD" != "$WEBUI_PASSWORD_CONFIRM" ]; then
        warn "Passwords do not match. Try again."
        WEBUI_PASSWORD=""
      fi
    fi
  done
  SECRET_KEY="$(random_secret)"
}

write_env_file() {
  # Writes .env from an array of KEY=VALUE lines passed on stdin.
  local envpath="$DIR/.env"
  info "Writing $envpath"
  cat > "$envpath"
  chmod 600 "$envpath" 2>/dev/null || true
}

# ── nginx + host control agent ───────────────────────────────────────────

nginx_packages() {
  if is_deb; then echo "nginx"
  elif is_rpm; then echo "nginx"
  elif is_arch; then echo "nginx"
  fi
}

ensure_host_nginx() {
  # The managed nginx must live on this machine (the docker container edits
  # the mounted /etc/nginx, and the agent signals its master).
  local ngconf="/etc/nginx/nginx.conf"
  if ! has_cmd nginx; then
    warn "nginx is not installed."
    read -r -p "    Install nginx now? [y/N] " ans
    if [ "${ans:-n}" != "y" ] && [ "${ans:-n}" != "Y" ]; then
      die "nginx is required. Re-run after installing nginx."
    fi
    # shellcheck disable=SC2046
    pkg_install $(nginx_packages)
  fi
  has_cmd nginx || die "nginx is required"
  if [ ! -f "$ngconf" ]; then
    die "Expected nginx config at $ngconf but it is missing."
  fi
  ok "nginx present ($ngconf)"
}

ensure_host_agent() {
  info "Installing the host control agent so the container can manage"
  info "the HOST nginx (PID namespaces block direct signals; see README)."
  sudo cp "$DIR/host_agent.py" /usr/local/bin/nginx-webui-host-agent.py
  sudo chmod 755 /usr/local/bin/nginx-webui-host-agent.py
  sudo cp "$DIR/nginx-webui-host-agent.service" /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now nginx-webui-host-agent >/dev/null 2>&1
  sleep 1
  if curl -fsS http://127.0.0.1:9401/status >/dev/null 2>&1; then
    ok "Host control agent active on :9401 (binds 0.0.0.0 for containers)"
  else
    warn "Agent installed but not answering yet. Check: sudo systemctl status nginx-webui-host-agent"
  fi
}

ensure_docker() {
  if ! has_cmd docker || ! docker compose version >/dev/null 2>&1; then
    warn "Docker with the compose plugin is required but not installed."
    read -r -p "    Install Docker now? [y/N] " ans
    if [ "${ans:-n}" != "y" ] && [ "${ans:-n}" != "Y" ]; then
      die "Docker is required for this mode. Re-run after installing Docker."
    fi
    info "Installing Docker"
    if is_deb; then
      sudo apt-get update -qq
      sudo apt-get install -y -qq docker.io docker-compose-v2
    elif is_rpm; then
      sudo dnf install -y moby-engine docker-compose-plugin 2>/dev/null \
        || sudo dnf install -y moby-engine || \
        warn "Could not auto-install Docker. Install it manually and re-run."
    elif is_arch; then
      sudo pacman -S --noconfirm --needed docker docker-compose 2>/dev/null \
        || sudo pacman -S --noconfirm --needed docker
    else
      die "Unsupported distro for automatic Docker install. Install Docker manually."
    fi
  fi

  if ! docker compose version >/dev/null 2>&1; then
    die "Docker compose plugin is missing. Install docker-compose and re-run."
  fi
  if ! docker info >/dev/null 2>&1; then
    warn "Docker daemon is not reachable. Starting the docker service..."
    sudo systemctl enable --now docker >/dev/null 2>&1 || true
    sleep 2
  fi
  docker info >/dev/null 2>&1 \
    || die "Docker daemon is not reachable. Start it (sudo systemctl start docker) and re-run."
  ok "Docker + compose plugin available and the daemon is running"
}

ensure_python_tools() {
  if is_deb; then
    pkg_install python3 python3-venv
  elif is_rpm; then
    pkg_install python3 python3-pip
  elif is_arch; then
    pkg_install python python-virtualenv
  fi
  has_cmd python3 || die "python3 is required"
}

# ── Mode 1: Docker + host control agent ──────────────────────────────────

mode_docker() {
  info "Mode 1: Docker + host control agent (web UI container, host nginx)"
  ensure_docker
  ensure_host_nginx
  ensure_host_agent

  ask_password
  write_env_file <<EOF
WEBUI_PASSWORD=$WEBUI_PASSWORD
SECRET_KEY=$SECRET_KEY
NGINX_WEBUI_PORT=8400
NGINX_CONF_DIR=/etc/nginx
SERVICE_NAME=nginx-webui
LOG_FILE=
# Status/control are provided by the host control agent (installed above).
NGINX_STATUS_URL=
NGINX_CTL_URL=http://host.docker.internal:9401
EOF

  info "Starting the web UI container (builds the image on first run)"
  docker compose up -d --build
  ok "Deployed. Open http://localhost:8400"
  ok "The dashboard manages the HOST nginx (/etc/nginx mounted, agent on :9401)."
}

# ── Mode 2: Manual (all on host) ─────────────────────────────────────────

mode_manual() {
  info "Mode 2: Manual install (nginx + web UI on this machine)"
  ensure_host_nginx
  ensure_python_tools

  info "Setting up Python venv"
  python3 -m venv "$DIR/venv"
  "$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt"
  ok "Dependencies installed"

  ask_password
  info "Writing credentials to /etc/nginx-webui.env"
  local envout="/etc/nginx-webui.env"
  sudo tee "$envout" >/dev/null <<EOF
WEBUI_PASSWORD=$WEBUI_PASSWORD
SECRET_KEY=$SECRET_KEY
EOF
  sudo chmod 600 "$envout"

  info "Installing systemd service"
  {
    echo "[Unit]"
    echo "Description=nginx-webui backend (API-only)"
    echo "After=network-online.target nginx.service"
    echo "Wants=network-online.target"
    echo "Wants=nginx.service"
    echo ""
    echo "[Service]"
    echo "Type=simple"
    echo "User=root"
    echo "WorkingDirectory=$DIR"
    echo "ExecStart=$DIR/venv/bin/python app.py"
    echo "EnvironmentFile=-/etc/nginx-webui.env"
    echo "Restart=on-failure"
    echo "RestartSec=3"
    echo ""
    echo "[Install]"
    echo "WantedBy=multi-user.target"
  } | sudo tee /etc/systemd/system/nginx-webui.service >/dev/null

  sudo systemctl daemon-reload
  sudo systemctl enable nginx-webui
  sudo systemctl restart nginx-webui
  ok "Deployed. Open http://localhost:8400"
  ok "Manage with: sudo systemctl status nginx-webui"
}

# ── Main menu / flags ────────────────────────────────────────────────────

show_menu() {
  echo ""
  echo "Select how you want to run the nginx Web UI:"
  echo ""
  echo "  1) Docker + host agent   - web UI container managing nginx on THIS machine (recommended)"
  echo "  2) Manual                - nginx and the web UI both installed directly on THIS machine"
  echo ""
  while :; do
    read -r -p "Enter your choice [1-2]: " choice
    case "$choice" in
      1) mode_docker; return;;
      2) mode_manual; return;;
      *) warn "Please choose 1 or 2.";;
    esac
  done
}

do_check() {
  echo "── System check ─────────────────────────────"
  echo "Distro        : $OS_NAME ($OS_ID)"
  echo "Package tool  : $(is_deb && echo 'apt' || (is_rpm && echo 'rpm/dnf' || (is_arch && echo 'pacman' || echo 'unknown')))"
  echo "nginx conf    : /etc/nginx/nginx.conf"
  echo ""
  if has_cmd nginx; then
    echo "nginx         : installed ($(nginx -v 2>&1 | sed 's/^nginx version: //'))"
  else
    echo "nginx         : NOT installed"
  fi
  if curl -fsS http://127.0.0.1:9401/status >/dev/null 2>&1; then
    echo "host agent    : active on 127.0.0.1:9401"
  else
    echo "host agent    : not running"
  fi
  if has_cmd docker && docker compose version >/dev/null 2>&1; then
    echo "Docker+compose: available"
  else
    echo "Docker+compose: missing"
  fi
  if has_cmd python3; then
    echo "python3       : $(command -v python3)"
  else
    echo "python3       : missing"
  fi
  echo ""
}

case "${1:-}" in
  --check|-c) do_check; exit 0;;
  --help|-h)
    sed -n '1,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
  "")
    if [ "$(id -u)" -eq 0 ]; then
      warn "Running as root. Prefer running as a normal sudo user on some distros."
    fi
    show_menu
    echo ""
    echo "Done!"
    ;;
  *) echo "Unknown option: $1 (use --help)"; exit 1;;
esac