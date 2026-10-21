#!/usr/bin/env bash
# Copyright (c) 2026 controllercustom@myyahoo.com
# Distributed under the MIT license. See the LICENSE file for details.

# monitor.sh — serial monitor on the GIGA's USB-C port.
#
# This is the terminal half of the bridge: bytes typed here go to the serial
# device on USB-A, bytes from that device appear here.
#
# The settings are pinned to 115200 8N1 so the terminal matches both ends of
# the bridge. Opening the port also sends SET_LINE_CODING to the GIGA's own
# CDC, which is what changes its advertised line coding away from the 9600 8N1
# the Mbed core boots with.
#
# Quit before uploading (upload.sh needs the port for the 1200 bps touch):
# Ctrl-C with arduino-cli monitor.
#
# Usage:
#   scripts/monitor.sh [port] [extra arduino-cli monitor args...]
#
# Examples:
#   scripts/monitor.sh
#   scripts/monitor.sh /dev/ttyACM1
#   scripts/monitor.sh /dev/ttyACM0 --raw      # unbuffered passthrough

set -euo pipefail

PORT="${1:-${PORT:-/dev/ttyACM0}}"
shift || true

exec arduino-cli monitor -p "$PORT" \
  --config baudrate=115200,bits=8,parity=none,stop_bits=1 \
  "$@"
