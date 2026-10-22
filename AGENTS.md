# AGENTS.md — TinyUSB_Extras / USB_Serial_Term

Working notes for whoever changes this next. Every claim below is a file:line
in `~/TinyUSB_Arduino` (the library, used unmodified) or in the installed
`arduino:mbed_giga` 4.6.0 core, so it can be re-checked instead of trusted.

## What this project is

One sketch, `USB_Serial_Term`, that bridges the GIGA R1 WiFi's USB-C serial
port (`Serial`, the PC terminal) to a serial device enumerated on the USB-A
host port. Both ends 115200 8N1. Five host serial types are supported through
one driver: CDC-ACM, FTDI, PL2303, CP210x, CH34x.

## Build

Arduino IDE 2.x is the primary path, and it constrains two things:

1. **The library must be in the sketchbook.** The IDE has no equivalent of
   `--library`, so `#include <ArduinoTinyUSB.h>` only resolves if
   `TinyUSB_Arduino` sits in `<sketchbook>/libraries/`.
   `scripts/install-library.sh` symlinks `~/TinyUSB_Arduino` there (one copy of
   the library stays authoritative; same arrangement as
   `~/Arduino/libraries/ArduinoX360-tinyusb-g2`).
2. **No build flags, no config copying.** The IDE cannot pass `-D…`, so the
   sketch has to compile against the library's shipped default config. That is
   what it does: the union preset already enables everything this sketch needs
   (`src/config/tusb_config_union.h:147-151`), so nothing is ever copied over
   `src/tusb_config_arduinotinyusb.h`. Keep it that way — copying a config in
   changes every other project that builds against this library.

Measured, and identical for `arduino-cli --library` and `arduino-cli` with the
sketchbook symlink (the IDE path):

```
Sketch uses          164352 bytes (8%)  of 1966080
Global variables use  70176 bytes (13%) of 523624
```

`--warnings all` adds nothing from the sketch file — only pre-existing core
(`stm32h7xx_hal_rcc.h` volatile-to-void) and library (`printer_host.c:348`
unused variable) warnings.

`sketch.yaml` is a version record only; `arduino-cli` forbids `--library` with
`--profile`, so `scripts/build.sh` uses `--fqbn` + `--library` directly.

## Role split — do not disturb the USB-C port

| Port | Controller | Owner |
|---|---|---|
| USB-C (J12) | OTG_FS, rhport 0 | **Mbed core**, `Serial` = CDC, left at defaults |
| USB-A (J2) | OTG_HS, rhport 1 | TinyUSB host, `tuh_arduino_init()` / `tuh_arduino_poll()` |

**Never call `tud_arduino_init()` from this project.** It soft-disconnects
Mbed's CDC, reclaims the `OTG_FS` IRQ vector and resets the peripheral
(`src/ArduinoTinyUSB/bsp_giga.cpp:50-99`) — that is how the device examples
take over Type C, and it would take `Serial` away from the terminal.

`tuh_arduino_init()` (`src/ArduinoTinyUSB/bsp_giga.cpp:103`) does everything
the sketch needs for the host port: `PA_15` HIGH for VBUS with a 1 s settle,
PB_14/PB_15 to AF12, `USB1_OTG_HS` clock, `OTG_HS_IRQn` at priority 5, then
`tuh_rhport_init(1, HOST, FULL)`. It deliberately does not enable the ULPI
clock (no ULPI PHY on the GIGA; a clocked floating ULPI wrapper holds the
DWC2 core in reset).

`ARDUINO_TINYUSB_CONSOLE` is `Serial1` on GIGA (`src/ArduinoTinyUSB.h:75-78`),
which is why the library's `[TUH] step:` chatter shows up on D0/D1 and not on
the terminal. Do not "fix" that by `#define`-ing the macro in the sketch: the
macro is only consulted inside the library's own translation units, so a sketch
definition changes nothing there and only misleads the reader. If host-stack
init logging is wanted, wire a UART bridge to D0/D1.

**`Serial1.begin()` is mandatory, even with nothing wired to D0/D1.**
`tuh_arduino_init()` prints to `ARDUINO_TINYUSB_CONSOLE` as its first statement,
and `UART::write()` (`cores/arduino/Serial.cpp:218-239`) does
`while (!_serial->obj->writeable()) {}` with no null check — `_serial` is only
allocated by `UART::begin()`. Printing to an un-begun `Serial1` therefore
dereferences NULL: precise BusFault → `Fault_Handler` → `mbed_fault_handler` →
`mbed_error` → `mbed_die`, which spins forever. Observed on hardware exactly
this way: the banner appeared, then the board was dead in `mbed_die`, with
`USB1` still unclocked so `GSNPSID` read 0.

## How the five types bind

`cdch_open()` (`src/class/cdc/cdc_host.c:738`) is the single entry point:

- `bInterfaceClass == 0x02 && bInterfaceSubClass == 0x02` → ACM path. Protocol
  is not checked.
- otherwise `bInterfaceClass == 0xFF` → `tuh_vid_pid_get()`, then a linear
  search of the compiled VID:PID lists in the `serial_drivers[]` table
  (`src/class/cdc/cdc_host.c:204`), order FTDI → CP210x → CH34x → PL2303.
  **First exact match wins**, so a reprogrammed EEPROM PID does not bind and
  the lists in `src/tusb_option.h:832-898` are the authority on what "works".

Endpoint order is per chip and is the usual reason a clone fails to bind:

| chip | check | source |
|---|---|---|
| FTDI | `sub=FF proto=FF`, 2 endpoints, `wMaxPacketSize != 0` | `cdc_host.c:1208-1228` |
| CP210x | `sub=0 proto=0`, 2 endpoints | `cdc_host.c:1598` |
| CH34x | 3 endpoints, **bulk pair first**, interrupt last | `cdc_host.c:1770-1792` |
| PL2303 | 3 endpoints, **interrupt first**, bulk pair last | `cdc_host.c:2107-2131` |

RX/TX are assigned by endpoint direction, not descriptor order
(`open_ep_stream_pair`, `cdc_host.c:720-728`). No IAD is required by anything
here — `usbh.c` skips IAD descriptors entirely and `acm_open` accepts a data
interface "if any".

The sketch reports the type from `tuh_cdc_itf_get_info()`
(`src/class/cdc/cdc_host.h:51`) → `daddr` → `tuh_vid_pid_get()`
(`src/host/usbh.h:198`) and maps VID → name. Note `tuh_cdc_itf_get_info()`
always reports `bInterfaceClass = TUSB_CLASS_CDC` (`cdc_host.c:426`), so the
interface descriptor is useless for telling the types apart; VID is the only
signal available to an application. The map's default is `CDC`, which is a
deduction rather than a guess: a vendor-specific device whose VID matches no
list never mounts, so anything mounted with an unlisted VID necessarily came
through the ACM path. Measured — a Pico 2 prints
`[term] CDC mounted idx=0 vid=239a pid=cafd` and `ready CDC`.

One asymmetry to keep straight: the **name** comes from the VID alone, while
the **driver** binds on VID:PID pairs — `tusb_option.h:833-849` lists 16 FTDI
PIDs, `:859-861` three CP210x, `:868+` the CH34x set. An FTDI whose PID is not
listed would print `FTDI` yet never get the FTDI driver. Measured agreement:
`0403:6001` and `0403:6015` are both in the FTDI list, so name and driver
match for both.

## Line coding — why the sketch bounces through 1200 baud

- The GIGA config forces 115200 8N1 at enumeration:
  `CFG_TUH_CDC_LINE_CODING_ON_ENUM` (`src/config/tusb_config_common.h:142`).
  `set_line_state_on_enum()` (`cdc_host.c:825-871`) sends it for ACM, FTDI,
  CP210x and PL2303, and **skips CH34x** because the CH34x backend already
  programmed the chip during `SERIAL_INIT` (`cdc_host.c:840`).
- Sending that request also caches it: `p_cdc->line.coding` is updated on
  success, and the pool default is 8N1 `{9600,0,0,8}` — a local deviation from
  upstream (`cdc_host.c:369-371`, listed in `src/VERSION`), needed because a
  zero `data_bits` is rejected by the PL2303 backend.
- `pl2303_set_line_coding()` **returns false when the requested coding equals
  the cached one** (`cdc_host.c:2043-2054`, "the caller has to precheck").
  Since the enum-time request leaves the cache saying 115200 8N1, a direct
  request for 115200 8N1 would be a no-op that never reaches a chip still at
  its power-on 9600. So `configureHostSerial()` programs 1200 first (a rate no
  cache can already hold), then 115200. Same trick the `~/Giga-USBH-tinyusb`
  `HostTinyUSB_PL2303` example uses.
