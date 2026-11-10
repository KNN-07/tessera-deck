/* Tessera Deck Protocol (TDP) v1 - wire constants.
 * The normative description is docs/protocol.md; keep the two in sync.
 * SPDX-License-Identifier: MIT */
#ifndef TD_PROTO_H
#define TD_PROTO_H

#include <stdint.h>

#define TD_PROTO_MAJOR 1
#define TD_PROTO_MINOR 0

/* Frame: 'T' 'D' type seq len(u32 LE) payload crc32(u32 LE)
 * crc32 (IEEE, as zlib) covers type, seq, len and payload. */
#define TD_SOF0 0x54
#define TD_SOF1 0x44
#define TD_HEADER_LEN 8
#define TD_CRC_LEN 4
#define TD_FRAME_OVERHEAD (TD_HEADER_LEN + TD_CRC_LEN)

/* Host -> device requests. Every request is answered with the same seq. */
enum {
    TD_CMD_GET_INFO = 0x01,
    TD_CMD_PING = 0x02,
    TD_CMD_GET_STATE = 0x03,
    TD_CMD_GET_CONFIG = 0x04,
    TD_CMD_SET_CONFIG = 0x05,
    TD_CMD_SET_BRIGHTNESS = 0x10,
    TD_CMD_SET_POWER = 0x11,
    TD_CMD_SET_KEY_IMAGE = 0x12,
    TD_CMD_SET_STRIP_IMAGE = 0x13,
    TD_CMD_FILL = 0x14,
    TD_CMD_RESET_DISPLAY = 0x15,
    TD_CMD_SAVE_SPLASH = 0x16,
    TD_CMD_CLEAR_SPLASH = 0x17,
    TD_CMD_REBOOT = 0x20,
};

/* Device -> host responses */
enum {
    TD_RSP_ACK = 0x80,    /* status u8, then optional data (PING echo) */
    TD_RSP_INFO = 0x81,
    TD_RSP_STATE = 0x82,
    TD_RSP_CONFIG = 0x83,
};

/* Device -> host unsolicited events (seq is a free-running counter) */
enum {
    TD_EVT_KEY = 0xA0,        /* key u8, pressed u8 */
    TD_EVT_ENC_ROTATE = 0xA1, /* encoder u8, delta i8 (detents, + = clockwise) */
    TD_EVT_ENC_BUTTON = 0xA2, /* encoder u8, pressed u8 */
    TD_EVT_TOUCH = 0xA3,      /* kind u8, x u16, y u16, x2 u16, y2 u16 */
    TD_EVT_POWER = 0xA4,      /* awake u8 */
};

enum {
    TD_OK = 0,
    TD_ERR_UNKNOWN_CMD = 1,
    TD_ERR_BAD_LENGTH = 2,
    TD_ERR_BAD_PARAM = 3,
    TD_ERR_BAD_IMAGE = 4,
    TD_ERR_SIZE_MISMATCH = 5,
    TD_ERR_STORAGE = 6,
    TD_ERR_UNSUPPORTED = 7,
};

enum { TD_FMT_RGB565 = 0, TD_FMT_JPEG = 1 };
#define TD_FMT_MASK_RGB565 0x01
#define TD_FMT_MASK_JPEG 0x02

enum { TD_FILL_KEY = 0, TD_FILL_STRIP = 1, TD_FILL_ALL_KEYS = 2, TD_FILL_ALL = 3 };
enum { TD_TOUCH_TAP = 0, TD_TOUCH_LONG = 1, TD_TOUCH_DRAG = 2 };
enum { TD_REBOOT_NORMAL = 0, TD_REBOOT_BOOTLOADER = 1 };

/* INFO flags */
#define TD_INFO_FLAG_HW_BACKLIGHT 0x01 /* brightness drives a real backlight PWM */
#define TD_INFO_FLAG_TOUCH 0x02
#define TD_INFO_FLAG_SPLASH_STORE 0x04

/* CONFIG flags */
#define TD_CFG_FLAG_SWALLOW_WAKE 0x01      /* the press that wakes the deck is not reported */
#define TD_CFG_FLAG_SPLASH_ON_UNLINK 0x02  /* show the splash when the host closes the port */

#define TD_INFO_LEN 54
#define TD_STATE_LEN 14
#define TD_CONFIG_LEN 12
#define TD_CONFIG_VERSION 1

#endif
