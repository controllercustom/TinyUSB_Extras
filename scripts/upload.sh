#!/usr/bin/env bash
# Copyright (c) 2026 controllercustom@myyahoo.com
# Distributed under the MIT license. See the LICENSE file for details.

# upload.sh — compile and flash the sketch over USB-C (DFU).
#
# The GIGA uploads by DFU at 0x08040000; arduino-cli performs the 1200 bps
# touch reset that puts the bootloader in charge. Exit any serial monitor
# first, or the port is busy and the touch reset fails.
#
# Two steps because arduino-cli's `upload` has no --library flag: build.sh
# compiles with --library into build/<sketch>/, and `upload --input-dir`
# flashes what is already built.
#
# Usage:
#   scripts/upload.sh [port] [extra arduino-cli upload args...]
#
# Examples:
#   scripts/upload.sh                 # /dev/ttyACM0
#   scripts/upload.sh /dev/ttyACM1
#   PORT=/dev/ttyACM1 scripts/upload.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

FQBN="arduino:mbed_giga:giga"
TARGET="$PROJ_ROOT/USB_Serial_Term"
NAME="$(basename "$TARGET")"
BUILD_DIR="$PROJ_ROOT/build/$NAME"

PORT="${1:-${PORT:-/dev/ttyACM0}}"
shift || true

"$SCRIPT_DIR/build.sh" "$TARGET" --output-dir "$BUILD_DIR"

arduino-cli upload \
  -p "$PORT" \
  --fqbn "$FQBN" \
  --input-dir "$BUILD_DIR" \
  "$TARGET" "$@"

echo "uploaded to $PORT — monitor with scripts/monitor.sh $PORT"
