#!/usr/bin/env python3
# Copyright (c) 2026 controllercustom@myyahoo.com
# Distributed under the MIT license. See the LICENSE file for details.

"""Decode a Cynthion wire-level capture into the CDC packet sequence.

The capture is raw TLPs (usbll), and this build's dissector leaves usbll.data
empty -- the same quirk as usbmon's usb.capdata -- so payloads are parsed from
the frame bytes: PID(1) + header(2) + payload + CRC16(2). Direction comes from
tshark's usbll.src/dst, which does populate, merged by frame number.

    scripts/cynthion-seq.py /tmp/opencode/cdc-64-a.pcap

Prints, in wire order, every non-polling TLP: the DATA packets on the OUT
endpoint (host -> device) and the IN endpoint (device -> host), with payload
length and a short digest, plus ZLPs and TOCs. The polling storm (TIN/NAK and
the 0xa5 SYNC keepalive) is skipped because it is >99% of the frames.
"""

import struct
import subprocess
import sys


def frames(path):
    with open(path, "rb") as f:
        head = f.read(24)
        if head[:4] not in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1",
                            b"\xa1\xb2\xc4\xd3", b"\xa1\xb2\x3c\x4d"):
            sys.exit(f"not a recognised pcap magic (got {head[:4].hex()})")
        while True:
            rec = f.read(16)
            if len(rec) < 16:
                return
            _ts_s, _ts_ns, caplen, _wire = struct.unpack("<IIII", rec)
            data = f.read(caplen)
            if len(data) < caplen:
                return
            yield data


def main():
    path = sys.argv[1]
    skip = {0x69, 0x5a, 0xA5}  # TIN, NAK, SYNC keepalive

    raw = []
    for data in frames(path):
        pid = data[0]
        if pid in skip:
            continue
        # A 67-byte frame is PID(1) + payload(64) + CRC16(2): the analyzer
        # omits the 2-byte endpoint header for DATA TLPs, which is why the
        # dissector's own "Data" field is shifted by 2.
        payload = data[1:-2] if len(data) >= 3 else b""
        raw.append((pid, payload))

    # Direction from the dissector, in the same order as the filtered frames.
    out = subprocess.run(
        ["tshark", "-r", path,
         "-Y", "not (usbll.pid==0x69 or usbll.pid==0x5a or usbll.pid==0xa5)",
         "-T", "fields", "-e", "frame.number", "-e", "usbll.dst", "-e", "usbll.src"],
        capture_output=True, text=True)
    meta = [line.split("\t") for line in out.stdout.splitlines() if line.strip()]

    if len(meta) != len(raw):
        print(f"WARNING: {len(meta)} dissected vs {len(raw)} parsed -- merge may be off")

    print(f"{'frame':>8}  {'pid':>5}  {'src':>6} -> {'dst':<6}  "
          f"{'payload':>7}  digest")
    for (pid, payload), m in zip(raw, meta):
        num, dst, src = m[0], m[1] if len(m) > 1 else "?", m[2] if len(m) > 2 else "?"
        ep = "OUT" if dst.split(".")[-1] == "2" or src.split(".")[-1] == "2" else "IN"
        print(f"{num:>8}  {pid:#04x}  {src:>6} -> {dst:<6}  {len(payload):>7}  "
              f"{payload[:8].hex() if payload else '- ZLP -'}")


main()
