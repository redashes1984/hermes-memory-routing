#!/usr/bin/env bash
# memory-routing installer — one-command deployment for Hermes Agent profiles
# Usage: bash install.sh [profile_name]
#   profile_name: defaults to "default", can specify any Hermes profile

set -euo pipefail

# ─── Colors ────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

# ─── Args ──────────────────────────────────────────────────────────────────
PROFILE="${1:-default}"
REPO="redashes1984/hermes-memory-routing"
CLONE_URL="https://github.com/${REPO}.git"

# HERMES_HOME resolution: when running inside a profile session, HERMES_HOME
# may already point to ~/.hermes/profiles/<name>/, so detect and handle both.
if [ -n "${HERMES_HOME:-}" ] && [ -f "${HERMES_HOME}/config.yaml" ]; then
    # Already in a profile directory — use it directly if PROFILE matches
    # or try to resolve from the global directory
    GLOBAL_HERMES="$HOME/.hermes"
    PROFILE_DIR="${GLOBAL_HERMES}/profiles/${PROFILE}"
    if [ ! -d "${PROFILE_DIR}" ]; then
        PROFILE_DIR="${HERMES_HOME}"
    fi
else
    HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
    PROFILE_DIR="${HERMES_HOME}/profiles/${PROFILE}"
fi

PLUGIN_DIR="${PROFILE_DIR}/plugins/memory-routing"
CONFIG_YAML="${PROFILE_DIR}/config.yaml"
ENV_FILE="${PROFILE_DIR}/.env"

# ─── Pre-flight checks ────────────────────────────────────────────────────
info "Installing memory-routing for profile: ${PROFILE}"

command -v git &>/dev/null    || fail "git is required but not installed"
command -v python3 &>/dev/null || fail "python3 is required but not installed"
command -v hermes &>/dev/null  || fail "hermes CLI is required but not installed"

if [ ! -d "${PROFILE_DIR}" ]; then
    fail "Profile directory not found: ${PROFILE_DIR}"
    echo "Available profiles:"
    hermes profile list 2>/dev/null || true
fi

# ─── Step 1: Clone or update repo ──────────────────────────────────────────
if [ -d "${PLUGIN_DIR}" ]; then
    warn "memory-routing already exists at ${PLUGIN_DIR}"
    if [ -t 0 ]; then
        read -rp "  Pull latest changes? (y/N) " -r
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            info "Pulling latest from ${REPO}..."
            (cd "${PLUGIN_DIR}" && git pull --ff-only) || (cd "${PLUGIN_DIR}" && git fetch origin && git checkout -f origin/main)
            ok "Updated"
        else
            info "Skipping git pull"
        fi
    else
        info "Non-interactive mode: skipping git pull"
    fi
else
    info "Cloning ${REPO}..."
    mkdir -p "${PROFILE_DIR}/plugins"
    git clone --depth 1 "${CLONE_URL}" "${PLUGIN_DIR}"
    ok "Cloned to ${PLUGIN_DIR}"
fi

# ─── Step 2: Install Python dependencies ───────────────────────────────────
info "Checking Python dependencies..."

NEED_INSTALL=false
python3 -c "import mcp" 2>/dev/null || NEED_INSTALL=true
python3 -c "import requests" 2>/dev/null || NEED_INSTALL=true

if [ "$NEED_INSTALL" = true ]; then
    info "Installing: mcp[fastmcp], requests"
    pip3 install --quiet mcp[fastmcp] requests 2>&1 | tail -1
    ok "Dependencies installed"
else
    ok "All dependencies already satisfied"
fi

# ─── Step 3: Extract LLM config from profile ──────────────────────────────
info "Extracting LLM configuration from profile config..."

