#!/usr/bin/env bash
# Copyright (c) 2026 controllercustom@myyahoo.com
# Distributed under the MIT license. See the LICENSE file for details.

# upload-swd.sh — flash the sketch over SWD with an STLINK-V3 (or ST-LINK/V2).
#
# Use this when the DFU path is unavailable: board stuck in the bootloader,
# a sketch that wedged the USB-C stack, or no CDC port to touch at 1200 bps.
#
# Wiring: STLINK-V3SET main module -> GIGA R1 WiFi J10 (10-pin MIPI/ARM10):
#   SWDIO, SWCLK, GND, NRST (+ VTREF as sense only — the probe does not power
#   the target, the board must be powered by its own USB). The ARM10 ribbon has
#   no VCP pins, so this path gives no console; the console is still USB-C.
#
# The app lives at 0x08040000: the first 256 KB of flash is the Mbed bootloader
# and must not be touched.
#
# Pinned to the xPack OpenOCD that ships with the ST core, because
# interface/stlink.cfg is the native st-link driver there and the legacy hla
# driver in the system build. -s keeps the configs from the same tree as the
# binary.
#
# Usage:
#   scripts/upload-swd.sh                 # build/USB_Serial_Term/USB_Serial_Term.ino.bin
#   scripts/upload-swd.sh path/to/app.bin
#   OPENOCD=/opt/openocd scripts/upload-swd.sh
#   ADAPTER_SPEED=1000 scripts/upload-swd.sh    # long leads / slow target

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

APP_BASE="0x08040000"
ADAPTER_SPEED="${ADAPTER_SPEED:-2000}"
OOC_BASE="${OPENOCD:-$HOME/.arduino15/packages/STMicroelectronics/tools/xpack-openocd/0.12.0-6}"
OOC="$OOC_BASE/bin/openocd"
OOC_SCRIPTS="$OOC_BASE/openocd/scripts"

BIN="${1:-$PROJ_ROOT/build/USB_Serial_Term/USB_Serial_Term.ino.bin}"

if [[ ! -x "$OOC" ]]; then
  echo "upload-swd.sh: no openocd at $OOC" >&2
  echo "  set OPENOCD to an openocd prefix containing bin/openocd" >&2
  exit 1
fi
if [[ ! -f "$BIN" ]]; then
  echo "upload-swd.sh: no such binary: $BIN" >&2
  echo "  run scripts/build.sh first" >&2
  exit 1
fi
if [[ ! -d "$OOC_SCRIPTS" ]]; then
  OOC_SCRIPTS="/usr/share/openocd/scripts"
  echo "upload-swd.sh: falling back to $OOC_SCRIPTS (legacy hla driver)" >&2
fi

echo "flashing $(basename "$BIN") -> $APP_BASE over SWD at ${ADAPTER_SPEED} kHz"

"$OOC" \
  -s "$OOC_SCRIPTS" \
  -f interface/stlink.cfg \
  -f target/stm32h7x.cfg \
  -c "adapter speed $ADAPTER_SPEED" \
  -c "init" \
  -c "reset halt" \
  -c "flash write_image erase $BIN $APP_BASE" \
  -c "verify_image $BIN $APP_BASE" \
  -c "reset run" \
  -c "shutdown"

echo "flashed $BIN at $APP_BASE — console is the USB-C port"
