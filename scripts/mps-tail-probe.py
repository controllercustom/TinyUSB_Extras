#!/usr/bin/env python3
# Copyright (c) 2026 controllercustom@myyahoo.com
# Distributed under the MIT license. See the LICENSE file for details.

"""MPS-boundary tail probe: does a whole number of 64-byte packets come back
whole, and if not, is the tail held or lost?

    scripts/mps-tail-probe.py /dev/ttyACM1

The plain sweep cannot answer this on the GIGA leg: a tail that the host never
received is released by the NEXT write, so it lands in the following size's
read and shows up as "extra bytes" or a mismatch at index 0. This probe closes
each size before moving on — it writes one release byte and drains everything
— so every verdict is about that size alone.

For each N it classifies:
  exact        all N bytes arrived on their own
  held tail    N-k missing, and they arrive once one more byte is written
  lost         N-k missing and still absent after the release byte
  whole +k     all N arrived plus k bytes the device invented — measured on the
               CP2104, whose firmware answers the host-TX ZLP with one stale
               byte; a byte-counting consumer sees COUNT+k
"""

import os
import random
import select
import sys
import termios
import time
import tty

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM1"
# Sizes may be given on the command line; the default is the one-packet-per-
# step boundary sweep, which is where the parity rule shows up.
SIZES = [int(x) for x in sys.argv[2:]] or [64, 128, 192, 256, 320, 384, 448, 512]
READ_S = 3.0

seed = int(time.time() * 1000) & 0x7FFFFFFF

fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY)
tty.setraw(fd)


def drain(limit_s=1.0):
    """Throw away whatever is pending so the next verdict is about one size."""
    termios.tcflush(fd, termios.TCIOFLUSH)
    t0 = time.time()
    while time.time() - t0 < limit_s:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            os.read(fd, 65536)
        else:
            return


def read(n, limit_s):
    rx = bytearray()
    t0 = time.time()
    while len(rx) < n and time.time() - t0 < limit_s:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            rx.extend(os.read(fd, 65536))
    return bytes(rx)


results = []

for n in SIZES:
    blob = random.Random(seed + n).randbytes(n)
    drain()

    t0 = time.time()
    written = 0
    while written < n:
        written += os.write(fd, blob[written:written + 4096])
    got = read(n, READ_S)
    own = len(got)

    if got == blob:
        verdict = "exact"
        missing = 0
    elif own > n and got[:n] == blob:
        # The payload is whole and longer than what was sent: the device
        # invented bytes. Measured on the CP2104, whose firmware answers the
        # host-TX ZLP (cdc_host.c:683-686) with one stale byte of its own.
        verdict = f"whole + {own - n} spurious"
        missing = 0
    else:
        # One more byte is what releases a held tail; the release byte echoes
        # too, so expect missing + 1 bytes back.
        release = bytes([0xEE])
        os.write(fd, release)
        more = read(n - own + 1, READ_S)
        tail = more[:len(more) - 1] if len(more) else b""
        if got + tail == blob:
            verdict = "held tail (released by the next byte)"
        else:
            verdict = "lost / not recoverable"
        missing = n - own
        got = got + tail

    rate = len(got) / max(time.time() - t0, 0.001)
    print(f"{n:6d} bytes ({n // 64} pkts) -> {own} on their own  [{verdict}]"
          + (f"  rate={rate / 1024:.0f} KiB/s" if verdict == "exact" else ""))
    results.append((n, verdict, missing))

os.close(fd)

held = [n for n, v, m in results if v != "exact"]
print(f"\nsummary: {len(results) - len(held)}/{len(results)} exact; "
      f"boundary sizes affected: {held}")
if held:
    print(f"missing counts: {sorted(set(m for _, v, m in results if v != 'exact'))}")
    sys.exit(1)
sys.exit(0)