read LLM_MODEL LLM_PROVIDER LLM_BASE_URL LLM_API_KEY_RAW < <(python3 -c "
import yaml, sys
try:
    with open('${CONFIG_YAML}', 'r') as f:
        cfg = yaml.safe_load(f)
    m = cfg.get('model', {})
    model     = m.get('default', '')
    provider  = m.get('provider', 'custom')
    base_url  = m.get('base_url', '')
    api_key   = m.get('api_key', '')
    print(model, provider, base_url, api_key)
except Exception:
    print('','','','')
" 2>/dev/null) || true

# If auto-detect succeeded, resolve API key from .env if it's a variable name
if [ -n "$LLM_MODEL" ]; then
    LLM_API_KEY="$LLM_API_KEY_RAW"
    if [[ "$LLM_API_KEY" =~ ^[A-Z_][A-Z0-9_]*$ ]]; then
        # Check profile .env first, then global .env
        for CHECK_ENV in "$ENV_FILE" "$HOME/.hermes/.env"; do
            if [ -f "$CHECK_ENV" ]; then
                RESOLVED=$(grep "^${LLM_API_KEY}=" "$CHECK_ENV" 2>/dev/null | head -1 | cut -d= -f2-) || true
                if [ -n "$RESOLVED" ]; then
                    LLM_API_KEY="$RESOLVED"
                    break
                fi
            fi
        done
    fi
else
    warn "Could not auto-detect LLM config from ${CONFIG_YAML}"
    if [ -t 0 ]; then
        echo "  Please enter the LLM settings for memory-routing's intent classifier."
        echo "  (Press Enter to skip and use server.py defaults)"
        read -rp "  Model [Qwen3.5-9B-AWQ]: " LLM_MODEL
        LLM_MODEL="${LLM_MODEL:-Qwen3.5-9B-AWQ}"
        read -rp "  Provider [custom]: " LLM_PROVIDER
        LLM_PROVIDER="${LLM_PROVIDER:-custom}"
        read -rp "  Base URL [http://10.10.4.9:8000/v1]: " LLM_BASE_URL
        LLM_BASE_URL="${LLM_BASE_URL:-http://10.10.4.9:8000/v1}"
        read -rp "  API Key [VLLM]: " LLM_API_KEY
        LLM_API_KEY="${LLM_API_KEY:-VLLM}"
    else
        warn "Non-interactive mode: using server.py defaults"
        LLM_MODEL="Qwen3.5-9B-AWQ"
        LLM_PROVIDER="custom"
        LLM_BASE_URL="http://10.10.4.9:8000/v1"
        LLM_API_KEY="VLLM"
    fi
fi

info "LLM configuration for memory-routing:"
echo "  Provider:  ${LLM_PROVIDER}"
echo "  Model:     ${LLM_MODEL}"
echo "  Base URL:  ${LLM_BASE_URL}"
echo "  API Key:   ${LLM_API_KEY:0:6}..."

# ─── Step 4: Register MCP server in config.yaml ────────────────────────────
info "Registering MCP server in ${CONFIG_YAML}..."

# Check if already registered
ALREADY_EXISTS=$(python3 -c "
import yaml
with open('${CONFIG_YAML}', 'r') as f:
    cfg = yaml.safe_load(f)
print('yes' if cfg.get('mcp_servers', {}).get('memory-routing') else 'no')
" 2>/dev/null) || ALREADY_EXISTS="no"

if [ "$ALREADY_EXISTS" = "yes" ]; then
    warn "memory-routing MCP server already registered in config.yaml"
    if [ -t 0 ]; then
        read -rp "  Update the existing registration? (y/N) " -r
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            ok "Skipping config update"
        fi
    else
        info "Non-interactive mode: updating existing registration"
    fi
fi

# Apply registration (new or update)
python3 - "$CONFIG_YAML" "$PLUGIN_DIR" "$LLM_PROVIDER" "$LLM_MODEL" "$LLM_BASE_URL" "$LLM_API_KEY" << 'PYEOF'
import yaml, shutil, sys

config_path   = sys.argv[1]
plugin_path   = sys.argv[2]
llm_provider  = sys.argv[3]
llm_model     = sys.argv[4]
llm_base_url  = sys.argv[5]
llm_api_key   = sys.argv[6]

# Backup first
shutil.copy2(config_path, config_path + '.bak.memory-routing')

with open(config_path, 'r') as f:
    cfg = yaml.safe_load(f)

if 'mcp_servers' not in cfg:
    cfg['mcp_servers'] = {}

cfg['mcp_servers']['memory-routing'] = {
    'command': '/usr/bin/python3',
    'args': [plugin_path + '/server.py'],
    'env': {
        'HERMES_MCP_SERVER_NAME': 'memory-routing',
        'HERMES_MCP_TOOLSET': 'memory',
        'HERMES_LLM_PROVIDER': llm_provider,
        'HERMES_LLM_MODEL': llm_model,
        'HERMES_LLM_BASE_URL': llm_base_url,
        'HERMES_LLM_API_KEY': llm_api_key,
    },
    'enabled': True,
}

with open(config_path, 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

print("OK")
PYEOF

ok "MCP server registered in config.yaml"
ok "Backup saved: ${CONFIG_YAML}.bak.memory-routing"

# ─── Step 5: Verify ────────────────────────────────────────────────────────
info "Verifying installation..."

python3 -c "
import py_compile
py_compile.compile('${PLUGIN_DIR}/server.py', doraise=True)
" 2>&1

ok "server.py syntax check passed"

# Check config has the entry
python3 -c "
import yaml
with open('${CONFIG_YAML}', 'r') as f:
    cfg = yaml.safe_load(f)
mr = cfg.get('mcp_servers', {}).get('memory-routing', {})
if mr:
    print('MCP entry in config: OK')
    env = mr.get('env', {})
    for k in ['HERMES_LLM_PROVIDER', 'HERMES_LLM_MODEL', 'HERMES_LLM_BASE_URL']:
        print(f'  {k}: {env.get(k, \"MISSING\")}')
else:
    print('WARNING: memory-routing not found in mcp_servers')
"

# ─── Done ──────────────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  memory-routing installed successfully!"
echo "=============================================="
echo ""
echo "  Profile:    ${PROFILE}"
echo "  Plugin:     ${PLUGIN_DIR}"
echo "  LLM Model:  ${LLM_MODEL}"
echo ""
echo "  Next steps:"
echo "  1. Restart Hermes gateway:"
echo "     hermes gateway restart"
echo ""
echo "  2. Or reload MCP servers in-session:"
echo "     /reload-mcp"
echo ""
echo "  3. Test the tool:"
echo "     hermes chat -q '使用 route_and_save_memory 测试记忆路由'"
echo ""
echo "  4. Configure AGENTS.md to use route_and_save_memory"
echo "     (not the memory tool) for cross-session memory"
echo ""