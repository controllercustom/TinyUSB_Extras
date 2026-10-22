#!/usr/bin/env python3
# Copyright (c) 2026 controllercustom@myyahoo.com
# Distributed under the MIT license. See the LICENSE file for details.

"""Exact-multiple-of-64 round-trip sweep for a CDC-ACM device plugged straight
into this PC, handled by the kernel cdc_acm.

    scripts/cdc64-sweep.py /dev/ttyACM3

This is the control that separates the GIGA's host-IN fault from a device
property: with the Pico 2 behind the GIGA's DWC2 host stack, a payload that is
a whole number of 64-byte endpoint packets came back exactly 64 short (the
de-alloc -> re-arm window NAKs one IN packet). The kernel cdc_acm read keeps
the IN endpoint armed continuously, so there is no such window, and every size
here must come back byte-exact.

Sizes are the MPS boundary plus a few large ones. Stay at or under 16 KB: the
Pico's CDC echo buffer cannot absorb a 64 KB burst at once, and the PC applies
write backpressure unlike the GIGA's paced host-TX FIFO, so a 64 KB write
blocks on the kernel write and says nothing about the tail.

Same raw-tty and unique-seed gating as soak.py: ECHO left on would double the
stream and look like a pass, and a fixed seed would let stale input match.
"""

import os
import random
import select
import sys
import termios
import time

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM3"
TIMEOUT_S = 10.0

# The boundary sweep, then two big sizes, then the boundary sizes again so a
# pass cannot be a first-connect fluke.
SIZES = [63, 64, 65, 128, 192, 256, 320, 384, 448, 512, 4096, 16384, 64, 128, 192]

seed = int(time.time() * 1000) & 0x7FFFFFFF

# Raw tty: no local echo, no CR/LF translation. With ECHO on, every byte would
# come back twice and a "byte-exact" verdict would be a lie.
fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY)

import tty

tty.setraw(fd)

failures = []

for n in SIZES:
    blob = random.Random(seed + n).randbytes(n)
    # Drop anything left in the tty from the previous size so the diff is
    # about this transfer only.
    termios.tcflush(fd, termios.TCIOFLUSH)

    t_start = time.time()
    written = 0
    while written < n:
        written += os.write(fd, blob[written:written + 4096])

    rx = bytearray()
    # select bounds the wait: os.read alone would block forever on a held tail,
    # which is exactly the condition this sweep exists to detect.
    while len(rx) < n and time.time() - t_start < TIMEOUT_S:
        ready, _, _ = select.select([fd], [], [], 0.5)
        if ready:
            rx.extend(os.read(fd, 65536))

    got = bytes(rx)
    elapsed = time.time() - t_start
    exact = got == blob
    print(f"{n:6d} bytes -> {len(got)} back in {elapsed:.3f} s  "
          f"{'exact' if exact else 'MISMATCH'}")

    if not exact:
        if len(got) > n and got[:n] == blob:
            # Payload intact with the spurious byte appended: the +1 case, where
            # every index matches and only the tail is extra.
            kind = f"{len(got) - n} extra bytes {got[n:].hex(' ')}"
        elif got == blob[:len(got)]:
            kind = (f"{n - len(got)} short (held tail)" if len(got) < n
                    else f"{len(got) - n} extra bytes {got[n:].hex(' ')}")
        else:
            diffs = [i for i in range(min(len(got), n)) if got[i] != blob[i]]
            kind = f"corrupted at index {diffs[0]} of {len(diffs)}"
        failures.append((n, kind))
        print(f"           !! {kind}")

os.close(fd)

if failures:
    print(f"RESULT: FAIL -- {len(failures)} of {len(SIZES)} sizes not byte-exact")
    for n, kind in failures:
        print(f"  {n}: {kind}")
    sys.exit(1)

print(f"RESULT: PASS -- all {len(SIZES)} transfers byte-exact, "
      "including every whole number of 64-byte packets")
sys.exit(0)
