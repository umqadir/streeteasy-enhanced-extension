#!/usr/bin/env bash
set -euo pipefail

if ! command -v apt-get >/dev/null 2>&1; then
  echo "ERROR: apt-get not found. Install COLMAP via your base image or use an Ubuntu/Debian-based image." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates \
  curl \
  git \
  libgl1 \
  libglib2.0-0 \
  ffmpeg \
  unzip \
  wget \
  colmap

echo "OK: installed system deps (colmap)."
