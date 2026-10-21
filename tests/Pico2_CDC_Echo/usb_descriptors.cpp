// Copyright (c) 2026 controllercustom@myyahoo.com
// Distributed under the MIT license. See the LICENSE file for details.

// usb_descriptors.cpp — TinyUSB CDC ACM descriptors for the Pico 2 echo.
//
// Separate tab because the Arduino .ino prototype generator mangles the
// extern "C" descriptor callbacks. Reused from the library's Device_CDC
// example: TUD_CDC_DESCRIPTOR emits a valid ACM interface (bInterfaceClass
// 0x02 / subclass 0x02 with bmCapabilities set), which is what the GIGA's
// host binds on. Bulk data EPs use distinct physical endpoint numbers per
// direction (OUT 0x02 / IN 0x83) so a sustained echo never deadlocks.

#include <ArduinoTinyUSB.h>

enum { ITF_NUM_CDC = 0, ITF_NUM_CDC_DATA, ITF_NUM_TOTAL };

#define CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN)

static uint8_t const desc_fs_configuration[] = {
  TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, 0x00, 100),
  TUD_CDC_DESCRIPTOR(ITF_NUM_CDC, 4, 0x81, 8, 0x02, 0x83, 64),
};

static char const *string_desc_arr[] = {
  (const char[]){ 0x09, 0x04 },  // 0: English
  "Arduino-TinyUSB",             // 1: Manufacturer
  "Pico2 CDC Echo",              // 2: Product
  "123456",                      // 3: Serial
  "TinyUSB CDC",                 // 4: CDC interface
};

static uint16_t _desc_str[32];

static tusb_desc_device_t const desc_device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = 64,
    .idVendor = 0x2341,
    .idProduct = 0x8001,
    .bcdDevice = 0x0100,
    .iManufacturer = 0x01,
    .iProduct = 0x02,
    .iSerialNumber = 0x03,
    .bNumConfigurations = 0x01
};

extern "C" uint8_t const *tud_descriptor_device_cb(void)
{
  return (uint8_t const *) &desc_device;
}

extern "C" uint8_t const *tud_descriptor_configuration_cb(uint8_t index)
{
  (void) index;
  return desc_fs_configuration;
}

extern "C" uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid)
{
  (void) langid;
  uint8_t chr_count;
  if (index == 0)
  {
    memcpy(&_desc_str[1], string_desc_arr[0], 2);
    chr_count = 1;
  }
  else
  {
    if (index >= sizeof(string_desc_arr) / sizeof(string_desc_arr[0]))
    {
      return NULL;
    }
    const char *str = string_desc_arr[index];
    chr_count = (uint8_t) strlen(str);
    if (chr_count > 31)
    {
      chr_count = 31;
    }
    for (uint8_t i = 0; i < chr_count; i++)
    {
      _desc_str[1 + i] = str[i];
    }
  }
  _desc_str[0] = (TUSB_DESC_STRING << 8) | (2 * chr_count + 2);
  return _desc_str;
}
