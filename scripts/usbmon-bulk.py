#!/usr/bin/env python3
# Copyright (c) 2026 controllercustom@myyahoo.com
# Distributed under the MIT license. See the LICENSE file for details.

"""Decode CDC BULK endpoint traffic from a usbmon capture.

The bundled pcap-analysis decoder filters to interrupt URBs (transfer type 1),
so it reports zero for a CDC-ACM device, whose data rides bulk endpoints (type
2). This one keeps bulk and prints the per-URB packet sizes, which is what the
-64 investigation needs: the device's IN packet sequence at an MPS boundary,
and whether a zero-length packet closes a whole-number-of-packets transfer.

    scripts/usbmon-bulk.py /tmp/opencode/pico-leg.pcap --dev 72 --in 0x83

usbmon frame layout, read off this bench's capture (the pcap-analysis skill's
offsets are wrong for this kernel: transfer type 2 is CTRL, not bulk, and the
device number is one byte, not four):
  [8]      'S' submit / 'C' complete
  [9]      transfer type: 0=SHORT 1=LONG 2=CTRL 3=RAW(bulk)
  [10]     endpoint: 0x80|dir | number, so IN 0x83 / OUT 0x02 / control 0x80
  [11]     device number, [12] bus number
  [16:24]  timestamp copy, [24:28] usec, [28:32] status (-115 = pending)
  payload starts at offset 64; its length is caplen - 64, which is the only
  reliable length field here (the in-header lengths are request-relative)
"""

import struct
import sys


def frames(path):
    with open(path, "rb") as f:
        head = f.read(24)
        if head[:4] == b"\xd4\xc3\xb2\xa1":
            endian = "<"
        elif head[:4] == b"\xa1\xb2\xc4\xd3":
            endian = ">"
        else:
            sys.exit(f"not a legacy pcap (got {head[:4].hex()}); "
                     "convert pcapng first with tshark -F pcap")
        while True:
            rec = f.read(16)
            if len(rec) < 16:
                return
            sec, usec, caplen, _wire = struct.unpack(endian + "IIII", rec)
            data = f.read(caplen)
            if len(data) < caplen:
                return
            yield sec, usec, data


def main():
    args = sys.argv[1:]
    path = args[0]
    want_dev = None
    want_ep = None
    kinds = {"S", "C"}

    i = 1
    while i < len(args):
        if args[i] == "--dev":
            want_dev = int(args[i + 1])
            i += 2
        elif args[i] in ("--in", "--out", "--ep"):
            want_ep = int(args[i + 1], 0)
            i += 2
        else:
            i += 1

    t0 = None
    counts = {}
    for sec, usec, data in frames(path):
        if len(data) < 64:
            continue
        ts = (sec, usec)
        if t0 is None:
            t0 = ts
        rel = (sec - t0[0]) + (usec - t0[1]) / 1e6

        kind = chr(data[8])
        xfer = data[9]
        ep = data[10]
        dev = data[11]
        bus = data[12]
        status = struct.unpack("<i", data[28:32])[0]

        if xfer != 3:  # RAW == bulk on this kernel
            continue
        if want_dev is not None and dev != want_dev:
            continue
        if want_ep is not None and ep != want_ep:
            continue
        if kind not in kinds:
            continue

        payload = data[64:]
        counts[ep] = counts.get(ep, 0) + 1
        flag = "" if status == 0 else f" status={status}"
        print(f"t={rel:9.6f}s  {kind} ep=0x{ep:02x} dev={dev} bus={bus} "
              f"payload={len(payload)}{flag}  "
              f"{payload.hex(' ') if payload else '-- (no data) --'}")

    print(f"total bulk URBs: {sum(counts.values())} "
          f"({', '.join(f'ep 0x{e:02x}: {n}' for e, n in sorted(counts.items()))})")


main()
