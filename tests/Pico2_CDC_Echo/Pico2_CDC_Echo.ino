// Copyright (c) 2026 controllercustom@myyahoo.com
// Distributed under the MIT license. See the LICENSE file for details.

// Pico2_CDC_Echo.ino — raw-byte CDC echo on the Pico 2 (RP2350), built on
// TinyUSB_Arduino device mode (NOT the Adafruit TinyUSB library).
//
// Purpose: isolate the -64 MPS-boundary loss. Plug the Pico's USB-C into the
// GIGA's USB-A host port; the GIGA sends N bytes OUT, this echoes them back IN.
// The echo is RAW (no \r\n translation, no per-byte writes) so an MPS-multiple
// payload round-trips byte-exact and the 64-byte boundary is exercised cleanly.
//
// Descriptors live in the separate usb_descriptors.cpp tab: the .ino prototype
// generator mangles the extern "C" descriptor callbacks otherwise.
//
// Build (usbstack=nousb is required: the rp2040 core otherwise compiles its own
// USB.cpp that defines the same descriptor callbacks):
//   arduino-cli compile --fqbn "rp2040:rp2040:rpipico2:usbstack=nousb" \
//     --library ~/TinyUSB_Arduino tests/Pico2_CDC_Echo

#include <ArduinoTinyUSB.h>

uint8_t buf_rx[CFG_TUD_CDC_RX_BUFSIZE];
uint8_t buf_tx[CFG_TUD_CDC_TX_BUFSIZE];

void setup()
{
  ARDUINO_TINYUSB_CONSOLE.begin(ARDUINO_TINYUSB_BAUD);

  // TinyUSB device stack on the Pico's native USB port (device XOR host at
  // runtime; tud_arduino_init() picks DEVICE). Do NOT call tuh_arduino_init().
  tud_arduino_init();

  ARDUINO_TINYUSB_CONSOLE.println("[pico2] TinyUSB_Arduino CDC echo (raw bytes)");
}

void loop()
{
  // Feed the TinyUSB device task (drives the SIE + CDC driver).
  tud_arduino_task();

  // Echo exactly what the host sent, in bulk, so the round-trip is byte-exact.
  uint32_t count = tud_cdc_n_available(0);
  if (count)
  {
    uint32_t read = tud_cdc_n_read(0, buf_rx, sizeof(buf_rx));
    tud_cdc_n_write(0, buf_rx, read);
    tud_cdc_n_write_flush(0);
  }
}
