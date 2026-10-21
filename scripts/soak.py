#!/usr/bin/env python3
# Copyright (c) 2026 controllercustom@myyahoo.com
# Distributed under the MIT license. See the LICENSE file for details.

"""Round-trip integrity soak for USB_Serial_Term.

Sends a seeded pseudorandom blob into the GIGA's USB-C terminal and collects
what comes back through the host adapter's jumpered TX->RX. Requires the
adapter's TX shorted to its RX (see README).

    scripts/soak.py /dev/ttyACM1 100000

    scripts/soak.py /dev/ttyACM1 100000 150 0   # CDC-ACM device: skip the
                                                # 11520 B/s wire-rate check, which
                                                # only applies to UART-bridge chips

Two things this measures, and one it does not:

- Integrity: every byte must come back unchanged. Raw tty, no escaping, all
  256 byte values included, so anything but an exact match is a bug.
- Speed: reported as one number, from the first byte written to the last byte
  read. Expect roughly 11.5 kB/s, the 115200 8N1 wire rate: the two directions
  pipeline fully, so a self-loopback is limited by a single wire.
- NOT the host-side write rate. tty write() returns once the bytes are queued
  into the CDC driver's URBs, far ahead of the wire, so timing the write loop
  says nothing about the link. That number used to be printed here and it was
  13.6 KiB/s against an 11.25 KiB/s wire, which is the tell that it was wrong.

The reader therefore runs concurrently with the writer. A sequential test would
measure flow control instead of integrity.
"""

import os
import random
import sys
import termios
import threading
import time
import tty

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM1"
COUNT = int(sys.argv[2]) if len(sys.argv) > 2 else 100_000
TIMEOUT_S = float(sys.argv[3]) if len(sys.argv) > 3 else 120.0
# Expected wire rate for the rate sanity check. 115200 8N1 = 11520 B/s applies
# to the UART-bridge chips (FTDI, PL2303, CP210x, CH34x). A CDC-ACM device on a
# fast MCU has no such limit, so pass 0 to skip the check for those.
WIRE_BYTES_PER_S = float(sys.argv[4]) if len(sys.argv) > 4 else 11520.0

# Unique per run. A fixed seed would make this run's blob byte-identical to an
# earlier one, and any input left in the tty from that run would then match and
# pass for the wrong reason -- the same trap as the rate check above.
seed = int(time.time() * 1000) & 0x7FFFFFFF
blob = random.Random(seed).randbytes(COUNT)

fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY)
tty.setraw(fd)
# Drop anything left over from earlier runs so the diff is about this run only.
termios.tcflush(fd, termios.TCIOFLUSH)

rx = bytearray()
stop = threading.Event()
first_byte_at = None


def reader():
    global first_byte_at
    while not stop.is_set():
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            continue
        if first_byte_at is None:
            first_byte_at = time.time()
        rx.extend(chunk)
        if len(rx) >= COUNT:
            break


t = threading.Thread(target=reader, daemon=True)
t.start()

t_start = time.time()
written = 0

while written < COUNT:
    written += os.write(fd, blob[written:written + 4096])
    if len(rx) >= COUNT:
        break
queued_at = time.time() - t_start

while len(rx) < COUNT and time.time() - t_start < TIMEOUT_S:
    time.sleep(0.05)

stop.set()
t.join(timeout=1.0)

got = bytes(rx)
elapsed = time.time() - t_start
roundtrip = (len(got) / WIRE_BYTES_PER_S) if WIRE_BYTES_PER_S else 0.0
prefix_ok = got == blob[:len(got)]

print(f"port           : {PORT}")
print(f"seed           : {seed}  (unique per run; stale input cannot match)")
print(f"sent           : {written} bytes")
print(f"write() done   : {queued_at:.2f} s  (host queueing, not the wire -- ignore)")
rate_line = (f" ({len(got) / max(elapsed, 0.001) / 1024:.1f} KiB/s round trip)"
             if len(got) == COUNT else
             " (no rate: the tail never arrived, so the wait says nothing)")
print(f"received       : {len(got)} bytes in {elapsed:.2f} s{rate_line}")

if prefix_ok and len(got) == COUNT:
    print(f"result         : PASS -- byte-for-byte identical")
    print(f"byte values    : {len(set(got))} distinct of 256")
    # A real self-loopback cannot finish much sooner than the wire needs; local
    # echo or a port looped before the GIGA finishes in a small fraction of it.
    if WIRE_BYTES_PER_S and queued_at < 0.5 * roundtrip:
        print(f"WARNING        : all {written} bytes were written in {queued_at:.2f} s, "
              f"faster than the\n               {WIRE_BYTES_PER_S:.0f} B/s wire needs "
              f"({roundtrip:.2f} s). The data cannot all have\n"
              "               gone out and come back. Check that the adapter is really "
              "on the GIGA's\n               USB-A port, that ECHO is off, and that "
              "RX is shorted to TX.")
    sys.exit(0)

if prefix_ok and len(got) == 0:
    # Nothing at all, which is not the condition below. Measured with a CP2102:
    # every size returned 0 bytes while the device table still showed it mounted
    # and configured (flags 0x07, hostReady 1), so the table cannot tell a dead
    # loopback from a working bridge. Check the adapter's TX-to-RX short, or
    # reset the board if the GIGA has stopped forwarding.
    print("result         : NOTHING RETURNED -- no byte came back at all, so the loopback")
    print("                 path is dead: check the adapter's TX-to-RX short, or reset the")
    print("                 board if the GIGA has stopped forwarding. Not a held tail.")
    sys.exit(3)

if prefix_ok:
    # Byte-transparent but short. Measured on a Pico 2: 200,000 bytes came back
    # exactly 64 short -- one endpoint packet -- and writing one more byte released
    # all 65. The far device holds the last packet in its own output ring until the
    # next write, so this is the device's buffering, not a bridge fault.
    print(f"result         : INCOMPLETE -- byte-transparent, {COUNT - len(got)} bytes "
          "still held in the far device's own output ring")
    print("                 write one more byte and they arrive; a live terminal "
          "always has more traffic")
    sys.exit(2)

print("result         : FAIL")
if len(got) != COUNT:
    print(f"length         : expected {COUNT}, got {len(got)} "
          f"({'truncated' if len(got) < COUNT else 'extra bytes'})")
    if len(got) > COUNT and got[:COUNT] == blob:
        print(f"extra bytes    : {got[COUNT:].hex(' ')} -- the payload itself came back "
              "whole, so these arrived on their own")

limit = min(len(got), COUNT)
diffs = [i for i in range(limit) if got[i] != blob[i]]
print(f"first mismatch : index {diffs[0] if diffs else 'none in common prefix'}")
if diffs:
    i = diffs[0]
    print(f"  expected ...{blob[max(0, i - 8):i + 8].hex(' ')}")
    print(f"  received ...{got[max(0, i - 8):i + 8].hex(' ')}")
print(f"mismatch count : {len(diffs)} of {limit}")

missing = sorted(set(blob[:limit]) - set(got[:limit]))
if missing:
    print(f"missing values : {[hex(v) for v in missing]} "
          "(XON/XOFF in a PL2303 EEPROM eats 0x11 0x13 0x18)")
sys.exit(1)
