#!/usr/bin/env python3
# Copyright (c) 2026 controllercustom@myyahoo.com
# Distributed under the MIT license. See the LICENSE file for details.

"""Mark the capture window on this bench for the inline Cynthion analyzer.

The analyzer records on its own computer; this script's only job is to make the
window boundaries unambiguous and timed, so the operator can arm and stop in
sync. Run it on the GIGA terminal:

    scripts/cynthion-window.py /dev/ttyACM1 192     # 3 packets: the −64 case
    scripts/cynthion-window.py /dev/ttyACM1 256     # 4 packets: the exact case

Window rules, and why each boundary is where it is:

- ARM BEFORE the trigger. The skill's own tip is "start capture, THEN plug in or
  reset", because an analyzer that is not armed early can miss the first frames.
  Arming late loses exactly the enumeration and the first packets, which is the
  part that answers the −64 question.
- The analyzer re-initialises the USB link when a capture starts (skill:
  "inline Cynthion analyzers re-initialize the USB link when a capture starts").
  On this bench that reads as an unmount, so the sketch's recoverHostPort() runs
  and costs its 1 s settle plus the debounce. The countdown below is sized for
  that; if the mount line has not appeared by the trigger, do not trigger.
- STOP after the release byte, never mid-transfer. A held tail is released by
  the NEXT write, so a window that ends before the release byte shows a stall
  with no explanation, and a window cut mid-URB makes the tail ambiguous.
- ONE payload per capture. The Cynthion can bundle several transfers into one
  frame, so back-to-back payloads make the packet stream unreadable.
"""

import os
import random
import select
import sys
import termios
import time
import tty

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM1"
COUNT = int(sys.argv[2]) if len(sys.argv) > 2 else 192
ARM_S = 12.0     # time to start the capture on the other computer
TAIL_S = 3.0     # wait with the tail held (nothing should arrive)
STOP_S = 6.0     # margin after the last byte before stopping

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


print(f"payload {COUNT} bytes = {COUNT // 64} whole packets "
      f"({'ODD -> expect 64 held' if (COUNT // 64) % 2 else 'EVEN -> expect exact'})")
print(f"\n>>> ARM THE CYNTHION CAPTURE NOW -- {ARM_S:.0f} s to arm\n")
time.sleep(ARM_S)

blob = random.Random(int(time.time())).randbytes(COUNT)
t0 = time.time()
written = 0
while written < COUNT:
    written += os.write(fd, blob[written:written + 512])
got = read(TAIL_S)
elapsed = time.time() - t0

print(f"payload written; {len(got)} of {COUNT} back in {elapsed:.2f} s")

release = b"\xee"
os.write(fd, release)
more = read(TAIL_S)
held = max(COUNT - len(got), 0)
print(f"release byte written; {len(more)} more back "
      f"(want {held + 1}: the held bytes plus the echoed 0xee)")

print(f"\n>>> STOP THE CAPTURE NOW -- {STOP_S:.0f} s of margin remain\n")
time.sleep(STOP_S)

os.close(fd)
