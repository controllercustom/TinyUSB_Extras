#!/usr/bin/env python3
# Copyright (c) 2026 controllercustom@myyahoo.com
# Distributed under the MIT license. See the LICENSE file for details.

"""Hold one payload open and read both ends' own buffers over SWD inside it.

The capture can only show what reached an endpoint; this shows what each stack
did with it. Same window rules as scripts/cynthion-window.py (the analyzer must
be armed before the trigger, and its arming re-initialises the link, so the
mount has to re-appear inside the countdown), but the hold is long enough to
attach OpenOCD and read the stacks' state while the tail is still held. The
target is halted for the read and resumed afterwards, never reset, so the
mount survives the probe.

    scripts/fifo-hold-probe.py /dev/ttyACM0 192

What each read decides, measured on the CP2104 with a 192-byte payload:

- `cdch_data[0].stream.rx.ff` with wr_idx == rd_idx after the application has
  drained everything means the class handed every received byte to the sketch.
  It is NOT a sign of loss: a FIFO that received 128 and consumed 128 and one
  that received 192 and consumed 192 both read as empty. The class's delivery
  log (cdch_rx_log / cdch_rx_bytes, added to cdch_xfer_cb's IN branch as a
  TEMP instrumentation) is what closes that gap: one 192-byte payload gives
  `64 64 64 0` and cdch_rx_bytes accounts for every byte, so the class
  received and delivered all 192 through cdc_host.c:704.
- `cdch_epbuf.rx` holding the payload's last packet says nothing on its own:
  the staging buffer keeps the last packet after delivery too. It was never a
  discriminator.
- The terminal's own 64-byte TX buffer is NOT where the −64 sits, and holding
  the last packet there says nothing: staging buffers retain the last packet
  after delivery, same as cdch_epbuf.rx. The USB-C capture settles it — all
  192 bytes crossed GIGA→PC inside 17.3 ms, each IN DATA ACKed by the PC — so
  the terminal sent everything and the −64 is the PC's kernel cdc_acm URB
  (readsize = 2 * MPS, drivers/usb/class/cdc-acm.c:1388-1389). The live USBCDC
  is `_SerialUSB`, NOT `_UART_USB_` (a second, unused instance that reads all
  zero), which is why the earlier member reads came back empty. Offsets are
  build-specific: re-derive with gdb `ptype /o arduino::USBCDC` (gives
  _tx_in_progress +132, _tx_buffer +133, _tx_buf +200, _tx_size +204) and
  `info address _SerialUSB`.
"""

import os
import random
import select
import subprocess
import sys
import termios
import time
import tty

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
COUNT = int(sys.argv[2]) if len(sys.argv) > 2 else 192
ARM_S = 12.0
HOLD_S = 25.0
STOP_S = 6.0

ELF = "/home/me/TinyUSB_Extras/build/USB_Serial_Term/USB_Serial_Term.ino.elf"
XP = "/home/me/.arduino15/packages/STMicroelectronics/tools/xpack-openocd/0.12.0-6"
GDB = "/home/me/.arduino15/packages/STMicroelectronics/tools/xpack-arm-none-eabi-gcc/14.2.1-1.1/bin/arm-none-eabi-gdb"

# _SerialUSB plus the member offsets from `ptype /o arduino::USBCDC`, for the
# build in ELF. Re-derive both if the core or the sketch changes.
SERIAL_USB = 0x24007350
TX_IN_PROGRESS = SERIAL_USB + 132
TX_BUFFER = SERIAL_USB + 133
TX_BUF_PTR = SERIAL_USB + 200
TX_SIZE = SERIAL_USB + 204

fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY)
tty.setraw(fd)
termios.tcflush(fd, termios.TCIOFLUSH)


def read(limit_s):
    rx = bytearray()
    t0 = time.time()
    while time.time() - t0 < limit_s:
        ready, _, _ = select.select([fd], [], [], 0.3)
        if ready:
            rx.extend(os.read(fd, 65536))
        else:
            break
    return bytes(rx)


oc = subprocess.Popen(
    [
        f"{XP}/bin/openocd",
        "-s",
        f"{XP}/openocd/scripts",
        "-f",
        "interface/stlink.cfg",
        "-f",
        "target/stm32h7x.cfg",
        "-c",
        "adapter speed 2000",
        "-c",
        "gdb_port 3333",
        "-c",
        "init",
    ],
    stdout=open("/tmp/opencode/openocd.log", "w"),
    stderr=subprocess.STDOUT,
)

print(f"payload {COUNT} bytes = {COUNT // 64} whole packets "
      f"({'ODD -> expect 64 held' if (COUNT // 64) % 2 else 'EVEN -> expect exact'})")
blob = random.Random(int(time.time())).randbytes(COUNT)
for i in range(0, COUNT, 64):
    print(f"pkt {i // 64}: {blob[i:i + 64].hex(' ')}")
print(f"\n>>> ARM THE CYNTHION CAPTURE NOW -- {ARM_S:.0f} s to arm\n")
time.sleep(ARM_S)

t0 = time.time()
written = 0
while written < COUNT:
    written += os.write(fd, blob[written:written + 512])
got = read(2.0)
print(f"payload written; {len(got)} of {COUNT} back in {time.time() - t0:.2f} s")

print("\n--- SWD read, target halted then resumed, hold still open ---")
gdb = subprocess.run(
    [
        GDB,
        "-batch",
        "-ex",
        "target remote :3333",
        "-ex",
        "monitor halt",
        "-ex",
        "p cdch_data[0].stream.rx.ff",
        "-ex",
        f"p *(unsigned int*){TX_SIZE}",
        "-ex",
        f"x/1xb {TX_IN_PROGRESS}",
        "-ex",
        f"x/64xb {TX_BUFFER}",
        "-ex",
        f"x/1xw {TX_BUF_PTR}",
        "-ex",
        "monitor resume",
        ELF,
    ],
    capture_output=True,
    text=True,
    timeout=60,
)
print(gdb.stdout)
print(gdb.stderr)

held = HOLD_S - (time.time() - t0)
if held > 0:
    time.sleep(held)

extra = read(2.0)
if extra:
    print(f"still nothing else for {HOLD_S:.0f} s -> {len(extra)} more arrived during the hold")

release = b"\xee"
os.write(fd, release)
more = read(3.0)
missing = COUNT - len(got) - len(extra)
print(f"release byte written; {len(more)} more back (want {missing + 1})")

print(f"\n>>> STOP THE CAPTURE NOW -- {STOP_S:.0f} s of margin remain\n")
time.sleep(STOP_S)

oc.terminate()
os.close(fd)
