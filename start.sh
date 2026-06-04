#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "${ROOT_DIR}"

usage() {
  cat <<'EOF'
AI Router helper script

Usage:
  bash start.sh setup       Create Python venv, install backend deps, create .env
  bash start.sh run         Run the backend server
  bash start.sh build-web   Install dashboard deps and build static files
  bash start.sh dev-web     Run dashboard dev server
  bash start.sh check       Print detected versions and configured endpoint

Environment:
  PYTHON_BIN=python3.11     Override Python executable

Default endpoint:
  http://localhost:32128
EOF
}

ensure_python() {
  if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "Python executable not found: ${PYTHON_BIN}" >&2
    echo "Install Python 3.11+ or run with PYTHON_BIN=/path/to/python." >&2
    exit 1
  fi
}

ensure_venv() {
  ensure_python
  if [ ! -d "${VENV_DIR}" ]; then
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
  fi
}

venv_python() {
  if [ -x "${VENV_DIR}/bin/python" ]; then
    echo "${VENV_DIR}/bin/python"
  elif [ -x "${VENV_DIR}/Scripts/python.exe" ]; then
    echo "${VENV_DIR}/Scripts/python.exe"
  else
    echo "Virtualenv Python not found. Run: bash start.sh setup" >&2
    exit 1
  fi
}

cmd_setup() {
  ensure_venv
  local py
  py="$(venv_python)"

  "${py}" -m pip install --upgrade pip
  "${py}" -m pip install -r requirements.txt

  if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "Created .env from .env.example"
  else
    echo ".env already exists, leaving it unchanged"
  fi

  mkdir -p data

  echo
  echo "Setup complete."
  echo "Run server: bash start.sh run"
  echo "Dashboard:  http://localhost:32128"
}

cmd_run() {
  if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp .env.example .env
    echo "Created .env from .env.example"
  fi

  if [ ! -d "${VENV_DIR}" ]; then
    echo "Virtualenv not found. Running setup first."
    cmd_setup
  fi

  local py
  py="$(venv_python)"

  echo "Starting AI Router on http://localhost:32128"
  exec "${py}" run.py
}

cmd_build_web() {
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm not found. Install Node.js 20+ first." >&2
    exit 1
  fi

  ensure_venv
  local py
  py="$(venv_python)"

  cd "${ROOT_DIR}/web"
  npm install

  cd "${ROOT_DIR}"
  "${py}" scripts/build_static.py
}

cmd_dev_web() {
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm not found. Install Node.js 20+ first." >&2
    exit 1
  fi

  cd "${ROOT_DIR}/web"
  if [ ! -d "node_modules" ]; then
    npm install
  fi
  npm run dev
}

cmd_check() {
  ensure_python
  echo "Root: ${ROOT_DIR}"
  "${PYTHON_BIN}" --version
  if command -v node >/dev/null 2>&1; then
    node --version
  else
    echo "node: not found"
  fi
  if command -v npm >/dev/null 2>&1; then
    npm --version
  else
    echo "npm: not found"
  fi
  echo "Endpoint: http://localhost:32128"
}

case "${1:-}" in
  setup)
    cmd_setup
    ;;
  run)
    cmd_run
    ;;
  build-web)
    cmd_build_web
    ;;
  dev-web)
    cmd_dev_web
    ;;
  check)
    cmd_check
    ;;
  ""|help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown command: $1" >&2
    echo >&2
    usage >&2
    exit 1
    ;;
esac
