#!/usr/bin/env python3
# Copyright (c) 2026 controllercustom@myyahoo.com
# Distributed under the MIT license. See the LICENSE file for details.

"""Write one payload and wait long, without ever writing a second byte.

Distinguishes a withheld transfer end (the device is waiting for a terminator
that never comes) from a packet that is merely late or lost: a held tail
released by the next write eventually arrives on its own if nothing else is
sent, a genuinely lost one never does.

    scripts/../hold-probe.py /dev/ttyACM0 [bytes] [wait_s]
"""

import os
import select
import sys
import termios
import time
import tty

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 64
WAIT = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0

import random

blob = random.Random(1234).randbytes(N)

fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY)
tty.setraw(fd)

written = 0
while written < N:
    written += os.write(fd, blob[written : written + 4096])

rx = bytearray()
t0 = time.time()
while time.time() - t0 < WAIT:
    ready, _, _ = select.select([fd], [], [], 0.5)
    if ready:
        rx.extend(os.read(fd, 65536))

termios.tcflush(fd, termios.TCIOFLUSH)
os.close(fd)

print(f"{N} bytes sent, nothing else sent for {WAIT:.0f} s -> {len(rx)} back")
if len(rx) < N:
    print(f"  {N - len(rx)} never arrived: {blob[len(rx):].hex(' ')[:120]}")
elif rx != blob:
    print(f"  {len(rx) - N} extra: {rx[N:].hex(' ')}")
else:
    print("  byte-exact")
