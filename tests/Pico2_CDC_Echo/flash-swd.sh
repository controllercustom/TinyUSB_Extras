#!/usr/bin/env bash
# Copyright (c) 2026 controllercustom@myyahoo.com
# Distributed under the MIT license. See the LICENSE file for details.

# flash-swd.sh — load Pico2_CDC_Echo onto a Pico 2 over SWD via the RPi Debug
# Probe. No BOOTSEL.
#
# REQUIRES: the Pico 2's USB-C plugged into the probe's TARGET port, and the
# probe's own USB-C in the PC. (If the Pico is in the PC directly, SWD cannot
# reach it — see the USB tree: the probe must be the parent of the Pico.)
#
# The Pico runs from RAM, so "flashing" = write the .elf sections to their LMA,
# then resume from the entry point. `continue` starts the echo loop on the Pico;
# gdb then blocks (the loop never returns), so `timeout` ends gdb while the Pico
# keeps running the new program.
set -euo pipefail

P=/home/me/pico-sdk-tinyusb/tools/openocd-rpi
ELF="${1:-build/Pico2_CDC_Echo.ino.elf}"
[ -f "$ELF" ] || { echo "no .elf at $ELF"; exit 1; }

"$P/bin/openocd" -s "$P/share/openocd/scripts" \
  -f interface/cmsis-dap.cfg -f target/rp2350.cfg \
  -c "adapter speed 2000" -c "init" &
OCD=$!
sleep 2

timeout 15 gdb-multiarch -batch \
  -ex "target remote :3333" \
  -ex "load" \
  -ex "continue" \
  "$ELF" 2>&1 | grep -vE "^\[New|Reading symbols|remote memory" || true

kill "$OCD" 2>/dev/null || true
