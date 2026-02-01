#!/usr/bin/env bash
# Build COLMAP with CUDA support and install to /workspace/tools/colmap
# Run once per pod volume - the binary persists across restarts.
#
# Usage: bash build_colmap_cuda.sh [--jobs N]
#
# Prerequisites (already in runpod/pytorch images):
#   - CUDA toolkit (nvcc)
#   - cmake, git, build-essential
#   - Various libs (we install them below)

set -euo pipefail

COLMAP_VERSION="${COLMAP_VERSION:-3.9.1}"
INSTALL_PREFIX="${CVP_VOLUME:-/workspace}/tools/colmap"
BUILD_DIR="/tmp/colmap_build"
JOBS="$(nproc)"

# Parse --jobs argument
if [[ "${1:-}" == "--jobs" ]] && [[ -n "${2:-}" ]]; then
  JOBS="$2"
elif [[ -n "${1:-}" ]] && [[ "${1:-}" =~ ^[0-9]+$ ]]; then
  JOBS="$1"
fi

echo "Building COLMAP $COLMAP_VERSION with CUDA..."
echo "  Install prefix: $INSTALL_PREFIX"
echo "  Build jobs: $JOBS"
echo

# Check CUDA
if ! command -v nvcc &>/dev/null; then
  echo "ERROR: nvcc not found. CUDA toolkit required." >&2
  exit 1
fi
echo "CUDA: $(nvcc --version | grep release)"

# Install build dependencies
echo "Installing build dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
  build-essential \
  cmake \
  ninja-build \
  git \
  libboost-all-dev \
  libeigen3-dev \
  libflann-dev \
  libfreeimage-dev \
  libmetis-dev \
  libgoogle-glog-dev \
  libgflags-dev \
  libsqlite3-dev \
  libglew-dev \
  qtbase5-dev \
  libqt5opengl5-dev \
  libcgal-dev \
  libceres-dev

# Clone and build
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

echo "Cloning COLMAP $COLMAP_VERSION..."
git clone --branch "$COLMAP_VERSION" --depth 1 https://github.com/colmap/colmap.git
cd colmap

mkdir build && cd build

# Detect CUDA architecture (RTX 4090 = 8.9 -> 89)
CUDA_ARCH="${CUDA_ARCH:-}"
if [[ -z "$CUDA_ARCH" ]] && command -v nvidia-smi &>/dev/null; then
  CAP=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1)
  if [[ -n "$CAP" ]]; then
    CUDA_ARCH="${CAP/./}"  # "8.9" -> "89"
  fi
fi
CUDA_ARCH="${CUDA_ARCH:-89}"  # Default to RTX 4090
echo "Using CUDA architecture: sm_$CUDA_ARCH"

echo "Configuring with CMake..."
cmake .. -GNinja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$INSTALL_PREFIX" \
  -DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCH" \
  -DGUI_ENABLED=OFF

echo "Building (this takes 10-30 minutes)..."
ninja -j"$JOBS"

echo "Installing to $INSTALL_PREFIX..."
ninja install

# Cleanup build dir (save container disk space)
cd /
rm -rf "$BUILD_DIR"

# Verify
if "$INSTALL_PREFIX/bin/colmap" --help | grep -q "COLMAP"; then
  echo
  echo "SUCCESS: COLMAP with CUDA installed to $INSTALL_PREFIX"
  echo
  echo "Add to PATH:"
  echo "  export PATH=\"$INSTALL_PREFIX/bin:\$PATH\""
  echo
  "$INSTALL_PREFIX/bin/colmap" --help | head -5
else
  echo "ERROR: Build completed but colmap binary not working" >&2
  exit 1
fi
