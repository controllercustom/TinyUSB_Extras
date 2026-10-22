// Copyright (c) 2026 controllercustom@myyahoo.com
// Distributed under the MIT license. See the LICENSE file for details.

// Mega2560_CH340_Echo.ino — raw-byte serial echo on a Mega 2560 clone whose
// serial port is a CH340/CH341 USB-Serial chip (VID 1a86, PID 7523).
//
// Purpose: put a CH34x-class device on the bench, which it never had. Every
// CH34x claim in AGENTS.md is read off code, so this is the first measurement
// for that class. Plug the board into the GIGA's USB-A host port: TinyUSB's
// CH34x backend initialises the chip, the GIGA sends bytes OUT, the chip's TX
// feeds this sketch, and its RX feeds the GIGA back.
//
// The shape the CH34x driver demands (cdc_host.c:1770-1792): bNumEndpoints ==
// 3, the bulk pair FIRST and an interrupt endpoint last, whose address the
// driver stores in ep_notif. The mount-time chain is READ_VERSION -> SERIAL_INIT
// -> SPECIAL_REG_WRITE -> FLOW_CONTROL (cdc_host.c:1806-1840), and only a chip
// whose self-reported version is >= 0x30 is initialised at all -- the version
// comes from a vendor control-IN, not from bcdDevice.
//
// The echo is raw and silent: nothing here prints, so the round-trip is
// byte-clean and an MPS-multiple payload exercises the 64-byte boundary
// without extra bytes of our own. Baud is not this sketch's concern -- the
// GIGA programs the chip through the vendor requests above.
//
// Build + upload (the board is a CH340 clone: avrdude reaches it over the same
// USB device the kernel's ch341-uart converter is attached to):
//   arduino-cli compile --fqbn arduino:avr:mega tests/Mega2560_CH340_Echo
//   arduino-cli upload  --fqbn arduino:avr:mega --port /dev/ttyUSB1 tests/Mega2560_CH340_Echo

#include <Arduino.h>

void setup()
{
  Serial.begin(115200);
}

void loop()
{
  while (Serial.available() > 0)
  {
    Serial.write((uint8_t) Serial.read());
  }
}
