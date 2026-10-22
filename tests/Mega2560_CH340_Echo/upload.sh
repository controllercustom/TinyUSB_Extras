#!/usr/bin/env bash
# Copyright (c) 2026 controllercustom@myyahoo.com
# Distributed under the MIT license. See the LICENSE file for details.

# upload.sh — flash the echo sketch to the CH340-based Mega 2560 clone.
#
# The programmer is `wiring` (STK500 v2 over the CH340's serial channel), NOT
# `arduino` (STK500 v1). Measured on this bench: -c arduino -P /dev/ttyUSB1
# sent 0x30 and read back 0x00 on all ten attempts ("programmer is not
# responding"), while -c wiring answered immediately with
# "Device signature = 1E 98 01 (ATmega2560)". The clone's bootloader speaks
# STK500 v2, so the part is atmega2560 and no reset-button press is needed.
#
# -r (the hard-reset flow) is unusable here: this avrdude build prints
# "avrdude built without libserialport support". It is not needed.
#
# Never flash *.with_bootloader.hex — the plain .hex goes to flash.
#
# Usage:
#   tests/Mega2560_CH340_Echo/upload.sh              # resolve the port by identity
#   tests/Mega2560_CH340_Echo/upload.sh /dev/ttyUSB0 # explicit port
#
# The ttyUSB number is not stable across sessions, so with no argument the
# port is resolved from udev by the chip's identity (VID 1a86, PID 7523).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AVRDUDE="$HOME/.arduino15/packages/arduino/tools/avrdude/8.0.0-arduino1/bin/avrdude"
CONF="$HOME/.arduino15/packages/arduino/tools/avrdude/8.0.0-arduino1/etc/avrdude.conf"
HEX="$SCRIPT_DIR/build/Mega2560_CH340_Echo.ino.hex"

if [[ ! -f "$HEX" ]]; then
  echo "upload.sh: no such hex: $HEX (build the sketch first)" >&2
  exit 1
fi

PORT="${1:-}"
if [[ -z "$PORT" ]]; then
  for p in /dev/ttyUSB*; do
    [[ -c "$p" ]] || continue
    info="$(udevadm info --query=property --property=ID_VENDOR_ID,ID_MODEL_ID "$p" 2>/dev/null || true)"
    if grep -q 'ID_VENDOR_ID=1a86' <<<"$info" && grep -q 'ID_MODEL_ID=7523' <<<"$info"; then
      PORT="$p"
      break
    fi
  done
fi
if [[ -z "$PORT" ]]; then
  echo "upload.sh: no ttyUSB node reports VID 1a86 PID 7523" >&2
  exit 1
fi
echo "upload.sh: port $PORT (1a86:7523)"

"$AVRDUDE" -C "$CONF" -v -V -patmega2560 -cwiring -P"$PORT" -b115200 -D \
  "-Uflash:w:$HEX:i"
