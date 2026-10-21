# TinyUSB_Extras — USB_Serial_Term

A serial terminal bridge for the **Arduino GIGA R1 WiFi**: bytes from a serial
device plugged into the **USB-A host** port appear on the **USB-C** serial port,
and bytes typed into the USB-C terminal are sent to that device.

```
you / PC  <-- USB-C, 115200 8N1 -->  GIGA R1 WiFi  <-- USB-A host -->  serial device
                                      (Serial)                        CDC | FTDI | PL2303
                                                                      CP210x | CH34x
```

Both ends are 115200 bits/sec, 8 data bits, no parity, 1 stop bit.

Built on [TinyUSB_Arduino](https://github.com/controllercustom/TinyUSB_Arduino),
which runs the TinyUSB host stack on the GIGA's second USB controller. The
GIGA's USB-C device port is left exactly as the Mbed core configures it, so
`Serial` keeps working as the terminal while TinyUSB owns the USB-A port.

## Install (Arduino IDE 2.x)

1. **Install the library.** Arduino IDE 2.x resolves `#include <ArduinoTinyUSB.h>`
   from the sketchbook only, so `TinyUSB_Arduino` has to live there. Use
   **Sketch → Include Library → Add .zip Library…** with a ZIP of the
   `TinyUSB_Arduino` repository, then restart the IDE.

2. **Install the board support.** *Tools → Board → Boards Manager*, search
   **GIGA**, install **Arduino Mbed OS GIGA Boards** (tested with 4.6.0).

3. **Open the sketch.** *File → Open…* `USB_Serial_Term/USB_Serial_Term.ino`.

4. **Select** *Tools → Board → Arduino GIGA R1 WiFi* and the port the board
   enumerates on (`/dev/ttyACM0`, `COM7`, …).

5. **Verify / Upload.** No Tools-menu setting, no build flag, and no config
   file to copy: the sketch builds against the library's default configuration,
   which already compiles in CDC + FTDI + PL2303 + CP210x + CH34X host drivers.

There is nothing to configure in the sketch. Flash it, open the Serial Monitor,
then plug a supported serial device into the USB-A port.

## Wiring

| From | To | Cable / port |
|---|---|---|
| PC | GIGA R1 WiFi | USB-C (J12) — the terminal, shows up as `/dev/ttyACM0` |
| Serial device | GIGA R1 WiFi | USB-A (J2) — host port, VBUS switched on by the sketch |

The sketch powers the USB-A port (`PA_15` HIGH, one second to settle) before
starting the host stack. A bus-powered adapter is fine; a device that wants
more than the port can source needs its own supply or a powered hub.

## Supported host serial types

All five are bound by one driver — TinyUSB's CDC host class — and the sketch
treats them identically. The type is reported at mount.

| Type | Bound by | Notes |
|---|---|---|
| **CDC** (CDC-ACM) | interface class `0x02`, subclass `0x02` | any vendor VID/PID, real CDC devices |
| **FTDI** | VID `0x0403` + PID in the FTDI list | FT232R / FT232RL / FT-X; the 2-byte FTDI modem-status prefix is stripped by the driver |
| **PL2303** | VID `0x067B` + PID in the PL2303 list | 2303 / TB / GC / GB / GT / GL / GE / GS |
| **CP210x** | VID `0x10C4` + PID `0xEA60` / `0xEA61` / `0xEA70` | device firmware must support `SET_BAUDRATE` (V4 and later) |
| **CH34x** | VID `0x1A86` / `0x4348` / `0x2184` / `0x9986` | CH340 / CH341 / CH330 |

A vendor chip whose VID:P**ID** was reprogrammed in EEPROM is not in the list,
so it will not bind; a reprogrammed FTDI or CP210x needs the list extended in
the library config.

## What you see

```
### TinyUSB GIGA serial terminal ###
USB-A host serial <-> USB-C Serial, 115200 8N1
Types: CDC, FTDI, PL2303, CP210x, CH34x
[term] host stack up, waiting for a device on USB-A
[term] FTDI mounted idx=0 vid=0403 pid=6001
[term] ready FTDI idx=0
```

Verified on hardware with an **FTDI FT232R** (`0403:6001`), a **CP2104**
(`10c4:ea60`), a **PL2303** (`067b:2303`) and a **Pico 2** (`239a:cafd`,
CDC-ACM): ASCII plus a binary burst containing `00 01 02 ff fe` went in and came
back byte-for-byte through the adapter's jumpered TX→RX (46/46, 45/45 and
43/43 bytes on three runs, plus 41/41 on the PL2303 with `11 13 18` included). A round-trip soak pushes
100,000 pseudorandom bytes through and gets 100,000 back identical, all 256
byte values, at 11.3–11.4 kB/s — the 115200 8N1 wire rate — on the CP2104, the
PL2303, the FTDI and a CP2102 (200,000 bytes on both the FTDI and the CP2102,
11.2 KiB/s). Hot-plug is verified on the CP2104 and on an FTDI: unplug, wait,
replug and the port re-initialises itself and reports `ready` again without a
board reset, with the device table identical to a cold mount.

