/* Tessera Deck firmware core: portable, no OS or hardware dependencies.
 *
 * A port (ESP32-S3, the POSIX simulator, ...) supplies a td_port and calls the
 * td_dev_* entry points. The core is not re-entrant: a port that calls it from
 * several tasks must serialise the calls with one lock.
 * SPDX-License-Identifier: MIT */
#ifndef TD_DEVICE_H
#define TD_DEVICE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "td_frame.h"
#include "td_proto.h"

#define TD_FW_MAJOR 0
#define TD_FW_MINOR 1
#define TD_FW_PATCH 0

#define TD_MAX_KEYS 32
#define TD_MAX_ENCODERS 8
#define TD_JPEG_POOL 4096

/* Geometry of one hardware variant, in panel pixels. */
typedef struct {
    const char *model;      /* at most 14 characters */
    uint16_t panel_w, panel_h;
    uint8_t rows, cols;
    uint16_t key_w, key_h;
    uint16_t key_x0, key_y0;       /* top-left of key 0 */
    uint16_t key_pitch_x, key_pitch_y;
    uint8_t encoders;
    uint16_t strip_x, strip_y, strip_w, strip_h;
    uint8_t strip_segments;
} td_layout;

extern const td_layout td_layout_rev_a; /* 5" 800x480: 3x5 keys, 4 encoders, 800x100 strip */

typedef struct {
    uint8_t version;
    uint8_t default_brightness;   /* percent */
    uint16_t sleep_timeout_s;     /* 0 = never sleep on its own */
    uint16_t long_press_ms;       /* touch strip long-press threshold */
    uint8_t debounce_ms;
    uint8_t enc_steps_per_detent; /* quadrature transitions per detent: 4, 2 or 1 */
    uint8_t enc_invert_mask;      /* bit n set = encoder n counts the other way */
    uint8_t flags;                /* TD_CFG_FLAG_* */
} td_config;

typedef struct td_port {
    void *ctx;
    /* Display. Pixels are native-endian RGB565 in panel coordinates. */
    void (*blit)(void *ctx, int x, int y, int w, int h, const uint16_t *px);
    void (*fill)(void *ctx, int x, int y, int w, int h, uint16_t color);
    /* on = false blanks the display; pct is the brightness while on. */
    void (*set_backlight)(void *ctx, bool on, uint8_t pct);
    /* Send bytes to the host. May drop them when no host is listening. */
    void (*tx)(void *ctx, const uint8_t *data, size_t len);
    uint32_t (*millis)(void *ctx);
    /* Small persistent blob (the config). Return false on failure / not found. */
    bool (*nv_load)(void *ctx, uint8_t *data, size_t len);
    bool (*nv_save)(void *ctx, const uint8_t *data, size_t len);
    /* Boot splash = a stored copy of the whole screen. All three may be NULL. */
    bool (*splash_save)(void *ctx);
    bool (*splash_load)(void *ctx);
    bool (*splash_clear)(void *ctx);
    void (*reboot)(void *ctx, uint8_t mode);
    void (*serial)(void *ctx, char out[16]);
    uint8_t info_flags; /* TD_INFO_FLAG_* the port supports */
} td_port;

typedef struct {
    const td_layout *layout;
    td_port port;
    td_config cfg;
    td_parser parser;

    uint8_t brightness;
    bool awake;
    bool host_suspended;
    bool swallow;        /* dropping input until everything is released */
    uint32_t last_activity_ms;
    uint8_t evt_seq;

    uint32_t key_raw, key_stable;
    uint32_t key_change_ms[TD_MAX_KEYS];
    uint32_t btn_raw, btn_stable;
    uint32_t btn_change_ms[TD_MAX_ENCODERS];
    uint8_t enc_prev[TD_MAX_ENCODERS];
    int8_t enc_acc[TD_MAX_ENCODERS];

    bool touch_down, touch_tracking, touch_moved, touch_long_sent;
    int16_t touch_x0, touch_y0, touch_x, touch_y;
    uint32_t touch_start_ms;

    /* JPEG decode session */
    const uint8_t *jpg_data;
    uint32_t jpg_len, jpg_pos;
    int16_t jpg_ox, jpg_oy;
    uint32_t jpg_pool[TD_JPEG_POOL / 4]; /* word-aligned for the decoder */
} td_device;

/* rxbuf must hold the largest frame payload plus 4 bytes; see td_dev_rx_buffer_size(). */
size_t td_dev_rx_buffer_size(const td_layout *layout);
void td_dev_init(td_device *dev, const td_layout *layout, const td_port *port, uint8_t *rxbuf, size_t rxcap);
/* Loads the config, sets brightness and draws the splash. Call once at start-up. */
void td_dev_boot(td_device *dev);

void td_dev_rx(td_device *dev, const uint8_t *data, size_t len);
/* Call at least every 10 ms. */
void td_dev_tick(td_device *dev);

/* Raw (undebounced) inputs; bit n = key n / encoder n, 1 = pressed. */
void td_dev_feed_keys(td_device *dev, uint32_t raw);
void td_dev_feed_enc_buttons(td_device *dev, uint8_t raw);
/* Sample of one encoder's quadrature pins. Call at about 1 kHz or on change. */
void td_dev_feed_enc_ab(td_device *dev, unsigned idx, bool a, bool b);
/* Touch sample in panel coordinates. */
void td_dev_feed_touch(td_device *dev, bool down, int x, int y);

void td_dev_host_link(td_device *dev, bool connected);
void td_dev_host_suspend(td_device *dev, bool suspended);

void td_config_defaults(td_config *cfg);
void td_dev_key_rect(const td_layout *l, unsigned key, int *x, int *y);
/* Draws the built-in splash through the port. */
void td_splash_draw(td_device *dev);

#endif