- `tuh_cdc_set_line_coding()` is the only generic entry point. ACM and PL2303
  implement `set_line_coding` directly; FTDI, CP210x and CH34x have `NULL`
  there and fall through a two-stage baud → data-format sequence
  (`cdc_host.c:592-622`). The comment at `cdc_host.h:156` claiming FTDI and
  CP210x are unsupported is stale — all five work.
- `user_data` is threaded through to the completion callback
  (`cdch_internal_control_complete`, `cdc_host.c:882-894`; stage
  callbacks `cdc_host.c:896-924`), which is how the sketch carries the CDC
  `idx` through the chain instead of trusting a global.
- The `cdc_line_coding_t` passed to `tuh_cdc_set_line_coding()` must outlive
  the transfer: for ACM its pointer goes straight into the control transfer.
  Hence `hostCoding` is a file-scope static, not a local.
- **A failed line-coding request holds the bridge down** rather than continuing
  — a silent baud mismatch returns plausible garbage. That is a deliberate
  house rule (`~/Giga-USBH-tinyusb/AGENTS.md`: every mount-time chain checks
  `xfer->result` and stops).
- DTR/RTS are asserted by the sketch because nobody else does:
  `CFG_TUH_CDC_LINE_CONTROL_ON_ENUM` is not defined anywhere in the library, so
  the enum state machine never touches control lines. Refusal is tolerated (a
  CDC device with `bmCapabilities == 0` fails `acm_set_control_line_state`,
  `cdc_host.c:952`).

## Start configuration from loop(), never from tuh_cdc_mount_cb()

`runConfigChain()` is called from `loop()`; `tuh_cdc_mount_cb()` only records
the index and sets `configStage = CONFIG_DUMMY`. Keep it that way:

- `usbh` serialises **one control transfer per stack**: `_usbh_data.ctrl_xfer_info`
  is a single struct (`src/host/usbh.c:898-911`), and the CDC driver's
  enumeration chain (`ftdi_process_set_config` and friends — for CH34x and
  PL2303 an eleven-step vendor dance) is still running when
  `tuh_cdc_mount_cb()` fires.
- `cdch_interface_t` has a single `user_complete_cb` slot, written by every
  `tuh_cdc_set_*()` call (`cdc_host.c:525,541,589`) and read by
  `cdch_internal_control_complete()` (`cdc_host.c:890`).

Issuing the first request from inside the mount callback collides with that.
Driven from `loop()` instead, one enumeration produces exactly one `[term] …
mounted` and one `[term] ready`, and the console then stays silent — verified:
15 s open after a reset received 70 bytes, those two lines and nothing else.

## Hot-plug: a reconnect needs a port re-init

`recoverHostPort()` in `loop()` exists because the port does not recover on its
own. Measured, in order, with a CP2104 (`10c4:ea60`):

- **First connect to a fresh port works.** Pull the adapter, reset the board,
  let it boot with nothing attached (`[term] host stack up…`, device table all
  zero), plug in → `mounted` + `ready`, table flags `0x07`
  connected|addressed|configured. So the root hub and the debounce are fine.
- **A reconnect after a device was mounted never comes back.** Unplug prints
  `[term] unmounted idx=0`; replug prints nothing at all, twice from two
  different starting states. The slot either stays all-zero or freezes inside
  `ENUM_SET_ADDR`, which writes only `bus_info` + `bMaxPacketSize0` before
  issuing SET_ADDRESS (`usbh.c:1957-1964`) — a sample held `MPS0 = 0x40` with
  `connected = addressed = 0` unchanged for 9 s. No retry, no timeout.
- **Toggling VBUS at runtime is worse, not better.** Driving PA15 (`ODR`/`IDR`
  confirmed the pin) low for 2.5 s does remove the device and prints
  `unmounted`, but the port then never enumerates again either. `HPRT` stayed
  0 afterwards. The library's VBUS enable is a one-shot at init
  (`bsp_giga.cpp:108-111`); do not treat PA15 as a runtime power switch.
  Note host mode is brought up with `vbus_sensing = false`
  (`hcd_dwc2.c:500` → `dwc2_stm32.h:312-331`), so connect detection rides the
  port's CCS bit — the failure is not a VBUS-sense problem.

The stack does have a real teardown, which is what makes recovery possible:
`tuh_deinit(rhport)` (`host/usbh.h:156`) calls `hcd_deinit()`
(`portable/synopsys/dwc2/hcd_dwc2.c:526` → `dwc2_core_deinit()`), and
`tuh_rhport_init()` only skips while the port is *still active*
(`usbh.c:509-511`). So `tuh_deinit(1)` followed by `tuh_arduino_init()` puts
the port back into the power-on state — the state that is known to enumerate a
connect. `tuh_arduino_init()` is safe to call repeatedly (GPIO, clock and NVIC
setup are idempotent; it costs its `delay(1000)`).

Two rules for editing this:

- The teardown runs from `loop()`, never from `tuh_cdc_umount_cb()` — same
  reason the configuration chain does (do not tear down the stack while the
  driver is unwinding from it).
- `portRecovering` suppresses the `umount` callback that `tuh_deinit()` itself
  generates. Without it the flag re-arms every iteration and the sketch re-inits
  forever, one second at a time.

Verified after the fix: unplug → `unmounted`, `re-initialising USB-A host port
for hot-plug`, `host stack up…`, `CP210x mounted idx=0 vid=10c4 pid=ea60`,
`ready CP210x idx=0`, then 43/43 echo bytes exact including `00 01 02 fe ff`.

## Bridge mechanics

- `tuh_cdc_read()` is non-blocking: it drains the RX ring FIFO and re-arms the
  next IN URB (`src/tusb.c:451-455`); 0 means empty, not an error.
- `tuh_cdc_write()` counts **accepted** bytes, not transmitted ones, and a full
  TX FIFO accepts nothing (`src/tusb.c:408-418`). So the terminal read is
  gated on `tuh_cdc_write_available()` **before** consuming from `Serial` —
  read-then-write loses bytes. Single-threaded, nothing else writes that FIFO,
  so the gate is exact.
- `tuh_cdc_write_flush()` after every batch: without it a tail shorter than the
  endpoint MPS sits in the FIFO, and a terminal needs characters out now.
- The FTDI 2-byte modem-status prefix is stripped inside the driver
  (`cdc_host.c:691-699`); the sketch sees a plain byte stream. A 2-byte-only
  packet is dropped without firing `tuh_cdc_rx_cb` — harmless for a bridge that
  polls.
- Throughput ceiling is TinyUSB's stream API (one MPS-sized packet per
  transfer, one in-flight transfer per endpoint), not the DWC2 hardware:
  ≈11 KB/s per direction on the UART-bridge chips, which is the 115200 8N1 wire
  rate. Measured on the sibling project: CP2102 11.3/11.2, FTDI FT232R
  11.3/11.2, PL2303 11.2/11.1 KB/s, 0 errors. Measured here with
  `scripts/soak.py`: CP2104 100 KB in 8.89 s, PL2303 100 KB in 8.76 s — both
  11.3–11.4 kB/s. A CDC-ACM device has no baud limit and runs far above that:
  a Pico 2 round-tripped 100 KB in 0.27 s and 200,001 bytes in 0.53 s, so
  ~365–371 KiB/s, about 32x the UART-bridge chips.
