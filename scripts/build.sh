#!/usr/bin/env bash
# Copyright (c) 2026 controllercustom@myyahoo.com
# Distributed under the MIT license. See the LICENSE file for details.

# build.sh — compile the sketch for the Arduino GIGA R1 WiFi.
#
# Mirrors what Arduino IDE 2.x does: same FQBN, the library's shipped union
# config, no extra flags. Byte-for-byte the same image as the IDE build
# (measured: 163584 bytes flash, 70168 bytes static RAM either way).
#
# --library keeps this script working even when TinyUSB_Arduino is not in the
# sketchbook; scripts/install-library.sh is what puts it there for the IDE.
#
# Usage:
#   scripts/build.sh [sketch_dir] [extra arduino-cli args...]
#
# Examples:
#   scripts/build.sh
#   scripts/build.sh USB_Serial_Term --clean
#   TINYUSB_ARDUINO=/tmp/TinyUSB_Arduino scripts/build.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

LIB_DIR="${TINYUSB_ARDUINO:-$HOME/TinyUSB_Arduino}"
FQBN="arduino:mbed_giga:giga"

TARGET="${1:-$PROJ_ROOT/USB_Serial_Term}"
shift || true
[[ "$TARGET" = /* ]] || TARGET="$PROJ_ROOT/$TARGET"

if [[ ! -d "$TARGET" ]]; then
  echo "build.sh: no such sketch dir: $TARGET" >&2
  exit 1
fi
if [[ ! -d "$LIB_DIR" ]]; then
  echo "build.sh: no such library dir: $LIB_DIR" >&2
  exit 1
fi

NAME="$(basename "$TARGET")"

arduino-cli compile \
  --fqbn "$FQBN" \
  --library "$LIB_DIR" \
  --build-path "$PROJ_ROOT/build/$NAME" \
  "$TARGET" "$@"
