// Copyright (c) 2026 controllercustom@myyahoo.com
// Distributed under the MIT license. See the LICENSE file for details.

/*
 * USB_Serial_Term — USB-C terminal <-> USB-A host serial bridge.
 *
 * Runs on the Arduino GIGA R1 WiFi. Every byte received from a serial device
 * on the USB-A host port is written to Serial; every byte received from Serial
 * is written to that device. Serial is the USB-C port the PC terminal is
 * plugged into.
 *
 * Wiring:
 *   PC          -- USB-C --> GIGA  Serial   (Mbed CDC, /dev/ttyACM0)
 *   serial unit -- USB-A --> GIGA  host port (OTG_HS, VBUS on PA_15)
 *
 * Supported host serial types, all bound by the TinyUSB CDC host driver:
 *   CDC-ACM   interface class 0x02 subclass 0x02
 *   FTDI      vendor VID 0x0403
 *   PL2303    vendor VID 0x067B
 *   CP210x    vendor VID 0x10C4
 *   CH34x     vendor VID 0x1A86, 0x4348, 0x2184, 0x9986
 *
 * Role split: Mbed keeps the USB-C device port at its default settings, so
 * tud_arduino_init() is never called and TinyUSB only ever owns the USB-A
 * host port. tuh_arduino_init() powers USB-A VBUS (PA_15 HIGH, 1 s settle).
 *
 * Line coding is 8 data bits, no parity, 1 stop bit at both ends. The driver
 * already applies 115200 8N1 at enumeration (CFG_TUH_CDC_LINE_CODING_ON_ENUM);
 * configureHostSerial() re-applies it through a dummy rate so that the PL2303,
 * which rejects a request identical to its cached line coding, is guaranteed
 * to have been told. If that request fails the bridge stays down: a dead
 * bridge is better than one silently running at the wrong baud rate.
 */

#include <ArduinoTinyUSB.h>

#define TERMINAL_BAUD     115200
#define HOST_BAUD         115200
#define HOST_DUMMY_BAUD   1200
#define BRIDGE_BUF_SIZE   256

static uint8_t hostIdx = 0;
static bool hostReady = false;
static const char *hostTypeName = "unknown";
static cdc_line_coding_t hostCoding;

// VID is the only signal an application gets: the interface descriptor always
// reports TUSB_CLASS_CDC (cdc_host.c:426), so it cannot tell the five types
// apart. A device whose VID is not in the library's vendor lists binds only
// through the ACM path (cdch_open, cdc_host.c:738), and a vendor-specific
// device that matches no list never mounts at all -- so "CDC" as the default
// is a deduction from what mounted, not a guess.
static const char *hostTypeNameOf(uint16_t vid)
{
  switch (vid)
  {
    case 0x0403:
      return "FTDI";
    case 0x067B:
      return "PL2303";
    case 0x10C4:
      return "CP210x";
    case 0x1A86:
    case 0x4348:
    case 0x2184:
    case 0x9986:
      return "CH34x";
    default:
      return "CDC";
  }
}

// ---------------------------------------------------------------------------
// Mount-time configuration: dummy line coding -> 115200 8N1 -> DTR+RTS.
//
// The chain is started from loop(), never from tuh_cdc_mount_cb(). A control
// request made inside the mount callback overwrites cdch_interface_t's single
// user_complete_cb slot while the driver's own enumeration chain is still
// using it: the enum never completes, usbh retries SET_CONFIGURATION, and the
// mount callback fires again — an endless mount storm. Measured on hardware:
// ~10000 mount lines in 20 s before the state machine happened to settle.
// ---------------------------------------------------------------------------

#define CONFIG_RETRY_MAX 20

enum ConfigStage
{
  CONFIG_NONE = 0,
  CONFIG_DUMMY,
  CONFIG_TARGET,
  CONFIG_MODEM,
  CONFIG_DONE
};

static volatile ConfigStage configStage = CONFIG_NONE;
static bool configPending = false;
static uint8_t configRetries = 0;

static void configComplete(tuh_xfer_t *xfer)
{
  configPending = false;
}

static void configCodingComplete(tuh_xfer_t *xfer)
{
  configPending = false;

  if (xfer->result != XFER_RESULT_SUCCESS)
  {
    Serial.print("[term] line coding stage failed, result=");
    Serial.println((int) xfer->result);
    configStage = CONFIG_NONE;
    Serial.println("[term] bridge held down, replug the device");
  }
}