- **A payload whose packet count is a whole number of MPS packets reads back
  short by one packet — and the missing packet is on the wire.** With a Pico 2
  on the host port, 200,000 bytes (3125 whole MPS packets) came back exactly 64
  bytes short with zero mismatches, and one more byte released all 65. A bulk
  transfer whose length is a multiple of MPS is indistinguishable from "more
  coming" unless the producer ends it with a short or zero-length packet, so
  *something* owes a terminator. **The reader that owes it on this bench is the
  PC's kernel `cdc_acm`, not the bridge** — settled below. The GIGA's USB-A host
  stack already terminates both ways: `cdc_host.c:683-686` calls
  `tu_edpt_stream_write_zlp_if_needed` after a completed TX transfer, gated in
  `tusb.c:376-383` on `last_xferred_bytes > 0 && 0 == (last_xferred_bytes & (mps-1))`
  and issuing `stream_xfer(s, 0)`, and on the read side `cdc_host.c:729` opens
  every stream with `xfer_len = tu_edpt_packet_size(desc_ep)`, so
  `tu_edpt_stream_read_xfer` asks for `min(available & ~63, 64)` — one packet
  per URB — and a bulk IN of exactly MPS completes on the full packet. The
  application cannot send a ZLP either: `tu_edpt_stream_write` opens with
  `TU_VERIFY(bufsize > 0)`, so `tuh_cdc_write(idx, buf, 0)` is a no-op.
  `scripts/soak.py` reports the shortfall as INCOMPLETE (exit 2) and prints no
  rate, since the wait time says nothing.
  **Settled by the wire, on the terminal side (Cynthion, this session).** An
  inline analyzer on the GIGA's USB-C link — between the board and this PC —
  captures raw TLPs at a level usbmon cannot reach. Two captures, one 192-byte
  payload each, decoded with the pcap record timestamps (`scripts/cynthion-seq.py`
  prints frame numbers, which are not time; `/tmp/opencode/seq-times.py` adds
  the record times):

  | capture | OUT PC→GIGA | IN GIGA→PC | what the tty read |
  |---|---|---|---|
  | `giga_linux.pcap`, host-TX ZLP on | 3 × 64 B at 0.000003 / 0.000068 / 0.000133 s | 3 × 64 B at 6.298 / 12.280 / **17.274** ms, digests matching the OUT packets, then **1 B `08` at 17.340 ms** | **193 of 192** in 0.32 s, release returned 1 |
  | `giga_linux-b.pcap`, host-TX ZLP off | 3 × 64 B at 0.000003 / 0.000068 / 0.000133 s | 3 × 64 B at 6.325 / 12.326 / **17.281** ms, digests matching | **128 of 192** in 0.31 s, then **65** after the release byte |

  Every IN DATA is followed by a `0xd2` ACK from the PC, so the third packet
  crossed GIGA→PC and was acknowledged at 17.3 ms — 295 ms before the release
  byte was even written. Nothing is lost and nothing is late: the last packet
  is **withheld by the reader**.

  **The mechanism, from kernel source** (`drivers/usb/class/cdc-acm.c`, torvalds/
  linux master). `:1388-1389` sets `readsize = usb_endpoint_maxp(epread) *
  (quirks == SINGLE_RX_URB ? 1 : 2)` — **128 bytes** for the GIGA's 64-byte
  bulk IN — and `:1413` sets `acm->rx_buflimit = num_rx_buf`. The kernel arms
  2-packet IN URBs, and a bulk IN packet of exactly MPS does not terminate a
  URB: `acm_read_bulk_callback` (`:515`) delivers only on URB completion —
  `acm_process_read_urb` (`:530`) does `tty_insert_flip_string` +
  `tty_flip_buffer_push` — and re-arms at `:585`. So 192 B = URB A (packets
  1+2, full, completes at 12.3 ms) + URB B (one packet, still open). The
  parity rule is exactly `readsize / MPS = 2`: odd packet counts leave one
  packet in an open URB, even counts fill them. A short IN packet terminates
  it, which is why the ZLP-on run delivered everything — the CP2104's spurious
  byte is itself an extra IN transfer, 1 byte at 17.340 ms. Mbed's USBCDC
  sends whole 64-byte IN packets and never a terminating short/ZLP packet of
  its own, which is why the open URB exists at all.

  **Everything upstream of the PC is exonerated, in order.** The host stack
  received and acknowledged every packet: in the host-side capture (`cp2104-
  zlp-a.pcap`) each IN DATA is followed by a `0xd2` ACK, the retry traffic is
  on the OUT side (frames `253214` and `253217`, identical payload and parity
  — the chip refusing an OUT packet while its own ring is full, benign), and
  ring-full is not it either: `cdch_data` is `0x850` = 2128 B in the linked
  .elf (RX/TX 1024 each, `tusb_config_common.h:134-135` under
  `ARDUINO_TINYUSB_BOARD_GIGA`, which `board_auto.h:56` defines), so a 192-byte
  payload fits with 832 bytes spare. The CDC host class delivered all 192: a
  delivery log compiled into `cdch_xfer_cb`'s IN branch counts `64 64 64 0`
  per payload, `cdch_rx_bytes` accounts for every byte, and
  `cdch_data[0].stream.rx.ff` shows `wr_idx == rd_idx`. Neither staging buffer
  is a discriminator — `cdch_epbuf.rx` and `_SerialUSB._tx_buffer` both keep
  the last packet after delivery, so "it is still there" says nothing. The
  terminal put all 192 on the wire inside 17.3 ms, so Mbed's USBCDC
  (`USBCDC.cpp:375-405`, `send_nb` / `_send_isr_start`) is exonerated too.
  The live USBCDC is `_SerialUSB` (0x24007350); `_UART_USB_` (0x24006c84) is a
  second, unused instance that reads all zero, which is why the earlier member
  reads failed. There is nothing to fix in the class's read accounting, and no
  class-side fix can move bytes the class already handed to `Serial.write`.
  Capture facts, all measured: nanosecond pcap (magic `4d3cb2a1`), link type
  usbll, payload = frame[1:-2] — the analyzer omits the 2-byte endpoint header
  on DATA TLPs, so Wireshark's `Data` field is the payload and `usbll.data` is
  empty like usbmon's capdata. PIDs: `0xe1` OUT request, `0x4b`/`0xc3` DATA1/
  DATA0, `0xd2` ACK, `0x5a` NAK, `0xa5` SYNC keepalive at ~1000/s. 2.7 M frames
  over 11.4 s. tshark refuses a capture it cannot traverse to: `~/mydocs` is
  755 but `~` is 750, so even a 664 file under it is rejected — copy to
  `/tmp/opencode` and `chmod 644`. `scripts/cynthion-seq.py` decodes it.
  **The Pico was never the variable, in either direction.** Plugged straight
  into this Linux PC — kernel `cdc_acm` on `/dev/ttyACM3`, echo internal, no
  serial short — the same Pico 2 (`239a:cafd`, Adafruit CDC Echo) came back
  exact across the exact-multiple-of-64 sweep (64/128/192/256/320/384/448/512,
  plus 4096 and 16384, and 64/128/192 repeated x2), no missing tail. It is
  exact because its own CDC device stack ends *every* packet with a ZLP
  (`[64][ZLP][64][ZLP]…`), so no kernel URB is ever left open — that is the
  discriminator, not the device's identity. Rebuilt on TinyUSB_Arduino device
  mode (`tests/Pico2_CDC_Echo`, VID `2341` PID `8001`, bulk OUT `0x02` / bulk
  IN `0x83`, MPS 64 — read off the config descriptor the kernel cached, and
  `lsusb` went `239a:cafd` -> `2341:8001`, so the kernel re-read the new
  endpoints rather than reusing a stale layout), the −64 signature is unchanged
  with the Pico on the GIGA's host port: swapping Adafruit TinyUSB for
  TinyUSB_Arduino on the device changes nothing, which is what makes the device
  side an eliminated variable, not a suspect. (Separately, a 65536- and
  200000-byte burst blocks on the kernel *write*: the Pico's CDC echo buffer
  can't absorb a 64 KB burst at once, and the PC — unlike the GIGA's paced
  host-TX FIFO — applies backpressure. That is a PC-side buffer limit, not the
  tail loss.)
  **The discriminator is packet parity, and the period is the kernel's.** One
  clean run at 1..16 packets: every ODD packet count comes back exactly 64 bytes
  short, every EVEN count is byte-exact (125–304 KiB/s on the exact ones, so no
  baud limit is involved), reproduced across three independent runs. The period
  is `readsize / MPS = 2` from `cdc-acm.c:1388-1389`, not a device property.
  The earlier wording is too broad: 200,000 bytes = 3125 packets (odd) lost its
  tail, while 100,000 bytes ends in a partial packet and comes back whole. Half
  of the MPS-multiple payloads are fine.
  **A plain sweep cannot see this.** A held tail is released by the *next* write,
  so it lands in the following size's read and shows up as extra bytes or a
  mismatch at index 0 — measured: `65 -> 129 back`, `256 -> corrupted at index 0
  of 256`. `scripts/mps-tail-probe.py` closes each size (one release byte, then
  drain) so every verdict is about that size alone, and it checks
  `got + tail == blob`, which is what separates held-and-released from lost.
  **At 4 KB and above the loss is not the tail and is not recoverable.** 4096
  bytes (64 packets) came back 384 short — 6 packets — and 4160 came back 576
  short, 9 packets: whole packets, mid-stream, and a release byte does not
  bring them back. That is the Pico's own device ring overflowing while the
  GIGA counts the bytes as accepted (`tuh_cdc_write` counts accepted, not
  transmitted). A 32 KB burst also wedged the host stack once the writer was
  killed mid-block; only a SWD reset cleared it, twice in this session. Keep
  boundary sweeps at or under 1 KB and the large sizes in their own run.
  The ring size is checkable: `CFG_TUD_CDC_RX_BUFSIZE` and
  `CFG_TUD_CDC_TX_BUFSIZE` are 256 each (`tusb_config_union.h:168-169`), so a
  100 KB soak is ~400x the ring and is not a volume test for this device at
  all. Measured with the split landed: 99936 of 100000 back, 542 mismatches
  from index 99392, tail never arrived — and the bridge was still alive
  afterwards (64 and 128 exact), so this occurrence did not wedge the host
  stack.
  **usbmon cannot show this at packet level.** `tcpdump -i usbmon3` reports per
  URB, not per packet: a 128-byte IN read arrives as ONE complete frame of 128
  bytes, so the capture proves the kernel arms IN continuously (16 pending
  polls, `status=-115`, precede the data) but cannot show the 64-byte packets
  or a trailing ZLP. Decoding also needs fixes the shared skill does not have:
  `d4c3b2a1` is the little-endian magic, transfer type 2 is CTRL (bulk is 3),
  and the device number is one byte at `[11]` — `usbmon_decode.py` filters to
  interrupt URBs and reports 0 for any CDC device. `scripts/usbmon-bulk.py` is
  the bulk-endpoint decoder that works.
