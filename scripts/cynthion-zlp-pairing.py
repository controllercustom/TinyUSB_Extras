#!/usr/bin/env python3
# Copyright (c) 2026 controllercustom@myyahoo.com
# Distributed under the MIT license. See the LICENSE file for details.

"""Pair the GIGA's zero-length OUT with the device's response to it.

scripts/cynthion-zlp-pairing.py /tmp/opencode/cp2104-a.pcap [...]

Direction comes from tshark's usbll.src/dst: host -> 1.N is an OUT transfer,
1.N -> host is an IN transfer, so no endpoint-number table is needed. Payloads
come from the frame bytes because this build's dissector leaves usbll.data
empty. Prints every DATA TLP in wire order with its full payload, then per
burst (split on a 0.15 s gap, which is far above the ~20 ms inside one burst
and far below the ~0.3 s between the payload and the release byte)
the OUT and IN byte counts, whether a zero-length OUT was sent, and the IN
bytes that have no OUT counterpart compared against the payload.
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
            ts_s, ts_ns, caplen, _wire = struct.unpack("<IIII", rec)
            data = f.read(caplen)
            if len(data) < caplen:
                return
            yield ts_s + ts_ns / 1e9, data


def main():
    skip = {0x69, 0x5A, 0xA5}
    for path in sys.argv[1:]:
        raw = []
        for ts, data in frames(path):
            pid = data[0]
            if pid in skip:
                continue
            payload = data[1:-2] if len(data) >= 3 else b""
            raw.append((ts, pid, payload))

        x = subprocess.run(
            ["tshark", "-r", path,
             "-Y", "not (usbll.pid==0x69 or usbll.pid==0x5a or usbll.pid==0xa5)",
             "-T", "fields", "-e", "frame.number", "-e", "usbll.src",
             "-e", "usbll.dst"],
            capture_output=True, text=True)
        meta = [line.split("\t") for line in x.stdout.splitlines() if line.strip()]
        if len(meta) != len(raw):
            print(f"WARNING: {len(meta)} dissected vs {len(raw)} parsed")

        rows = []
        for (ts, pid, payload), m in zip(raw, meta):
            num, src, dst = m[0], m[1], m[2]
            if pid == 0xD2:
                side = "ack"
            elif pid == 0xE1:
                side = "out-req"
            elif src == "host":
                side = "OUT"
            elif dst == "host":
                side = "IN"
            else:
                side = "?"
            rows.append((int(num), ts, pid, side, payload))

        t0 = rows[0][1] if rows else 0.0
        print(f"===== {path} =====")
        for (num, ts, pid, side, payload) in rows:
            print(f"  {num:>8}  {(ts - t0) * 1000:>8.1f} ms  {pid:#04x}  "
                  f"{side:<8} {len(payload):>4}  "
                  f"{payload.hex() if payload else '- ZLP -'}")

        bursts = []
        cur = []
        prev = None
        for row in rows:
            if prev is not None and row[1] - prev > 0.15:
                bursts.append(cur)
                cur = []
            cur.append(row)
            prev = row[1]
        if cur:
            bursts.append(cur)

        for i, b in enumerate(bursts):
            ob = [p for (_, _, _, s, p) in b if s == "OUT"]
            ib = [p for (_, _, _, s, p) in b if s == "IN"]
            ob_bytes = b"".join(ob)
            ib_bytes = b"".join(ib)
            extra = ib_bytes[len(ob_bytes):]
            print(f"  burst {i}: OUT {len(ob_bytes)} B in {len(ob)} pkts "
                  f"({sum(1 for p in ob if not p)} ZLP) | "
                  f"IN {len(ib_bytes)} B in {len(ib)} pkts "
                  f"({sum(1 for p in ib if not p)} ZLP)")
            if extra:
                print(f"    extra IN bytes: {extra.hex()}")
                print(f"    last OUT byte {ob_bytes[-1:].hex() or '-'}, "
                      f"first OUT byte {ob_bytes[:1].hex() or '-'}, "
                      f"last echoed byte {ib_bytes[max(len(ob_bytes)-1,0):len(ob_bytes)].hex() or '-'}")


main()
