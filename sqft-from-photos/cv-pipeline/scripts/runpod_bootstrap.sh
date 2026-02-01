#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
RunPod bootstrap for cv-pipeline.

Default: installs system deps (COLMAP), sets up Python env via uv, and installs Codex + Claude Code CLIs.

Usage:
  bash cv-pipeline/scripts/runpod_bootstrap.sh [--no-system] [--no-python] [--no-node] [--help]

Environment:
  CVP_VOLUME   Persistent root for models + runs (defaults to /workspace, else /runpod-volume).
  CVP_WORKDIR  Ephemeral working dir (defaults to /tmp/cv_pipeline_work).

Notes:
  - Re-runnable / idempotent.
  - Does NOT download model weights; run download_models.py after this finishes.
EOF
}

do_system=1
do_python=1
do_node=1

for arg in "$@"; do
  case "$arg" in
    --help|-h)
      usage
      exit 0
      ;;
    --no-system)
      do_system=0
      ;;
    --no-python)
      do_python=0
      ;;
    --no-node)
      do_node=0
      ;;
    "")
      # No-op: protects against callers that accidentally pass an empty arg.
      ;;
    *)
      echo "Unknown arg: $arg" >&2
      usage >&2
      exit 2
      ;;
  esac
done

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "${script_dir}/.." && pwd)" # cv-pipeline/

pick_volume_root() {
  if [[ -n "${CVP_VOLUME:-}" ]]; then
    echo "$CVP_VOLUME"
    return 0
  fi
  if [[ -d "/workspace" ]]; then
    echo "/workspace"
    return 0
  fi
  if [[ -d "/runpod-volume" ]]; then
    echo "/runpod-volume"
    return 0
  fi
  echo "${HOME}/.cache/cv_pipeline"
}

export CVP_VOLUME
CVP_VOLUME="$(pick_volume_root)"
# Use /workspace/work instead of /tmp so work persists across restarts
export CVP_WORKDIR="${CVP_WORKDIR:-$CVP_VOLUME/work}"

mkdir -p "$CVP_VOLUME"/{models,runs,tools,.cache} "$CVP_WORKDIR"

export TORCH_HOME="${TORCH_HOME:-$CVP_VOLUME/models/torch}"
export HF_HOME="${HF_HOME:-$CVP_VOLUME/models/hf}"
export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-$CVP_VOLUME/models/hf/transformers}"
export HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE:-$CVP_VOLUME/models/hf/hub}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$CVP_VOLUME/.cache/uv}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$CVP_VOLUME/.cache}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$CVP_VOLUME/.cache/matplotlib}"
export NPM_CONFIG_PREFIX="${NPM_CONFIG_PREFIX:-$CVP_VOLUME/tools/npm}"
export PATH="$NPM_CONFIG_PREFIX/bin:$HOME/.local/bin:$PATH"

env_file="$CVP_VOLUME/cv_pipeline_env.sh"
cat >"$env_file" <<EOF
export CVP_VOLUME="${CVP_VOLUME}"
export CVP_WORKDIR="\${CVP_WORKDIR:-$CVP_VOLUME/work}"  # Persists across restarts

# Use CUDA COLMAP if available (built via build_colmap_cuda.sh)
if [[ -x "\$CVP_VOLUME/tools/colmap/bin/colmap" ]]; then
  export PATH="\$CVP_VOLUME/tools/colmap/bin:\$PATH"
fi

export TORCH_HOME="\${TORCH_HOME:-$CVP_VOLUME/models/torch}"
export HF_HOME="\${HF_HOME:-$CVP_VOLUME/models/hf}"
export TRANSFORMERS_CACHE="\${TRANSFORMERS_CACHE:-$CVP_VOLUME/models/hf/transformers}"
export HUGGINGFACE_HUB_CACHE="\${HUGGINGFACE_HUB_CACHE:-$CVP_VOLUME/models/hf/hub}"

export UV_CACHE_DIR="\${UV_CACHE_DIR:-$CVP_VOLUME/.cache/uv}"
export XDG_CACHE_HOME="\${XDG_CACHE_HOME:-$CVP_VOLUME/.cache}"
export MPLCONFIGDIR="\${MPLCONFIGDIR:-$CVP_VOLUME/.cache/matplotlib}"
export NPM_CONFIG_PREFIX="\${NPM_CONFIG_PREFIX:-$CVP_VOLUME/tools/npm}"
export PATH="\$NPM_CONFIG_PREFIX/bin:\$HOME/.local/bin:\$PATH"