- **A MPS-multiple payload can gain one spurious byte — measured on the CP2104.**
  Sweeping the boundary: 63, 65, 130 and 193 bytes came back exact; 64, 128 and
  192 came back whole plus exactly one extra byte, values `68`, `71`, `10` —
  different every run, so not a fixed marker. The only thing that changes at that
  boundary is the GIGA's ZLP (`cdc_host.c:683-686`), so the chip is answering a
  zero-length OUT write with a byte of its own, most likely from a stale buffer.
  One byte per payload, not one per ZLP: mid-stream the TX FIFO is not empty, so
  no ZLP is issued there. The application cannot suppress it — the ZLP is the
  library's decision. Byte-transparent, but not byte-exact: a consumer that
  counts bytes will see COUNT+1.
  **Fixed in the library, landed.** `tusb_option.h` gained
  `CFG_TUH_CDC_CP210X_SUPPRESS_ZLP`, default **1**, and `cdc_host.c:686` gates
  the call on `p_cdc->serial_drid != SERIAL_DRIVER_CP210X`, wrapped in
  `#if CFG_TUH_CDC_CP210X && CFG_TUH_CDC_CP210X_SUPPRESS_ZLP` so the enum name
  is not referenced when the driver is compiled out. No per-instance state is
  needed: every driver sets `serial_drid` in its own `*_open()` (`:1025` ACM,
  `:1221` FTDI, `:1609` CP210X, `:1781` CH34X, `:2118` PL2303), so a hot-plug
  cannot carry a stale value — which matters because
  `cdch_close_interface` (`:659-668`) resets only `daddr`, `bInterfaceNumber`
  and `mounted`. The tell is in the disassembly, not the size report — both
  builds are 164288 bytes — `cdch_xfer_cb` does `ldrb r3,[r4,#5]`, `cmp r3,#2`,
  `beq` past the call, and `bl <tu_edpt_stream_write_zlp_if_needed>` for every
  other driver. Measured after the change on the CP2104: 63/65/128/130/193
  exact, 64 and 192 `held tail` with missing = 64, and **no spurious byte at
  any size**.
  **Not a bridge property.** The same sweep on an FTDI FT232R — 64, 128 and 192
  bytes, plus a 200,000-byte soak at 11.2 KiB/s — came back exact, so the
  injection is that chip's reaction to the ZLP. At this one boundary the four
  devices measured so far do three different things: FTDI exact, CP2104 +1
  spurious byte, Pico 2 −64 (tail held), CH340 `1a86:7523` exact. Judge a new
  device, not the bridge.
  **Not family-wide, and not identity-driven**: a CP2102 reporting the *same*
  `10c4:ea60` — it differs only in `bcdUSB 0x0110` against the CP2104's
  `0x0200` — came back exact across the 63/64/65/128/130/192/193 sweep and a
  200,000-byte soak at 11.2 KiB/s. Two chips under one identity, two
  behaviours, so the quirk follows the chip's firmware rather than its
  advertised identity.
  Re-measured with the split landed: bcdUSB `0x0110` and VID `10c4` PID
  `ea60` over an unhalted read, `serial_drid = 2` so the driver-scoped
  suppression binds for it as well, 7/7 exact across the same sweep, and
  200000/200000 byte-identical in 17.47 s at 11.2 KiB/s against the 17.46 s
  baseline above. Not a discriminating run — this chip was exact before the
  change too — but it is the regression that keeps the CP210x driver working
  for a second chip under the same identity.
  **Not the chip at all — the GIGA's host-TX ZLP is what triggers it.** The same
  CP2102 (`10c4:ea60`, `bcdDevice 0x0100`) plugged straight into this Linux PC —
  handled by the kernel `cp210x` usbserial converter on `/dev/ttyUSB1`, its
  serial TX jumpered back to RX — came back exact across the 63/64/65/128/192/193
  sweep and 9 more runs at the 64/128/192 boundary, no spurious byte. The kernel
  usbser driver speaks the USB-Serial protocol and never emits the TinyUSB host-TX
  ZLP OUT (`cdc_host.c:683-686`), so the +1 is that ZLP answered by the CP2104's
  firmware, not a property of the chip or of USB-Serial generally.
  **Confirmed on the wire (Cynthion, this session).** Four captures, one payload
  each, decoded by `scripts/cynthion-zlp-pairing.py`:
  - 64, 128 and 192 bytes: the OUT endpoint sends N whole packets and then a
    zero-length DATA packet carrying the next parity, and the chip ACKs it. The
    IN endpoint answers with N whole echoes plus one 1-byte DATA packet (`30`,
    `33`, `b0`), 6 ms in — right after the echo, not at the end of the read.
  - 193 bytes: the OUT side ends in a 1-byte partial packet, no ZLP is sent, and
    the IN side is exactly 193 bytes. The spurious byte exists only when the
    ZLP is on the wire, which is the pairing the sweep could only infer.
  - The extra byte is not an echo of anything: it matches neither the last nor
    the first OUT byte (`ed`/`26` against `30`, `ea`/`b6` against `33`,
    `da`/`1c` against `b0`), so it is the chip's own buffer content.
  - The release byte is a separate burst 0.3 s later and echoes itself, so the
    extra byte is not a held tail being pushed through.
  Endpoints here are `1.1` OUT and `1.2` IN, not the Pico's 2/3, so do not
  hard-code them: direction is `usbll.src == "host"` for OUT and `usbll.dst
  == "host"` for IN.
  **What the PC side actually does (kernel source, this session).** Linux does
  not avoid whole-packet transfers; it avoids the terminator. `cp210x` is not
  the usbser protocol: `drivers/usb/serial/cp210x.c` is a dedicated driver that
  programs the chip with the vendor requests (`IFC_ENABLE` 0x00, `SET_BAUDRATE`
  0x1E, `SET_LINE_CTL` 0x03, `SET_FLOW` 0x13) and declares no `write` member,
  so `usb_serial_operations_init` in `drivers/usb/serial/usb-serial.c` binds
  `usb_serial_generic_write`. That fills URBs of `port->bulk_out_size` (256,
  from `cp210x_device.bulk_out_size`) out of the write kfifo and submits them
  with only `transfer_buffer_length` set. The URBs come from
  `usb_alloc_urb`/`usb_init_urb`, which memsets the struct, and
  `usb_fill_bulk_urb` (`include/linux/usb.h`) sets no flags at all, so
  `transfer_flags` is 0 and `URB_ZERO_PACKET` — the flag `usb_submit_urb`
  lists as allowed for bulk OUT and the only thing that makes an HCD append a
  terminating packet — is never set. The chip therefore never sees a
  zero-length write and never gets the chance to answer one.
  The trade-off is the other way round from how we put it: TinyUSB's ZLP is
  the spec-correct one (a bulk transfer whose length is a multiple of MPS is
  otherwise indistinguishable from "more coming"), and the kernel leaves that
  ambiguity unresolved, which is harmless for a UART bridge because the chip's
  UART has no notion of a transfer boundary — the next write simply
  continues the same transfer. The +1 is the CP2104's bug in answering a
  legitimate ZLP, not a difference in the bytes either side sends.
  **Removing the ZLP does remove the +1, and costs a worse fault (measured,
  this session).** One A/B on the CP2104 — same chip, same tools, one library
  line commented out at `cdc_host.c:686`, reflashed. It is the only host-TX
  ZLP site: `tuh_cdc_write_flush` (`cdc_host.c:463-467`) goes through
  `tu_edpt_stream_write_xfer`, which never issues one, and the compiled
  `cdch_xfer_cb` was the tell — the TX branch ended at
  `tu_edpt_stream_write_xfer` with no ZLP call, while the symbol stayed in the
  .elf because other classes still reference it, so the size report (164288
  both ways) says nothing. ZLP off: no extra byte in any of the 22 transfers,
  and instead 64 and 192 (plus 448 in the wider sweep) come back 64 short and
  arrive only once one more byte is written — `mps-tail-probe.py` calls that a
  held tail, and a 64-byte message returns *nothing at all* until then. ZLP
  on, same session: the recorded +1 again at 64, 128, 192, 320, 384 and 448
  (`ad d9 8d 4f 52 f0`), exact at 256, 512, 4096 and 16384 — so the +1 is not
  at every MPS multiple, only at 1, 2, 3, 5, 6 and 7 packets. One stale byte
  from the chip versus a whole packet of user data withheld until the next
  keystroke — and the withheld packet is the PC's `cdc_acm` URB, settled above:
  the disable does not break the bridge, it removes the extra IN transfer that
  happened to terminate that URB. The swap is still strictly worse for a
  terminal, so the global disable stayed out; what was landed instead is the
  driver-scoped `CFG_TUH_CDC_CP210X_SUPPRESS_ZLP` above, which leaves the ZLP
  intact for ACM, FTDI, CH34X and PL2303.
  **The held tail is a parity rule, and it is the kernel's, not the device's.**
  Re-measured on the CP2104 with the host ZLP off, one clean `mps-tail-probe.py`
  pass per packet count from 1 to 15 (64..960, all under the 1 KB limit): every
  ODD count comes back exactly 64 short and arrives once one more byte is
  written, every EVEN count is byte-exact — the period is the PC's
  `readsize / MPS = 2`. Same signature the Pico 2 showed with the ZLP *on*, so
  parity is not a device property and the Pico was never the variable in it.
  The ZLP-on table for this chip is a different period: +1 at 1, 2, 3, 5, 6, 7
  packets and exact at 4, 8, 64 and 256 — bad unless the packet count is a
  multiple of 4. Two periods, one chip, one config line: the odd-one is the
  reader's URB, the period-4 one is the chip's answer to the ZLP. The set is
  not stable across runs: a stock-build re-run of the same probe after the
  reflash gave +1 at 1, 2, 3 and 4 packets, so treat the packet counts as
  measured noise and the +1 itself as the fact. `mps-tail-probe.py` now reports
  that as `whole + 1 spurious`; before the fix it called it `lost / not
  recoverable` with `missing = -1`, which is a size the probe never
  anticipated, not a loss.
  The device2host disable (`cdc_device.c:501`) cannot help a UART-bridge chip
  at all: that call belongs to TinyUSB's own CDC *device* stack, and the chip's
  IN bytes are its firmware, so with both CDC ZLPs off the CP2104 measured the
  host-off table unchanged. Bench state at the time of that run: both `TEMP`
  disables in the library, the GIGA flashed with the host-off build, the Pico
  device built with the device-off build but not loaded — the Pico test is
  still open.
  **Neither URB shape is the variable, and the tail is not late — it is
  withheld by the PC.** Two more `TEMP` edits on the same chip, both inert:
  `CFG_TUH_CDC_RX_EPSIZE` 64→512 (multi-packet IN URBs) and `CFG_TUH_CDC_TX_EPSIZE`
  64→256 (OUT URBs the size the kernel's cp210x path submits, `port->bulk_out_size`).
  Verified live — `cdch_epbuf` grew 0x88→0x248→0x308 and globals moved
  70176→70624→70816 — and both runs returned the host-off table byte for byte.
  They are inert because the cause is downstream of both: the GIGA's own IN
  URBs are one packet each (`cdc_host.c:729`), so reshaping them cannot change
  what the PC's 128-byte URB does with the bytes it is handed.
  `scripts/hold-probe.py` settles the direction: one payload, nothing else
  written for 20 s, and the last 64 bytes never arrive at all. A late packet
  would show up; a withheld one cannot.
  **The chip is not the side that requires a terminator, either.** The same
  CP2104 plugged straight into this Linux PC — kernel `cp210x` on `/dev/ttyUSB1`,
  TX jumpered back to RX, 115200 8N1 — came back byte-exact at 63/64/65/128/130/192/193
  and at 256, 320, 384, 448, 512 and 4096: no spurious byte, no held tail, and
  `hold-probe.py` returned 64 and 192 whole with nothing else written for 20 s.
  The chip emits a whole-packet tail without any terminator, and the kernel
  neither sends one nor waits for one. So the withheld tail is neither the chip
  nor the GIGA's host-IN path — it is the PC's `cdc_acm` URB, which is the
  conclusion the Pico run already reached, reached again from this side. The
  16 KB size on the PC is a pacing artifact (10421 back in 10.43 s, still
  draining at the 10 s bound), not corruption; the 1 KB bench rule stands.
  Bench state at the end of this session: the `TEMP` global disable was
  reverted and replaced by the landed driver-scoped option, so the library is
  clean of TEMP edits but is no longer ZLP-neutral for CP210x, and the flashed
  build suppresses the ZLP for that driver — the +1 baseline no longer
  reproduces, by design.
  **The reader artifact is not cp210x-specific, and the FTDI exactness is an
  alignment accident.** A second PL2303 plugged straight into this PC — kernel
  usb-serial on `/dev/ttyUSB1`, TX jumpered back to RX — came back 7/7 exact
  across 63/64/65/128/130/192/193: the usb-serial path neither invents a byte
  nor holds a tail, so the held tail belongs to the `cdc_acm` path, and the
  only device on that path is the GIGA's own terminal toward the PC. FTDI is
  exact through the GIGA because its 2-byte status prefix is stripped inside
  the driver (`cdc_host.c:691-699`), so a 64-byte chip packet carries 62 data
  bytes and the terminal's IN packets are never 64-aligned — a short packet
  appears mid-stream and terminates the kernel's URB. PL2303 and CH34X have
  no status prefix, so they are predicted to show the held tail through the
  GIGA at odd packet counts; PL2303 on the host port is unmeasured and CH34X
  was never on the bench. **That prediction is wrong, and it is now measured.**
  A PL2303 on the GIGA's host port came back 7/7 exact across 63/64/65/128/130/192/193
  — no held tail, no spurious byte. The discriminator is therefore not the
  status prefix and not the driver: it is whether the terminal's IN packets
  happen to be whole-MPS, which follows the batch sizes the sketch writes to
  `Serial`. Whole-MPS batches were measured for the CP2104 and the Pico 2, and
  both show the held tail; FTDI's 62-data-byte packets and the PL2303 do not,
  and both are exact. **The batching is now isolated, on the wire**
  (`~/mydocs/giga_linux-c.pcap`, decoded with the pcap record times): one
  192-byte PL2303 payload crossed the USB-C link as 3 whole 64-byte OUT
  packets PC->GIGA at 0.000003/0.000069/0.000140 s and came back as **192 IN
  packets of exactly 1 byte** GIGA->PC, 0.000522..0.017183 s, gaps median 86
  us = the 115200 8N1 wire rate, every one followed by a `0xd2` ACK, and the
  bytes concatenate to the OUT payload (`a8 99 5f e5 07 a2 7f fe` first,
  matching the first OUT digest). The release byte is a separate burst 0.317 s
  later, 1 byte out and 1 back, so nothing was held. The class therefore
  delivers the PL2303 **one byte at a time** — its USB-Serial IN packets are
  byte dribbles, not 64-byte packets — the sketch writes one byte per
  `Serial.write`, and every terminal IN packet is short, so the kernel's
  128-byte URB terminates on the first packet and no URB is ever left open.
  That is why this chip is exact at every size. The discriminator is the
  granularity of the class's delivery, which follows the chip's IN packet size
  on the USB-A link: the CP2104 and the Pico 2 deliver whole 64-byte packets
  and hold the tail, the PL2303 delivers 1 byte and does not. CH34X remains
  unmeasured.
  What the IN-side change actually moved, from the code rather than the wire:
  TinyUSB delivers on URB *completion* — `cdch_xfer_cb` calls
  `tu_edpt_stream_read_xfer_complete` only when the transfer callback fires —
  so a 512-byte URB fed with 64-byte full-MPS packets never completes and
  nothing is delivered. That explains an unchanged table without implicating the
  chip, and it is why multi-packet IN URBs cannot be landed as-is: they trade a
  lost packet for an undelivered one.
- **The sketch never hands the terminal a whole-MPS batch, so the held tail
  above is worked around at the source.** `writeTerminal()` splits a batch
  whose length is a multiple of `TERMINAL_MPS` (64 — the core's
  `CDC_MAX_PACKET_SIZE` for the USB-C OTG_FS port, `USBCDC.h:30-32`) into
  n-1 + 1, so the burst ends on a short IN packet and the PC's URB terminates
  on it. `Serial.flush()` between the halves is required, not cosmetic:
  `send_nb` no-ops while `_tx_in_progress` (`USBCDC.cpp:377-393`), so without
  it the tail byte can be appended to the still-open buffer and go out as one
  whole-MPS packet. **Discriminated on the CP2104.** An earlier round of runs
  was not: `cdch_data[0].serial_drid` read 4 = `SERIAL_DRIVER_PL2303` there,
  and that chip was exact at every size before the change, so its 7/7 sweep
  said nothing. With the CP2104 back on the port — identity confirmed by an
  **unhalted** `mdh 0x240060dc` read through the MEM-AP, VID `10c4` PID `ea60`
  bcdDevice `0100` flags `0x07`, no halt anywhere near the plug event — the
  sweep returns 7/7 exact across 63/64/65/128/130/192/193, with 64 and 192
  flipped from `held tail` to exact, which is the pass condition, and
  `scripts/soak.py` returned 100000 of 100000 byte-identical in 8.71 s at
  11.2 KiB/s, the baseline within noise — at 256-byte batches only whole-MPS
  batches pay one extra 1-byte IN packet. **The CDC-ACM case is confirmed too:**
  the Pico 2 running `tests/Pico2_CDC_Echo` (VID `2341` PID `8001`, flags `0x07`
  read unhalted) returns 7/7 exact across the boundary sweep and **16/16 exact
  across 1..16 whole packets** (64..1024) at 62-281 KiB/s, so the parity rule
  that held on this device before the change — every odd count 64 short — is
  gone. **The wire confirmation is measured
  too** (`~/mydocs/giga_linux-d.pcap`, decoded with the pcap record times): the
  OUT side is one 64-byte packet at 0.000003 s, ACKed at 0.000049 s, and the IN
  side is **63 bytes at 0.006094 s then 1 byte at 0.006151 s** — the split
  leaves as two transfers and the short one is first, so the PC's URB
  terminates at 6.1 ms rather than waiting for a next write that never comes.
  The release byte is a separate burst at 0.3058 s and echoes itself, so the
  63 + 1 pair is not a held tail being pushed through. `Serial.flush()` between
  the halves is sufficient. The root cause is unchanged: it is
  still the PC's `cdc_acm` URB, and the split is a source-level workaround,
  not a correction of it. **A split on the host side cannot do the same thing**
  — `tu_edpt_stream_write` (`src/tusb.c:404-415`) buffers into a FIFO and
  `tu_edpt_stream_write_xfer` sizes the transfer from FIFO occupancy
  (`tu_fifo_read_n(&s->ff, s->ep_buf, s->xfer_len)`), so 63 + 1 still leaves
  one 64-byte OUT transfer and still satisfies the ZLP predicate; the CP210x
  `+1` stays a library-level concern, which is where
  `CFG_TUH_CDC_CP210X_SUPPRESS_ZLP` is.
- **A large interrupted write can leave the host stack stuck, and only a reset
  clears it.** A 200,000-byte soak whose Python writer was killed mid-block left
  the GIGA forwarding nothing at all afterwards: 128, 130, 256 and 258 bytes all
  returned zero, while SWD still showed the chip mounted and configured
  (flags `0x07`, `hostReady = 1`, `configStage = 4`). A board reset restored the
  loopback with the chip untouched, so the stuck state was the GIGA's, not the
  device's. `recoverHostPort()` in `loop()` covers device unmounts, not this.
- **Do not time the write side when measuring that.** `tty write()` returns once
  the bytes are queued into the CDC driver's URBs, so a 100 KB write "finished"
  in 7.17 s against an 8.68 s wire — faster than the link, which is the tell
  that the number is meaningless. Time first byte out to last byte back.
  `scripts/soak.py` does this and warns if the run beats the wire, because a
  port looped before the GIGA, or ECHO left on, looks like a pass.

## The terminal side (`Serial` on GIGA)

- `Serial` is the USB-C CDC, not a UART: `SERIAL_CDC` is defined by
  `variants/GIGA/pins_arduino.h:213`, and `cores/arduino/Arduino.h:106-108`
  maps `Serial` / `SerialUSB` to `_UART_USB_`.
- `Serial.begin(115200)` is a **no-op** on that object — `UART::begin` returns
  immediately when `is_usb` (`cores/arduino/Serial.cpp:56-61`). It is still
  called because the requirement says 115200 and because on non-CDC builds the
  same line is real.
- The GIGA advertises **9600 8N1** to the host until asked otherwise
  (`cores/arduino/USB/USBCDC.cpp:31`, `cdc_line_coding_default`), and honours
  `SET_LINE_CODING` from the host (`USBCDC.cpp:263-275`). Nothing in the sketch
  can change the advertised default without reaching into Mbed internals — out
  of scope, and "leave the device port at default settings" is the standing
  instruction. `scripts/monitor.sh` sends 115200 8N1 explicitly, which is what
  makes the host side agree.

## Gotchas

- **One CDC host instance**: `CFG_TUH_CDC 1` → `cdcd_data[1]`. A second adapter
  (hub, FT2232 dual channel) does not bind. Raise it in the library config if
  ever needed, and remember that changes every other project's build.
- **A CDC-ACM device with `bmCapabilities == 0` never mounts**: the enum-time
  line-coding `TU_ASSERT` (`cdc_host.c:845`) fails and
  `cdch_process_line_state_on_enum()` ends the mount as failed
  (`cdc_host.c:876-878`), so `tuh_cdc_mount_cb()` is never called and the
  terminal shows nothing at all. Symptom: device enumerates on a PC, is
  invisible here.
- **PL2303 EEPROM flow control is not cleared** (`cdc_host.c:2287`, commented
  out upstream because it breaks some parts). XON/XOFF enabled in EEPROM
  silently eats `x`/`q`/`X`/`Q`. The bench chip (`067b:2303`, `bcdDevice 0x0300`)
  has it off: `11 13 18` round-tripped and a 100 KB soak over all 256 values
  lost nothing. A different Prolific may still eat those bytes, so check with
  `scripts/soak.py`, which reports missing values.
- **FTDI**: chip type comes only from `bcdDevice`; FT232A (`0x200`) is refused
  (`cdc_host.c:1297`) and `FTDI_UNKNOWN` asserts, so no mount. `CFG_TUH_CDC_FTDI_LATENCY`
  is unset, so the chip keeps its 16 ms latency — visible as batched arrivals.
  FTDI/SIO accepts only the closed baud list (…, 9600, 115200), and only 7- or
  8-bit data.
- **CP210x**: `cp210x_set_baudrate` uses `SET_BAUDRATE` (0x1E) only, with no
  validation; pre-V4 firmware that speaks only `SET_BAUDDIV` is not supported.
- **CH34x**: only versions ≥ 0x30 are initialised (older chips mount
  unconfigured), baud is capped at 2 000 000 (`cdc_host.c:1860`), 1.5 stop bits
  are rejected, and RTS/DTR are inverted (`cdc_host.c:1753-1758`). **Never
  observed on this bench** — no CH340-class adapter was available, so all of
  that is read off the code and none of it is measured.
  **A CH340-class board is now on the bench**: a Mega 2560 clone, chip identity
  `1a86:7523` (`bcdDevice 2.62`, `iProduct "USB2.0-Serial"`), running
  `tests/Mega2560_CH340_Echo`. Measured from `lsusb -v`: `bInterfaceClass 0xFF`,
  `bInterfaceSubClass 1`, `bInterfaceProtocol 2`, `bNumEndpoints = 3` with the
  bulk pair FIRST (EP2 OUT `0x02` / EP2 IN `0x82`, both `wMaxPacketSize 0x0020`
  = **32**, not 64) and an interrupt endpoint last (EP1 IN `0x81`) — exactly the
  shape `ch34x_open` demands. `1a86:7523` is the third row of
  `CFG_TUH_CDC_CH34X_VID_PID_LIST` (`tusb_option.h`), so the driver binds, and
  the sketch's VID map prints `CH34x`. The chip's bulk MPS being 32 means the
  host-TX ZLP boundary for this device is at 32-byte multiples, so a boundary
  sweep has to include 31/32/33 as well as the 64-byte terminal boundary.
  Uploading to it is measured: the clone's bootloader speaks **STK500 v2**, so
  the programmer is `-cwiring` (the IDE's line), not `-cardduino` — `-cardduino`
  read back `0x00` on all ten attempts. Flash write verified by reading it back
  (`dump flash 0 64` matches the `.hex` first record `0c 94 83 00 0c 94 b4 00…`).
  The PC-side tty loopback works but only at the chip's own rate: `picocom -b
  115200 /dev/ttyUSB1` echoed everything, while the earlier raw probes at the
  converter's default speed returned zero bytes — a baud artifact, not a dead
  converter, and not a dead sketch. **Measured on the GIGA host port**: the chip
  mounts and the bridge works. `scripts/mps-tail-probe.py /dev/ttyACM0 31 32 33
  63 64 65 95 96 97 128 192 193` → **12/12 exact**, no held tail and no spurious
  byte at any size; 32/64/96/128/192 are whole multiples of the chip's 32-byte
  bulk MPS, so it is exact at both its own boundary and the terminal's 64.
  `scripts/soak.py /dev/ttyACM0 100000` → PASS, 100000 of 100000 byte-for-byte
  identical in 8.70 s = 11.2 KiB/s, the 115200 8N1 wire rate, and **256 distinct
  byte values of 256** — so this chip's EEPROM has XON/XOFF off even though
  `cdc_host.c:2287` leaves flow control uncleared; a different CH34x may still
  eat `x`/`q`/`X`/`Q`, and the soak is what reports that.
  **Hot-plug works, and the console lines are the odd part.** Two events,
  measured through the state rather than the console. A **quick** unplug +
  replug left the bridge live (40/40 byte-exact) and the slot untouched at flags
  `0x0007` with VID `1a86` PID `7523` — no teardown at all. A **slow** unplug
  (left out ~10 s) did tear down: the whole descriptor area at `_usbh_devices`
  `0x240060dc` went to zero and `configStage` `4→0`, `hostReady` `1→0`, while
  `GSNPSID` `0x40040040` stayed `0x4f54330a`, so the USB1 AHB clock stayed on.
  Replugging restored the cold-mount state exactly — `0110 00ff 0800 1a86 7523
  0262 0200 0100 0007`, `configStage = 4`, `hostIdx = 0`, `hostReady = 1` — and
  the bridge echoed 40/40 again. So the CH34x hot-plug path is good: the
  difference from the CP2104 is only that a replug inside the debounce window is
  not seen as a disconnect at all.
  **Across both events, zero `[term]` bytes reached the PC tty** — no
  `unmounted`, no `re-initialising USB-A host port`, no `mounted`, no `ready`,
  read with no input flush and an 8 s window right after the plug. The same
  tty node delivers `loop()`-driven writes a moment later (the 40/40 echo), so
  the node, the kernel `cdc_acm` read path and the terminal's TX are all alive.
  The prints at `USB_Serial_Term.ino:444` (`tuh_cdc_mount_cb`) and `:474`
  (`tuh_cdc_umount_cb`) are the ones missing, and both run inside the TinyUSB
  host stack's callback rather than from `loop()`. Not chased to a cause: the
  state machine demonstrably runs, the bytes demonstrably do not appear, and
  the earlier CP2104 hot-plug on the same build printed the whole sequence, so
  the open question is what the callback context does differently for this
  chip, not whether the bridge works.
- **Halting the app across a plug event strands the class's stream bookkeeping.**
  Measured once: a chip was plugged while the target was halted for an OpenOCD
  read, and after `resume` the port looked configured and healthy — `_usbh_devices`
  flags `0x07`, `hostReady = 1`, `configStage = 4`, `portRecovering = 0` — with
  both class FIFOs empty (`cdch_data[0].stream.{tx,rx}.ff` read `wr_idx == rd_idx
  == 0`) and nothing round-tripping at any size; a SWD `reset run` cleared it.
  Neither trigger reproduces it on its own: a chip swap with no SWD session
  round-trips normally, and a halt/`resume` with the chip already mounted and
  idle does too, both measured. The trigger is the halt spanning the plug event,
  when the DWC2 phases advance and the class's `stream_claim` bookkeeping cannot
  follow. End SWD inspections with `reset run`, not `resume` — which is what the
  debugging recipe already says. **A `reset run` can leave the port dead on its
  own**, measured: resetting while the chip stayed attached left flags `0x07`,
  `hostReady = 1`, `configStage = CONFIG_DONE` and both class FIFOs empty, with
  zero bytes at every size and no console chatter at all — the reset tears the
  USB-C device port as well, so the PC's tty may need re-opening. A second
  `reset run` restored the loopback. One dead port after a reset is not a
  firmware fault.
- **Stale build after touching the core or the library**: `rm -rf build/
  ~/.cache/arduino/`.
- **Port busy on upload**: the monitor must be closed; the 1200 bps touch reset
  needs exclusive access. `fuser -k /dev/ttyACM0` if something else holds it.
- **The ttyACM number is not stable**: the GIGA was ACM1, then ACM2, then ACM0
  inside one session as other USB devices attached and detached, and a probe
  aimed at the stale number fails with `FileNotFound` or, worse, silently
  measures the STLINK. Resolve it by identity before every run:
  `for p in /dev/ttyACM*; do udevadm info --query=property --property=ID_VENDOR_ID,ID_MODEL_ID "$p"; done`.
  `2341:0266` is the GIGA running an app, `0483:374f` the STLINK-V3, `2e8a:000c`
  the RPi Debug Probe — which exposes no ttyACM at all, so a missing node is not
  a dead board; check `lsusb` for `2341:0266` before concluding anything.
- **Board hangs after a bad sketch**: double-tap RESET → DFU (`2341:0366`),
  then `arduino-cli upload` again; BOOT0 + RESET forces the STM32 system
  bootloader (`0483:df11`) as the fallback.

## Code style

Arduino community consensus, same as the sibling Giga projects:

- Allman braces for functions and control structures, `else` on its own line
- 2-space indent, no tabs
- camelCase for functions and variables, UPPER_SNAKE_CASE for defines
- multi-statement one-liners split
- comments only where a fact is non-obvious and checkable (see the line-coding
  and write-gating blocks)

## Layout

```
USB_Serial_Term/USB_Serial_Term.ino   the whole sketch; callbacks in an extern "C"
                                      block at the end (the .ino prototype generator
                                      would mangle extern "C" signatures otherwise)
scripts/install-library.sh            sketchbook symlink for Arduino IDE 2.x
scripts/build.sh upload.sh monitor.sh CLI mirror of the IDE build, upload, terminal
scripts/upload-swd.sh                 flash over ST-Link / STLINK-V3 SWD (J10), app at 0x08040000
scripts/soak.py                       100 KB round-trip integrity + rate check (adapter TX->RX shorted)
scripts/cdc64-sweep.py                byte-exact sweep at the 64-byte boundary; select-bounded
                                      reads so a held tail reports INCOMPLETE, not a hang, and
                                      an intact payload with an extra tail prints that byte
scripts/mps-tail-probe.py             per-size verdict at the boundary: exact / held tail /
                                      lost / whole +k. Writes one release byte and drains, so
                                      a held tail cannot leak into the next size's read
scripts/hold-probe.py                 one payload, nothing else for N s: separates a withheld
                                      transfer end (never arrives) from a late or lost packet
scripts/fifo-hold-probe.py            hold one payload open and read the class's delivery log
                                      and the terminal's own TX buffer over SWD inside the
                                      hold: the class delivered every byte and the terminal
                                      staged all of it, so the −64 is neither of them
scripts/usbmon-bulk.py                usbmon decoder for CDC BULK endpoints (the shared
                                      skill's decoder filters to interrupt URBs and sees 0)
scripts/cynthion-window.py            marks the arm/stop boundaries for an inline Cynthion
                                      analyzer: countdown, one payload, release byte, stop
                                      margin; the window rules are in its header
scripts/cynthion-seq.py               decode a Cynthion raw-TLP capture into the CDC
                                      packet sequence: OUT/IN DATA, ZLPs, ACKs, retries
                                      (usbll.data is empty, so payloads come from frame[1:-2])
scripts/cynthion-zlp-pairing.py       group a capture into bursts and pair the zero-length
                                      OUT with the device's response; direction comes from
                                      usbll.src/dst, so no endpoint-number table
tests/Pico2_CDC_Echo/                 CDC echo on a Pico 2, TinyUSB_Arduino device mode,
                                      usbstack=nousb; flash-swd.sh loads it over the RPi
                                      Debug Probe (program + reset, not continue)
sketch.yaml                           pin record (mbed_giga 4.6.0, TinyUSB_Arduino 1.0.0)
```

## Flashing paths (all measured on this bench)

The board enumerates in two distinct identities and the upload recipe differs:

| state | USB identity | how to flash |
|---|---|---|
| app running | `2341:0266` (Mbed CDC) | `scripts/upload.sh /dev/ttyACM<n>` — the 1200 bps touch resets into the bootloader, then `dfu-util` |
| Mbed bootloader | `2341:0366` (CDC **+ DFU runtime**) | direct: `dfu-util --device 0x2341:0x0366 -D build/USB_Serial_Term/USB_Serial_Term.ino.bin -a0 --dfuse-address=0x08040000:leave` |
| dead app / dead USB | anything | `scripts/upload-swd.sh` over the ST-Link — works regardless of firmware state |

- **Do the 1200 bps touch only against a *running app*.** Touching the
  bootloader's CDC port is what produced `dfu-util: No DFU capable USB device
  available` even though `dfu-util -l` had just listed the device's `Internal
  Flash 2MB` alternate.
- `arduino-cli upload` has **no `--library`** flag (only `compile` does), which
  is why `upload.sh` is build-then-`--input-dir`.
- Never flash `*.with_bootloader.bin`; the plain `.bin` goes at `0x08040000`.
- SWD flash measured (current build): `wrote 166688 bytes … 50.9 KiB/s`,
  `verified 166660 bytes … 832.0 KiB/s`, dual-bank H7 probed, app bank base
  `0x08000000`. The SWD path has been used on a board whose app was hard-faulted
  and whose DFU touch therefore could not work — it is the reason there is one.

## Debugging over SWD (this is how the two faults above were found)

Console output alone was not enough — a dead app looks identical to an idle
one. Recipe, with the ST-Link on J10:

```bash
XP=~/.arduino15/packages/STMicroelectronics/tools/xpack-openocd/0.12.0-6
$XP/bin/openocd -s $XP/openocd/scripts -f interface/stlink.cfg -f target/stm32h7x.cfg \
  -c "adapter speed 2000" -c "init" -c "halt" \
  -c "mdw 0x08040000 4" -c "mdw 0x40040040 2" -c "reset run" -c "shutdown"
```

- `mdw 0x08040000 4` — the vector table. `24080000 0805xxxx` means the app is
  alive; all-zero means the reads happened during reset, not that RAM is zero.
- `mdw 0x40040040 2` — `GSNPSID` of `USB1_OTG_HS` (`0x40040000`). It must read
  `0x4f54xxxx`. **Zero means the USB1 AHB clock is off**, i.e.
  `tuh_arduino_init()` never reached its clock enable.
- Where the CPU is: run `openocd … -c "gdb_port 3333" -c "init"` in the
  background and
  `arm-none-eabi-gdb -batch -ex "target remote :3333" -ex "bt" <sketch>.elf`.
  A backtrace ending `_wait_us_ticks ← mbed_die ← mbed_halt_system ←
  mbed_error ← mbed_fault_handler ← Fault_Handler` is a hard fault, not a
  hang.
- Fault class: `CFSR` `0xE000ED28`, `HFSR` `0xE000ED2C`, `BFAR` `0xE000ED38`.
  `CFSR = 0x00008200` is BFSR `0x82` = precise BusFault with valid BFAR.
- Enumeration state without any console: `_usbh_devices` (`nm` the .elf; slot
  *n* is address *n+1*). Layout is `tuh_bus_info_t` (4 B) then
  `desc_device_noheader_t` (16 B) then flags — so `mdh <sym> 12` gives
  bcdUSB, class, **VID**, **PID**, **bcdDevice** (FTDI type), and flags at
  `+20`: `0x07` = connected|addressed|configured.
- `mdw`/`mdh` on a *running* target works through the MEM-AP; `-c "regs"` does
  not exist in OpenOCD 0.12 (it is `reg`), and a Cortex-M7 r1p1 here warns
  about erratum 3092511, so prefer an explicit `halt` before reading and
  `reset run` after rather than `resume`.

## Verifying on hardware

1. `scripts/build.sh` — expect 164352 / 70176 bytes.
2. Flash (`scripts/upload.sh`, or `scripts/upload-swd.sh` when the app or DFU
   is not cooperating), then `scripts/monitor.sh`.
3. Expect, in order:

   ```
   ### TinyUSB GIGA serial terminal ###
   USB-A host serial <-> USB-C Serial, 115200 8N1
   Types: CDC, FTDI, PL2303, CP210x, CH34x
   [term] host stack up, waiting for a device on USB-A
   [term] FTDI mounted idx=0 vid=0403 pid=6001
   [term] ready FTDI idx=0
   ```

   then silence until the next plug event.
4. Loopback without a second machine: jumper the adapter's TX to RX and write
   to the terminal — everything should come back. **Verified with FTDI
   `0403:6001` (`bcdDevice 0x0600`, FT232R)**: 30 ASCII bytes plus
   `0d 0a 00 01 02 ff fe 20 …` sent, 46 of 46 bytes returned byte-for-byte
   including the NULs and `0xff`. **Verified with CP2104 `10c4:ea60`**
   (`bcdDevice 0x0100`): 45/45 and 43/43 byte-exact on two runs, and the type
   line correctly reads `CP210x` from the VID. **Verified with PL2303
   `067b:2303` (`bcdDevice 0x0300`)**: 41/41 byte-exact including `11 13 18`,
   and `mounted` / `ready` print with the type read as `PL2303`. Note it
   reports `bcdUSB = 0x0110`, a USB 1.1 device. **Verified with CDC-ACM: a Pico
   2 on the host port, VID `239a` PID `cafd`** (the earlephilhower core's
   identity) — `bcdUSB 0x0200`, MPS 64, table flags `0x07`, cached coding 115200
   8N1, `hostReady = 1`, and 41/41 bytes byte-exact including `00 01 02 ff fe`.
   **CDC-ACM re-verified with the device rebuilt on TinyUSB_Arduino** (`tests/
   Pico2_CDC_Echo`, VID `2341` PID `8001`, built `usbstack=nousb` so the core
   does not compile its own descriptor callbacks, loaded over the RPi Debug
   Probe with `program … verify; reset` — the loaded image carries its own
   `.ota`/`.partition` table at `0x10000000`, which is what starts it; `load`
   then `continue` is refused by OpenOCD's run/halt state machine). It mounts
   as `CDC` and the −64 reproduces with the same signature, so the Adafruit
   library was never the variable. The boundary table for this device is the
   parity rule above: 1..16 packets, odd counts 64 short, even counts exact.
   **Superseded once the split landed:** the same device returns 16/16 exact
   over 64..1024 and 7/7 across the boundary sweep, so the −64 is no longer
   observable here — see the workaround bullet.
   **Second FTDI variant verified: `0403:6015`, an FT-X series chip** (`bcdDevice
   0x1000`, device-descriptor `bMaxPacketSize0 = 8` rather than 64). It mounts as
   `FTDI`, is exact across the 63/64/65/128/130/192/193 sweep, and a 200,000-byte
   soak returned 200,000 identical in 17.42 s at 11.2 KiB/s. The lower device-level
   MPS is a red herring: the ZLP boundary follows the *endpoint* packet size, which
   the sweep at 64 confirms.
   Repeat for the last one: CH340 (`1A86:7523`).
5. Binary integrity at volume: **verified with the CP2104** —
   `scripts/soak.py /dev/ttyACM1 100000` sent 100,000 seeded pseudorandom bytes
   and got 100,000 back, byte-for-byte identical, all 256 byte values exercised,
   in 8.76 s = 11.4 kB/s, which is the 115200 8N1 wire rate to within 1 %. The
   driver's cached coding confirms the rate independently: `cdcd_data` holds
   `0x0001c200` (115200) with `data_bits = 8`, parity and stop 0. **Also
   verified with the FTDI FT232R**: `scripts/soak.py /dev/ttyACM1 200000` sent
   200,000 bytes — a whole number of MPS packets — and got 200,000 back
   byte-for-byte identical in 17.40 s = 11.2 KiB/s. The FTDI is also exact at
   the MPS boundary in the small sweep (64, 128, 192), which is what separates
   the CP2104's extra byte as a device quirk rather than a bridge fault. **Also
   verified with a CP2102** (`10c4:ea60`, `bcdUSB 0x0110`): 200,000 bytes back
   identical in 17.46 s at 11.2 KiB/s, and exact across the boundary sweep.
6. Hot-plug: `unmounted` on pull, then `re-initialising USB-A host port for
   hot-plug`, `host stack up…`, `mounted` + `ready` on replug — the bridge
   resumes without resetting the board (see the hot-plug section for why this
   needs the re-init). Measured on two types: the CP2104, and separately an
   FTDI FT232R (`0403:6001`) whose table after the replug was identical to a
   cold mount (flags `0x07`, `hostReady = 1`, `configStage = 4`) and which
   round-tripped 128 bytes — a whole number of packets — exact. Re-measured on
   the CP2104 with the split landed: pull, 5 s, replug, then 7/7 exact across
   the boundary sweep with no board reset, and the table after the replug is
   identical to a cold mount — `_usbh_devices` VID `10c4` PID `ea60` flags
   `0x07`, `cdch_data[0].serial_drid = 2` = `SERIAL_DRIVER_CP210X`,
   `hostReady = 1`, `configStage = 4`. Read over an **unhalted** OpenOCD
   session, which is also how the mount lines were not disturbed. A tty read
   aimed right at a replug shows nothing: the mount lines print only after the
   1 s settle plus enumeration, and the sweep's per-size `drain()` then
   discards them, so an empty console there is an artifact, not a missed
   event.
7. Re-reset check: reset over SWD, open the port 3 s later, read 15 s — one
   `mounted` and one `ready`, nothing else (this is the mount-storm regression
   test).