static void configModemComplete(tuh_xfer_t *xfer)
{
  configPending = false;

  if (xfer->result != XFER_RESULT_SUCCESS)
  {
    // Tolerated: a CDC device whose bmCapabilities says "no line requests"
    // refuses this one but still carries data.
    Serial.println("[term] DTR/RTS refused");
  }

  configStage = CONFIG_DONE;
  hostReady = true;
  Serial.print("[term] ready ");
  Serial.print(hostTypeName);
  Serial.print(" idx=");
  Serial.println(hostIdx);
}

static void setHostCoding(uint32_t baud, tuh_xfer_cb_t completeCb)
{
  hostCoding.bit_rate = baud;
  hostCoding.stop_bits = CDC_LINE_CODING_STOP_BITS_1;
  hostCoding.parity = CDC_LINE_CODING_PARITY_NONE;
  hostCoding.data_bits = 8;

  configPending = true;

  if (tuh_cdc_set_line_coding(hostIdx, &hostCoding, completeCb, (uintptr_t) hostIdx))
  {
    configRetries = 0;
    return;
  }

  configPending = false;
  configRetries++;

  if (configRetries > CONFIG_RETRY_MAX)
  {
    Serial.println("[term] set_line_coding never accepted, giving up");
    configStage = CONFIG_NONE;
  }
}

// ---------------------------------------------------------------------------
// Hot-plug recovery.
//
// Measured on hardware: the first connect to a fresh port enumerates fine
// (pull the adapter, reset the board, plug it in — mounted + ready), but a
// *re*connect after a device was mounted never comes back. Unplug prints
// `unmounted`, replug prints nothing at all, and _usbh_devices stays empty or
// stalls in ENUM_SET_ADDR (usbh.c:1957-1964 writes bus_info +
// bMaxPacketSize0 into the new slot before SET_ADDRESS; the sample froze there
// for 9 s with connected/addressed still 0). No retry, no recovery — the
// bridge is dead until the board is reset.
//
// The stack has a real teardown: hcd_dwc2.c:526 -> dwc2_core_deinit(), reached
// through tuh_deinit() (usbh.h:156), and tuh_rhport_init() only skips while
// the port is still active (usbh.c:509-511). So deinit + init puts the port
// back into the state a power-on leaves it in — which is the state that is
// known to enumerate a connect correctly.
// ---------------------------------------------------------------------------

#define HOST_RHPORT 1

static volatile bool portNeedsReinit = false;
static bool portRecovering = false;

// Called from loop(), never from the umount callback: tearing the stack down
// while the driver is still unwinding from it is the same bug as issuing
// control requests from a mount callback.
static void recoverHostPort(void)
{
  if (!portNeedsReinit || portRecovering)
  {
    return;
  }

  portRecovering = true;
  portNeedsReinit = false;

  Serial.println("[term] re-initialising USB-A host port for hot-plug");
  tuh_deinit(HOST_RHPORT);
  tuh_arduino_init();
  Serial.println("[term] host stack up, waiting for a device on USB-A");

  portRecovering = false;
}

// Drives the configuration chain one control request at a time, from task
// context. Each stage waits for its completion before the next is issued.
static void runConfigChain(void)
{
  if (configStage == CONFIG_NONE || configStage == CONFIG_DONE || configPending)
  {
    return;
  }

  if (!tuh_cdc_mounted(hostIdx))
  {
    configStage = CONFIG_NONE;
    return;
  }

  switch (configStage)
  {
    case CONFIG_DUMMY:
      // A rate no driver can already be holding. pl2303_set_line_coding()
      // returns false for a request equal to its cached coding, and the cache
      // is filled by the enumeration-time request, so a direct 115200 could
      // never reach a chip still at its power-on default.
      setHostCoding(HOST_DUMMY_BAUD, configCodingComplete);
      configStage = CONFIG_TARGET;
      break;

    case CONFIG_TARGET:
      setHostCoding(HOST_BAUD, configCodingComplete);
      configStage = CONFIG_MODEM;
      break;

    case CONFIG_MODEM:
    {
      // Nobody else asserts these: CFG_TUH_CDC_LINE_CONTROL_ON_ENUM is
      // undefined, so the driver leaves DTR/RTS low after enumeration. A
      // device that gates its transmitter on DTR would stay silent forever.
      uint16_t const lineState = CDC_CONTROL_LINE_STATE_DTR | CDC_CONTROL_LINE_STATE_RTS;

      configPending = true;

      if (tuh_cdc_set_control_line_state(hostIdx, lineState, configModemComplete, (uintptr_t) hostIdx))
      {
        configRetries = 0;
        break;
      }

      configPending = false;
      configRetries++;

      if (configRetries > CONFIG_RETRY_MAX)
      {
        Serial.println("[term] set_control_line_state never accepted, giving up");
        configStage = CONFIG_NONE;
      }
      break;
    }

    default:
      configStage = CONFIG_NONE;
      break;
  }
}

