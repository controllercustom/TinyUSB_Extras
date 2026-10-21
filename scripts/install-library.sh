#!/usr/bin/env bash
# Copyright (c) 2026 controllercustom@myyahoo.com
# Distributed under the MIT license. See the LICENSE file for details.

# install-library.sh — make TinyUSB_Arduino visible to Arduino IDE 2.x.
#
# The IDE resolves #include <ArduinoTinyUSB.h> from the sketchbook only: it has
# no equivalent of arduino-cli's --library flag. So the library has to live in
# <sketchbook>/libraries/. This script symlinks it there, which keeps a single
# copy of the library (~/TinyUSB_Arduino stays the one you edit, git, and
# version) — the same arrangement other libraries in this workspace use.
#
# Override the source or the sketchbook:
#   TINYUSB_ARDUINO=/path/to/TinyUSB_Arduino SKETCHBOOK=/path/to/Arduino ./install-library.sh

set -euo pipefail

LIB_SRC="${TINYUSB_ARDUINO:-$HOME/TinyUSB_Arduino}"
SKETCHBOOK="${SKETCHBOOK:-$HOME/Arduino}"
DEST="$SKETCHBOOK/libraries/TinyUSB_Arduino"

if [[ ! -f "$LIB_SRC/library.properties" ]]; then
  echo "install-library.sh: no library.properties in $LIB_SRC" >&2
  echo "  set TINYUSB_ARDUINO to the TinyUSB_Arduino checkout" >&2
  exit 1
fi

mkdir -p "$SKETCHBOOK/libraries"

if [[ -e "$DEST" || -L "$DEST" ]]; then
  if [[ -L "$DEST" ]]; then
    echo "already installed: $DEST -> $(readlink "$DEST")"
    exit 0
  fi
  echo "install-library.sh: $DEST exists and is not a symlink" >&2
  echo "  remove or rename it first, then re-run" >&2
  exit 1
fi

ln -s "$LIB_SRC" "$DEST"
echo "installed: $DEST -> $LIB_SRC"
echo "restart the Arduino IDE if it is running, then select Tools > Board > Arduino GIGA R1 WiFi"
