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
  bash start.sh setup       Create Python venv, install backend deps, create .env, install Claude Code CLI when possible
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

detect_claude_cli() {
  if command -v claude >/dev/null 2>&1; then
    command -v claude
    return 0
  fi

  if command -v npm >/dev/null 2>&1; then
    local npm_prefix npm_root
    npm_prefix="$(npm config get prefix 2>/dev/null || true)"
    npm_root="$(npm root -g 2>/dev/null || true)"

    local candidates=(
      "${npm_prefix}/bin/claude"
      "${npm_root}/@anthropic-ai/claude-code/bin/claude"
      "${npm_root}/@anthropic-ai/claude-code/bin/claude.exe"
      "${npm_root}/@anthropic-ai/claude-code/node_modules/@anthropic-ai/claude-code-linux-x64/claude"
      "${HOME}/.local/bin/claude"
      "${HOME}/.npm-global/bin/claude"
      "${HOME}/.hermes/node/bin/claude"
      "/usr/local/bin/claude"
      "/usr/bin/claude"
    )

    local candidate
    for candidate in "${candidates[@]}"; do
      if [ -x "${candidate}" ]; then
        echo "${candidate}"
        return 0
      fi
    done
  fi

  local search_dir found
  for search_dir in ${CLAUDE_CLI_SEARCH_DIRS:-"${HOME} /usr/local /opt"}; do
    if [ -d "${search_dir}" ]; then
      found="$(find "${search_dir}" -type f -name claude -perm -111 2>/dev/null | head -n 1 || true)"
      if [ -n "${found}" ]; then
        echo "${found}"
        return 0
      fi
    fi
  done

  return 1
}

set_env_value() {
  local file="$1"
  local key="$2"
  local value="$3"
  local tmp="${file}.tmp.$$"

  if [ ! -f "${file}" ]; then
    touch "${file}"
  fi

  if grep -q "^${key}=" "${file}"; then
    while IFS= read -r line || [ -n "${line}" ]; do
      case "${line}" in
        "${key}="*) printf '%s=%s\n' "${key}" "${value}" ;;
        *) printf '%s\n' "${line}" ;;
      esac
    done < "${file}" > "${tmp}"
    mv "${tmp}" "${file}"
  else
    printf '\n%s=%s\n' "${key}" "${value}" >> "${file}"
  fi
}

ensure_claude_global_command() {
  local claude_bin="$1"
  local claude_dir
  claude_dir="$(dirname "${claude_bin}")"

  if command -v claude >/dev/null 2>&1; then
    return 0
  fi

  if [ -d "/usr/local/bin" ] && [ -w "/usr/local/bin" ]; then
    ln -sf "${claude_bin}" "/usr/local/bin/claude"
    echo "Linked Claude Code CLI to /usr/local/bin/claude"
  elif [ -d "/usr/local/bin" ] && command -v sudo >/dev/null 2>&1; then
    if sudo ln -sf "${claude_bin}" "/usr/local/bin/claude"; then
      echo "Linked Claude Code CLI to /usr/local/bin/claude"
    fi
  fi

  if command -v claude >/dev/null 2>&1; then
    return 0
  fi

  case ":${PATH}:" in
    *":${claude_dir}:"*) return 0 ;;
  esac

  export PATH="${claude_dir}:${PATH}"
  if [ -f "${HOME}/.bashrc" ]; then
    if ! grep -Fq "${claude_dir}" "${HOME}/.bashrc"; then
      printf '\nexport PATH="%s:$PATH"\n' "${claude_dir}" >> "${HOME}/.bashrc"
      echo "Added ${claude_dir} to ~/.bashrc PATH"
    fi
  else
    printf 'export PATH="%s:$PATH"\n' "${claude_dir}" > "${HOME}/.bashrc"
    echo "Created ~/.bashrc with Claude Code CLI PATH"
  fi
}

configure_claude_cli() {
  local claude_bin
  if claude_bin="$(detect_claude_cli)"; then
    echo "Claude Code CLI detected: ${claude_bin}"
    ensure_claude_global_command "${claude_bin}"
    set_env_value ".env" "AI_ROUTER_CLAUDE_CLI_BINARY" "${claude_bin}"
    echo "Configured AI_ROUTER_CLAUDE_CLI_BINARY in .env"
  else
    echo "Claude Code CLI binary was not detected." >&2
    echo "If it is installed outside PATH, set AI_ROUTER_CLAUDE_CLI_BINARY=/path/to/claude in .env" >&2
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

  if detect_claude_cli >/dev/null 2>&1; then
    configure_claude_cli
  elif command -v npm >/dev/null 2>&1; then
    echo "Installing Claude Code CLI..."
    if npm install -g @anthropic-ai/claude-code; then
      configure_claude_cli
    else
      echo "Claude Code CLI install failed. You can retry manually:" >&2
      echo "  npm install -g @anthropic-ai/claude-code" >&2
    fi
  else
    echo "npm not found; skipping Claude Code CLI install." >&2
    echo "Install Node.js/npm, then run: npm install -g @anthropic-ai/claude-code" >&2
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
  if detect_claude_cli >/dev/null 2>&1; then
    echo "claude: $(detect_claude_cli)"
  else
    echo "claude: not found"
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