# Git auth: if GITHUB_TOKEN is set (via RunPod secret), configure git to use it
if [[ -n "\${GITHUB_TOKEN:-}" ]]; then
  git config --global credential.helper store
  git config --global user.email "uzairq93@gmail.com"
  git config --global user.name "umqadir"
  echo "https://oauth2:\${GITHUB_TOKEN}@github.com" > ~/.git-credentials 2>/dev/null || true
fi
EOF

echo "CVP_VOLUME=$CVP_VOLUME"
echo "CVP_WORKDIR=$CVP_WORKDIR"
echo "Wrote: $env_file (source this in new shells)"
echo

install_system() {
  if command -v colmap >/dev/null 2>&1; then
    echo "OK: colmap already installed: $(command -v colmap)"
    return 0
  fi

echo "Installing system deps (COLMAP)..."
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    bash "$project_dir/scripts/runpod_setup_system.sh"
    return 0
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo bash "$project_dir/scripts/runpod_setup_system.sh"
    return 0
  fi
  echo "ERROR: Need root to install COLMAP. Re-run as root or install colmap in your base image." >&2
  return 1
}

install_uv() {
  if command -v uv >/dev/null 2>&1; then
    echo "OK: uv found: $(command -v uv)"
    return 0
  fi

  echo "Installing uv..."
  if ! command -v curl >/dev/null 2>&1; then
    echo "ERROR: curl not found (needed to install uv). Install curl or run --no-system only if uv already exists." >&2
    return 1
  fi
  curl -LsSf https://astral.sh/uv/install.sh | sh

  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv install succeeded but uv not on PATH. Try: export PATH=\"\$HOME/.local/bin:\$PATH\"" >&2
    return 1
  fi
  echo "OK: uv installed: $(command -v uv)"
}

install_python() {
  echo "Setting up Python env (uv, system-site-packages)..."
  cd "$project_dir"
  uv venv --system-site-packages --allow-existing .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  uv sync --extra gpu --extra sfm --extra depth --extra open3d --extra research --active
  echo "OK: Python env ready at $project_dir/.venv"
}

ensure_node() {
  if command -v node >/dev/null 2>&1; then
    major="$(node -p 'process.versions.node.split(\".\")[0]' 2>/dev/null || true)"
    if [[ "$major" =~ ^[0-9]+$ ]] && [[ "$major" -ge 18 ]]; then
      echo "OK: node $(node --version)"
      return 0
    fi
  fi

  echo "Installing Node.js via nvm into: $CVP_VOLUME/tools/nvm"
  if ! command -v curl >/dev/null 2>&1; then
    echo "ERROR: curl not found (needed for nvm install)." >&2
    return 1
  fi

  export NVM_DIR="${NVM_DIR:-$CVP_VOLUME/tools/nvm}"
  mkdir -p "$NVM_DIR"

  if [[ ! -s "$NVM_DIR/nvm.sh" ]]; then
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
  fi

  # shellcheck disable=SC1090
  source "$NVM_DIR/nvm.sh"

  nvm install 20
  nvm alias default 20
  nvm use default

  echo "OK: node $(node --version)"
}

install_node_tools() {
  ensure_node
  mkdir -p "$NPM_CONFIG_PREFIX"

  echo "Installing CLIs to: $NPM_CONFIG_PREFIX"
  npm install -g @openai/codex @anthropic-ai/claude-code

  echo "OK: codex=$(command -v codex || true)"
  echo "OK: claude=$(command -v claude || true)"
}

if [[ "$do_system" -eq 1 ]]; then
  install_system
fi

if [[ "$do_python" -eq 1 ]]; then
  install_uv
  install_python
fi

if [[ "$do_node" -eq 1 ]]; then
  if [[ "$do_system" -eq 0 ]] && ! command -v curl >/dev/null 2>&1; then
    echo "ERROR: curl is required to install Node/CLIs. Run without --no-system or install curl first." >&2
    exit 1
  fi
  install_node_tools
fi

cat <<EOF

Next steps:
  1) source "$env_file"
  2) Download default model:
       cd "$project_dir"
       uv run python scripts/download_models.py depth-anything-metric --encoder vitl --dataset hypersim
  3) Run a quick sanity check:
       uv run python scripts/doctor.py

For storage sizing: python "$project_dir/scripts/estimate_storage.py"
For VRAM sizing:    uv run python "$project_dir/scripts/profile_vram.py" all --amp --n-images 4 --size 512
EOF