After `ready`, everything is transparent in both directions — including
binary data. Unplug the device and `unmounted` is printed; replugging it
re-runs the configuration.

The line the device is configured to is forced on mount: the driver applies
115200 8N1 during enumeration, and the sketch re-applies it through a throwaway
rate so that the PL2303 — which refuses a line-coding request identical to the
one it already believes it has — is guaranteed to have actually been told. DTR
and RTS are asserted so a device that gates its transmitter on DTR starts
sending.

## Limits and gotchas

- **One serial device at a time.** The library config compiles a single CDC
  host instance (`CFG_TUH_CDC 1`), so a second adapter behind a hub will not
  bind.
- **The bridge holds itself down if the line-coding request fails.** A device
  running at an unknown baud rate returns plausible garbage; a dead bridge does
  not. Replug the device to retry.
- **Unplugging costs about a second before the next device is accepted.** A
  reconnect does not resume by itself — the port is re-initialised, which takes
  the host stack's one-second VBUS settle. Expect `re-initialising USB-A host
  port for hot-plug` followed by `host stack up…` on the terminal, then
  `mounted` / `ready` when you plug the next one in. No board reset is needed.
- **A CDC-ACM device that advertises no line-request capability never mounts.**
  Enumeration applies 115200 8N1 as part of mounting, and a device that refuses
  that request fails the mount. Most CDC devices (Arduino, RP2040/RP2350 native
  USB, FTDI in CDC mode) do support it.
- **PL2303 with XON/XOFF flow control enabled in EEPROM swallows characters**
  (`x`, `q`, `X`, `Q`). TinyUSB deliberately does not clear that setting; use
  the Prolific EEPROM writer to turn flow control off. The adapter on this bench
  already has it off, so `11 13 18` and a full 256-value soak came back whole.
- **FTDI FT232A is not supported** by the driver (`bcdDevice 0x200`), and a chip
  whose type cannot be determined from `bcdDevice` does not mount.
- **Throughput is about 11 KB/s per direction** for the UART-bridge chips, which
  is the 115200 8N1 wire limit (99% of 11,520 bytes/s). A CDC-ACM device has no
  such limit: measured with a Pico 2 on the host port, 100 KB round-tripped in
  0.27 s and 200 KB in 0.53 s — about 365 KiB/s, roughly 32 times the UART
  chips.
- **A device that buffers its own output can hold the last packet — this is a
  zero-length-packet (ZLP) condition, and it is the device that omits it.** A
  USB bulk transfer whose length is a whole number of packets cannot be told
  apart from "more data coming" unless the sender ends it with a short or
  empty packet. The GIGA does send one; the Pico 2's Arduino core did not, so
  its 200,000-byte payload (3125 whole packets) came back exactly 64 bytes
  short and uncorrupted, and one more byte released all 65. Nothing is lost
  and a live terminal always has more traffic, but do not expect the final
  packet of a closed stream.
- **The mirror image happens too: a whole number of packets can gain one byte.**
  With the CP2104, payloads of 64, 128 and 192 bytes came back complete plus
  exactly one extra byte, while 63, 65, 130 and 193 came back exact. The extra
  differs each run, so it is not a marker you can strip. The bridge is
  byte-transparent — nothing is corrupted or reordered — but a consumer that
  counts bytes can see one more than it sent.
  This is the device, not the bridge: an FTDI at those same sizes, and a
  200,000-byte run through it, came back exact, and so did a CP2102. End-of-
  stream behaviour varies by chip, not by family: measured so far, both FTDI
  variants and the CP2102 exact, the CP2104 one byte extra, the Pico 2 64
  bytes held back. The CP2102 and the CP2104 report the same VID and product
  ID, so the difference is the chip's firmware and is not visible in anything
  the sketch can read.
- **The GIGA's own USB-C line coding stays at its default** 9600 8N1 until a
  host asks otherwise. `Serial.begin(115200)` cannot change it (on the CDC
  transport the call is a no-op), and on a CDC link the baud value is advisory
  framing information, not a wire rate. Opening the port with
  a 115200 8N1 monitor sends a line-coding request, which sets it to 115200.
- **The library's own `[TUH]` start-up chatter goes to `Serial1`** (D0 / D1),
  not to the USB-C terminal. Wire a UART bridge there if you want the host
  stack's init sequence; the sketch's own `[term]` lines are all on USB-C.
- **Four of the five host types have been run on real hardware** — FTDI,
  CP210x, PL2303 and CDC-ACM (a Pico 2). Only CH34x is left unobserved: no
  CH340-class adapter was available on this bench, so its behaviour is read off
  the code and none of it is measured. A CDC-ACM device reports the type as
  `CDC` rather than a vendor name, since its VID is not one of the four vendor
  lists.

## Maintainer commands (arduino-cli)

```bash
scripts/install-library.sh      # symlink TinyUSB_Arduino into the sketchbook
scripts/build.sh                # compile -> build/USB_Serial_Term/
scripts/upload.sh /dev/ttyACM0  # compile + DFU upload (needs a running app to touch)
scripts/upload-swd.sh           # flash over an ST-Link / STLINK-V3 on J10 (SWD)
scripts/monitor.sh              # 115200 8N1 monitor on the USB-C port
scripts/soak.py                 # 100 KB round-trip integrity test (adapter TX-RX shorted)
```

`scripts/build.sh` passes `--library` explicitly so it also works without the
sketchbook install. It uses the same FQBN, the same default configuration and
no extra flags, and produces the same image as the IDE:

```
Sketch uses  164288 bytes (8%) of program storage space. Maximum is 1966080.
Global variables use 70176 bytes (13%) of dynamic memory.  Maximum is 523624.
```

**Flashing when the app will not cooperate.** A running app is `2341:0266` and
`upload.sh` does the 1200 bps touch against it. If the board is already in the
Mbed bootloader it is `2341:0366` (CDC + DFU runtime) and the touch is wrong —
flash directly instead:

```bash
dfu-util --device 0x2341:0x0366 -D build/USB_Serial_Term/USB_Serial_Term.ino.bin \
         -a0 --dfuse-address=0x08040000:leave
```

If the firmware itself is wedged, `scripts/upload-swd.sh` writes the same
`.bin` at `0x08040000` over SWD and leaves the Mbed bootloader in the first
256 KB alone. `AGENTS.md` has the register-level recipe for telling a dead
firmware from an idle one.

`setup()` begins `Serial1` even though the terminal is `Serial`: the library
prints its host-stack bring-up steps to `Serial1` on this board, and writing to
an un-`begin()`ed one hard-faults the core. Keep that line.

`sketch.yaml` records the pinned core and library versions. It is not read by
the IDE, and `arduino-cli` cannot combine `--profile` with `--library`, so the
scripts use `--fqbn` / `--library` directly.

## Licence

MIT. Uses TinyUSB (MIT, Ha Thach) via TinyUSB_Arduino.