// ---------------------------------------------------------------------------
// Bridge
// ---------------------------------------------------------------------------

static void bridgeHostToTerminal(void)
{
  uint8_t buf[BRIDGE_BUF_SIZE];
  uint32_t const available = tuh_cdc_read_available(hostIdx);

  if (available == 0)
  {
    return;
  }

  uint32_t const wanted = (available < BRIDGE_BUF_SIZE) ? available : BRIDGE_BUF_SIZE;
  uint32_t const count = tuh_cdc_read(hostIdx, buf, wanted);

  if (count > 0)
  {
    Serial.write(buf, count);
  }
}

static void bridgeTerminalToHost(void)
{
  int const pending = Serial.available();

  if (pending <= 0)
  {
    return;
  }

  // Gate before consuming from Serial: tu_edpt_stream_write() into a full TX
  // FIFO accepts nothing and the bytes taken off Serial would be lost.
  uint32_t const room = tuh_cdc_write_available(hostIdx);

  if (room == 0)
  {
    return;
  }

  uint32_t wanted = (uint32_t) pending;

  if (wanted > BRIDGE_BUF_SIZE)
  {
    wanted = BRIDGE_BUF_SIZE;
  }
  if (wanted > room)
  {
    wanted = room;
  }

  uint8_t buf[BRIDGE_BUF_SIZE];
  uint32_t count = 0;

  while (count < wanted)
  {
    int const byte = Serial.read();

    if (byte < 0)
    {
      break;
    }
    buf[count++] = (uint8_t) byte;
  }

  if (count == 0)
  {
    return;
  }

  uint32_t written = tuh_cdc_write(hostIdx, buf, count);
  tuh_cdc_write_flush(hostIdx);

  if (written < count)
  {
    Serial.print("[term] dropped ");
    Serial.print(count - written);
    Serial.println(" bytes, host TX FIFO full");
  }
}

// ---------------------------------------------------------------------------

void setup()
{
  Serial.begin(TERMINAL_BAUD);

  // The host stack prints its bring-up steps to ARDUINO_TINYUSB_CONSOLE, which
  // is Serial1 (D0/D1) on the GIGA. UART::write() dereferences the mbed serial
  // object without a null check, so printing before begin() faults the board
  // into mbed_die. Begin it even when nothing is wired to D0/D1.
  Serial1.begin(TERMINAL_BAUD);

  while (!Serial && millis() < 2000)
  {
    delay(1);
  }

  Serial.println();
  Serial.println("### TinyUSB GIGA serial terminal ###");
  Serial.println("USB-A host serial <-> USB-C Serial, 115200 8N1");
  Serial.println("Types: CDC, FTDI, PL2303, CP210x, CH34x");

  tuh_arduino_init();

  Serial.println("[term] host stack up, waiting for a device on USB-A");
}

void loop()
{
  tuh_arduino_poll();
  recoverHostPort();
  runConfigChain();

  if (!hostReady || !tuh_cdc_mounted(hostIdx))
  {
    return;
  }

  bridgeHostToTerminal();
  bridgeTerminalToHost();
}

extern "C" {

void tuh_cdc_mount_cb(uint8_t idx)
{
  hostIdx = idx;
  hostReady = false;
  configPending = false;
  configRetries = 0;

  uint16_t vid = 0;
  uint16_t pid = 0;
  tuh_itf_info_t info;

  if (tuh_cdc_itf_get_info(idx, &info) && tuh_vid_pid_get(info.daddr, &vid, &pid))
  {
    hostTypeName = hostTypeNameOf(vid);
  }

  char line[96];
  snprintf(line, sizeof(line), "[term] %s mounted idx=%u vid=%04x pid=%04x",
    hostTypeName, idx, vid, pid);
  Serial.println(line);

  // Keystrokes typed while no device was attached must not be blasted into
  // a freshly mounted one.
  while (Serial.available())
  {
    Serial.read();
  }

  tuh_cdc_read_clear(idx);
  tuh_cdc_write_clear(idx);

  // Arm the chain; loop() runs it. No control request may be issued from here.
  configStage = CONFIG_DUMMY;
}

void tuh_cdc_umount_cb(uint8_t idx)
{
  if (idx == hostIdx)
  {
    hostReady = false;
    configStage = CONFIG_NONE;
    configPending = false;

    // Ignore the umount that tuh_deinit() itself generates.
    if (!portRecovering)
    {
      portNeedsReinit = true;
    }
  }
  Serial.print("[term] unmounted idx=");
  Serial.println(idx);
}

}
