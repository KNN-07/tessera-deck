/* SPDX-License-Identifier: MIT */
#include "td_device.h"

#include <stdlib.h>
#include <string.h>

#include "third_party/tjpgd/tjpgd.h"

#define DRAG_THRESHOLD_PX 20
#define PING_MAX 256
#define LINE_PX 256

const td_layout td_layout_rev_a = {
    .model = "tessera-a5",
    .panel_w = 800, .panel_h = 480,
    .rows = 3, .cols = 5,
    .key_w = 96, .key_h = 96,
    .key_x0 = 32, .key_y0 = 8,
    .key_pitch_x = 160, .key_pitch_y = 124,
    .encoders = 4,
    .strip_x = 0, .strip_y = 380, .strip_w = 800, .strip_h = 100,
    .strip_segments = 4,
};

void td_config_defaults(td_config *cfg)
{
    cfg->version = TD_CONFIG_VERSION;
    cfg->default_brightness = 70;
    cfg->sleep_timeout_s = 0;
    cfg->long_press_ms = 500;
    cfg->debounce_ms = 8;
    cfg->enc_steps_per_detent = 4;
    cfg->enc_invert_mask = 0;
    cfg->flags = TD_CFG_FLAG_SWALLOW_WAKE | TD_CFG_FLAG_SPLASH_ON_UNLINK;
}

static void config_pack(const td_config *c, uint8_t *b)
{
    memset(b, 0, TD_CONFIG_LEN);
    b[0] = c->version;
    b[1] = c->default_brightness;
    td_wr16(b + 2, c->sleep_timeout_s);
    td_wr16(b + 4, c->long_press_ms);
    b[6] = c->debounce_ms;
    b[7] = c->enc_steps_per_detent;
    b[8] = c->enc_invert_mask;
    b[9] = c->flags;
}

static bool config_unpack(td_config *c, const uint8_t *b)
{
    td_config t;
    t.version = b[0];
    t.default_brightness = b[1];
    t.sleep_timeout_s = td_rd16(b + 2);
    t.long_press_ms = td_rd16(b + 4);
    t.debounce_ms = b[6];
    t.enc_steps_per_detent = b[7];
    t.enc_invert_mask = b[8];
    t.flags = b[9];
    if (t.version != TD_CONFIG_VERSION || t.default_brightness > 100 ||
        t.long_press_ms < 100 || t.long_press_ms > 5000 ||
        t.debounce_ms < 1 || t.debounce_ms > 50 ||
        (t.enc_steps_per_detent != 1 && t.enc_steps_per_detent != 2 && t.enc_steps_per_detent != 4)) {
        return false;
    }
    *c = t;
    return true;
}

size_t td_dev_rx_buffer_size(const td_layout *l)
{
    size_t key = (size_t)l->key_w * l->key_h * 2 + 2;
    size_t strip = (size_t)l->strip_w * l->strip_h * 2 + 10;
    return (key > strip ? key : strip) + TD_CRC_LEN;
}

void td_dev_key_rect(const td_layout *l, unsigned key, int *x, int *y)
{
    *x = l->key_x0 + (int)(key % l->cols) * l->key_pitch_x;
    *y = l->key_y0 + (int)(key / l->cols) * l->key_pitch_y;
}

static uint32_t now_ms(td_device *dev) { return dev->port.millis(dev->port.ctx); }

/* ---- transmit ---------------------------------------------------------- */

static void send(td_device *dev, uint8_t type, uint8_t seq, const uint8_t *payload, uint32_t len)
{
    uint8_t frame[PING_MAX + 1 + TD_FRAME_OVERHEAD];
    if (len > PING_MAX + 1) {
        return;
    }
    size_t n = td_frame_encode(frame, type, seq, payload, len);
    dev->port.tx(dev->port.ctx, frame, n);
}

static void ack(td_device *dev, uint8_t seq, uint8_t status) { send(dev, TD_RSP_ACK, seq, &status, 1); }

static void event(td_device *dev, uint8_t type, const uint8_t *payload, uint32_t len)
{
    send(dev, type, dev->evt_seq++, payload, len);
}

/* ---- power ------------------------------------------------------------- */

static void apply_backlight(td_device *dev)
{
    dev->port.set_backlight(dev->port.ctx, dev->awake && !dev->host_suspended, dev->brightness);
}

static void set_awake(td_device *dev, bool awake)
{
    if (dev->awake == awake) {
        return;
    }
    dev->awake = awake;
    apply_backlight(dev);
    uint8_t p = awake;
    event(dev, TD_EVT_POWER, &p, 1);
}

/* Registers user activity. Returns false when the input must not be reported
 * (it woke the deck, or belongs to the gesture that did). */
static bool input_gate(td_device *dev)
{
    dev->last_activity_ms = now_ms(dev);
    if (!dev->awake) {
        set_awake(dev, true);
        if (dev->cfg.flags & TD_CFG_FLAG_SWALLOW_WAKE) {
            dev->swallow = true;
        }
    }
    return !dev->swallow;
}

static void maybe_unswallow(td_device *dev)
{
    if (dev->swallow && !dev->key_stable && !dev->btn_stable && !dev->touch_down) {
        dev->swallow = false;
    }
}

/* ---- display ----------------------------------------------------------- */

static void show_splash(td_device *dev)
{
    if (dev->port.splash_load && dev->port.splash_load(dev->port.ctx)) {
        return;
    }
    td_splash_draw(dev);
}

static void blit_raw565(td_device *dev, int x, int y, int w, int h, const uint8_t *data)
{
    uint16_t line[LINE_PX];
    for (int row = 0; row < h; row++) {
        for (int col = 0; col < w; col += LINE_PX) {
            int n = w - col < LINE_PX ? w - col : LINE_PX;
            for (int i = 0; i < n; i++) {
                line[i] = td_rd16(data + 2 * i);
            }
            dev->port.blit(dev->port.ctx, x + col, y + row, n, 1, line);
            data += 2 * n;
        }
    }
}

static size_t jpg_in(JDEC *jd, uint8_t *buf, size_t n)
{
    td_device *dev = jd->device;
    uint32_t left = dev->jpg_len - dev->jpg_pos;
    if (n > left) {
        n = left;
    }
    if (buf) {
        memcpy(buf, dev->jpg_data + dev->jpg_pos, n);
    }
    dev->jpg_pos += (uint32_t)n;
    return n;
}

static int jpg_out(JDEC *jd, void *bitmap, JRECT *r)
{
    td_device *dev = jd->device;
    dev->port.blit(dev->port.ctx, dev->jpg_ox + r->left, dev->jpg_oy + r->top,
                   r->right - r->left + 1, r->bottom - r->top + 1, bitmap);
    return 1;
}

static uint8_t draw_image(td_device *dev, int x, int y, int w, int h, uint8_t fmt, const uint8_t *data, uint32_t len)
{
    if (fmt == TD_FMT_RGB565) {
        if (len != (uint32_t)w * h * 2) {
            return TD_ERR_SIZE_MISMATCH;
        }
        blit_raw565(dev, x, y, w, h, data);
        return TD_OK;
    }
    if (fmt == TD_FMT_JPEG) {
        JDEC jd;
        dev->jpg_data = data;
        dev->jpg_len = len;
        dev->jpg_pos = 0;
        dev->jpg_ox = (int16_t)x;
        dev->jpg_oy = (int16_t)y;
        if (jd_prepare(&jd, jpg_in, dev->jpg_pool, sizeof(dev->jpg_pool), dev) != JDR_OK) {
            return TD_ERR_BAD_IMAGE;
        }
        if (jd.width != w || jd.height != h) {
            return TD_ERR_SIZE_MISMATCH;
        }
        return jd_decomp(&jd, jpg_out, 0) == JDR_OK ? TD_OK : TD_ERR_BAD_IMAGE;
    }
    return TD_ERR_BAD_PARAM;
}

static void fill_key(td_device *dev, unsigned key, uint16_t color)
{
    int x, y;
    td_dev_key_rect(dev->layout, key, &x, &y);
    dev->port.fill(dev->port.ctx, x, y, dev->layout->key_w, dev->layout->key_h, color);
}

/* ---- requests ---------------------------------------------------------- */

static void send_info(td_device *dev, uint8_t seq)
{
    const td_layout *l = dev->layout;
    uint8_t b[TD_INFO_LEN] = {0};
    b[0] = TD_PROTO_MAJOR;
    b[1] = TD_PROTO_MINOR;
    b[2] = TD_FW_MAJOR;
    b[3] = TD_FW_MINOR;
    b[4] = TD_FW_PATCH;
    b[5] = l->rows;
    b[6] = l->cols;
    td_wr16(b + 7, l->key_w);
    td_wr16(b + 9, l->key_h);
    b[11] = l->encoders;
    td_wr16(b + 12, l->strip_w);
    td_wr16(b + 14, l->strip_h);
    b[16] = l->strip_segments;
    b[17] = TD_FMT_MASK_RGB565 | TD_FMT_MASK_JPEG;
    b[18] = dev->port.info_flags;
    td_wr32(b + 20, dev->parser.max_payload);
    char serial[16] = {0};
    if (dev->port.serial) {
        dev->port.serial(dev->port.ctx, serial);
    }
    memcpy(b + 24, serial, 16);
    size_t model_len = strlen(l->model);
    memcpy(b + 40, l->model, model_len < 14 ? model_len : 14);
    send(dev, TD_RSP_INFO, seq, b, sizeof(b));
}

static void send_state(td_device *dev, uint8_t seq)
{
    uint8_t b[TD_STATE_LEN];
    b[0] = dev->awake;
    b[1] = dev->brightness;
    td_wr32(b + 2, dev->key_stable);
    b[6] = (uint8_t)dev->btn_stable;
    b[7] = dev->host_suspended ? 1 : 0;
    td_wr32(b + 8, now_ms(dev));
    td_wr16(b + 12, dev->parser.errors);
    send(dev, TD_RSP_STATE, seq, b, sizeof(b));
}

static void on_frame(void *ctx, uint8_t type, uint8_t seq, const uint8_t *p, uint32_t len)
{
    td_device *dev = ctx;
    const td_layout *l = dev->layout;

    switch (type) {
    case TD_CMD_GET_INFO:
        send_info(dev, seq);
        return;
    case TD_CMD_PING: {
        if (len > PING_MAX) {
            ack(dev, seq, TD_ERR_BAD_LENGTH);
            return;
        }
        uint8_t b[PING_MAX + 1];
        b[0] = TD_OK;
        memcpy(b + 1, p, len);
        send(dev, TD_RSP_ACK, seq, b, len + 1);
        return;
    }
    case TD_CMD_GET_STATE:
        send_state(dev, seq);
        return;
    case TD_CMD_GET_CONFIG: {
        uint8_t b[TD_CONFIG_LEN];
        config_pack(&dev->cfg, b);
        send(dev, TD_RSP_CONFIG, seq, b, sizeof(b));
        return;
    }
    case TD_CMD_SET_CONFIG: {
        if (len != TD_CONFIG_LEN) {
            ack(dev, seq, TD_ERR_BAD_LENGTH);
            return;
        }
        if (!config_unpack(&dev->cfg, p)) {
            ack(dev, seq, TD_ERR_BAD_PARAM);
            return;
        }
        uint8_t b[TD_CONFIG_LEN];
        config_pack(&dev->cfg, b);
        bool ok = dev->port.nv_save && dev->port.nv_save(dev->port.ctx, b, sizeof(b));
        ack(dev, seq, ok ? TD_OK : TD_ERR_STORAGE);
        return;
    }
    case TD_CMD_SET_BRIGHTNESS:
        if (len != 1) {
            ack(dev, seq, TD_ERR_BAD_LENGTH);
        } else if (p[0] > 100) {
            ack(dev, seq, TD_ERR_BAD_PARAM);
        } else {
            dev->brightness = p[0];
            apply_backlight(dev);
            ack(dev, seq, TD_OK);
        }
        return;
    case TD_CMD_SET_POWER:
        if (len != 1) {
            ack(dev, seq, TD_ERR_BAD_LENGTH);
        } else if (p[0] > 1) {
            ack(dev, seq, TD_ERR_BAD_PARAM);
        } else {
            dev->last_activity_ms = now_ms(dev);
            ack(dev, seq, TD_OK);
            set_awake(dev, p[0]);
        }
        return;
    case TD_CMD_SET_KEY_IMAGE: {
        if (len < 2) {
            ack(dev, seq, TD_ERR_BAD_LENGTH);
            return;
        }
        if (p[0] >= l->rows * l->cols) {
            ack(dev, seq, TD_ERR_BAD_PARAM);
            return;
        }
        int x, y;
        td_dev_key_rect(l, p[0], &x, &y);
        ack(dev, seq, draw_image(dev, x, y, l->key_w, l->key_h, p[1], p + 2, len - 2));
        return;
    }
    case TD_CMD_SET_STRIP_IMAGE: {
        if (len < 10) {
            ack(dev, seq, TD_ERR_BAD_LENGTH);
            return;
        }
        uint32_t x = td_rd16(p), y = td_rd16(p + 2), w = td_rd16(p + 4), h = td_rd16(p + 6);
        if (!l->strip_w || !w || !h || x + w > l->strip_w || y + h > l->strip_h) {
            ack(dev, seq, TD_ERR_BAD_PARAM);
            return;
        }
        ack(dev, seq, draw_image(dev, (int)(l->strip_x + x), (int)(l->strip_y + y), (int)w, (int)h,
                                 p[8], p + 10, len - 10));
        return;
    }
    case TD_CMD_FILL: {
        if (len != 4) {
            ack(dev, seq, TD_ERR_BAD_LENGTH);
            return;
        }
        uint16_t color = td_rd16(p + 2);
        unsigned nkeys = (unsigned)l->rows * l->cols;
        switch (p[0]) {
        case TD_FILL_KEY:
            if (p[1] >= nkeys) {
                ack(dev, seq, TD_ERR_BAD_PARAM);
                return;
            }
            fill_key(dev, p[1], color);
            break;
        case TD_FILL_STRIP:
            dev->port.fill(dev->port.ctx, l->strip_x, l->strip_y, l->strip_w, l->strip_h, color);
            break;
        case TD_FILL_ALL_KEYS:
            for (unsigned k = 0; k < nkeys; k++) {
                fill_key(dev, k, color);
            }
            break;
        case TD_FILL_ALL:
            dev->port.fill(dev->port.ctx, 0, 0, l->panel_w, l->panel_h, color);
            break;
        default:
            ack(dev, seq, TD_ERR_BAD_PARAM);
            return;
        }
        ack(dev, seq, TD_OK);
        return;
    }
    case TD_CMD_RESET_DISPLAY:
        dev->brightness = dev->cfg.default_brightness;
        dev->last_activity_ms = now_ms(dev);
        show_splash(dev);
        apply_backlight(dev);
        ack(dev, seq, TD_OK);
        set_awake(dev, true);
        return;
    case TD_CMD_SAVE_SPLASH:
        if (!dev->port.splash_save) {
            ack(dev, seq, TD_ERR_UNSUPPORTED);
        } else {
            ack(dev, seq, dev->port.splash_save(dev->port.ctx) ? TD_OK : TD_ERR_STORAGE);
        }
        return;
    case TD_CMD_CLEAR_SPLASH:
        if (!dev->port.splash_clear) {
            ack(dev, seq, TD_ERR_UNSUPPORTED);
        } else {
            ack(dev, seq, dev->port.splash_clear(dev->port.ctx) ? TD_OK : TD_ERR_STORAGE);
        }
        return;
    case TD_CMD_REBOOT:
        if (len != 1) {
            ack(dev, seq, TD_ERR_BAD_LENGTH);
        } else if (p[0] > TD_REBOOT_BOOTLOADER) {
            ack(dev, seq, TD_ERR_BAD_PARAM);
        } else {
            ack(dev, seq, TD_OK);
            if (dev->port.reboot) {
                dev->port.reboot(dev->port.ctx, p[0]);
            }
        }
        return;
    default:
        ack(dev, seq, TD_ERR_UNKNOWN_CMD);
        return;
    }
}

/* ---- inputs ------------------------------------------------------------ */

static void scan_buttons(td_device *dev, uint32_t raw, uint32_t *stable, const uint32_t *change_ms,
                         unsigned count, uint8_t evt)
{
    uint32_t now = now_ms(dev);
    uint32_t diff = raw ^ *stable;
    for (unsigned i = 0; diff && i < count; i++) {
        uint32_t bit = 1u << i;
        if (!(diff & bit) || (uint32_t)(now - change_ms[i]) < dev->cfg.debounce_ms) {
            continue;
        }
        *stable ^= bit;
        bool pressed = (*stable & bit) != 0;
        if (input_gate(dev)) {
            uint8_t b[2] = {(uint8_t)i, pressed};
            event(dev, evt, b, 2);
        }
    }
    maybe_unswallow(dev);
}

void td_dev_feed_keys(td_device *dev, uint32_t raw)
{
    unsigned count = (unsigned)dev->layout->rows * dev->layout->cols;
    uint32_t now = now_ms(dev);
    uint32_t changed = raw ^ dev->key_raw;
    for (unsigned i = 0; changed && i < count; i++) {
        if (changed & (1u << i)) {
            dev->key_change_ms[i] = now;
        }
    }
    dev->key_raw = raw;
    scan_buttons(dev, dev->key_raw, &dev->key_stable, dev->key_change_ms, count, TD_EVT_KEY);
}

void td_dev_feed_enc_buttons(td_device *dev, uint8_t raw)
{
    uint32_t now = now_ms(dev);
    uint32_t changed = raw ^ dev->btn_raw;
    for (unsigned i = 0; changed && i < dev->layout->encoders; i++) {
        if (changed & (1u << i)) {
            dev->btn_change_ms[i] = now;
        }
    }
    dev->btn_raw = raw;
    scan_buttons(dev, dev->btn_raw, &dev->btn_stable, dev->btn_change_ms, dev->layout->encoders, TD_EVT_ENC_BUTTON);
}

void td_dev_feed_enc_ab(td_device *dev, unsigned idx, bool a, bool b)
{
    /* Indexed by (previous << 2) | current, each a 2-bit AB state. Invalid
     * double transitions count as 0, which also rejects contact bounce. */
    static const int8_t table[16] = {0, 1, -1, 0, -1, 0, 0, 1, 1, 0, 0, -1, 0, -1, 1, 0};
    if (idx >= dev->layout->encoders || idx >= TD_MAX_ENCODERS) {
        return;
    }
    uint8_t cur = (uint8_t)((a ? 2 : 0) | (b ? 1 : 0));
    uint8_t prev = dev->enc_prev[idx];
    dev->enc_prev[idx] = cur;
    if (prev > 3 || prev == cur) {
        return;
    }
    int8_t step = table[(prev << 2) | cur];
    if (dev->cfg.enc_invert_mask & (1u << idx)) {
        step = (int8_t)-step;
    }
    int8_t acc = (int8_t)(dev->enc_acc[idx] + step);
    int8_t per = (int8_t)dev->cfg.enc_steps_per_detent;
    int8_t delta = 0;
    while (acc >= per) {
        acc = (int8_t)(acc - per);
        delta++;
    }
    while (acc <= -per) {
        acc = (int8_t)(acc + per);
        delta--;
    }
    /* Both pins high is the detent rest position of a 4-step encoder: drop
     * any partial count there so a missed edge cannot accumulate. */
    if (per == 4 && cur == 3) {
        acc = 0;
    }
    dev->enc_acc[idx] = acc;
    if (delta) {
        if (input_gate(dev)) {
            uint8_t p[2] = {(uint8_t)idx, (uint8_t)delta};
            event(dev, TD_EVT_ENC_ROTATE, p, 2);
        }
        maybe_unswallow(dev);
    }
}

static void touch_event(td_device *dev, uint8_t kind)
{
    uint8_t b[9];
    b[0] = kind;
    td_wr16(b + 1, (uint16_t)dev->touch_x0);
    td_wr16(b + 3, (uint16_t)dev->touch_y0);
    td_wr16(b + 5, (uint16_t)dev->touch_x);
    td_wr16(b + 7, (uint16_t)dev->touch_y);
    event(dev, TD_EVT_TOUCH, b, sizeof(b));
}

static int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }

void td_dev_feed_touch(td_device *dev, bool down, int x, int y)
{
    const td_layout *l = dev->layout;
    int sx = x - l->strip_x, sy = y - l->strip_y;

    if (down && !dev->touch_down) {
        bool in_strip = l->strip_w && sx >= 0 && sy >= 0 && sx < l->strip_w && sy < l->strip_h;
        dev->touch_down = true;
        /* A touch that starts outside the strip is not input at all, even if
         * the finger later slides onto the strip. */
        dev->touch_tracking = in_strip && input_gate(dev);
        dev->touch_moved = false;
        dev->touch_long_sent = false;
        dev->touch_x0 = dev->touch_x = (int16_t)sx;
        dev->touch_y0 = dev->touch_y = (int16_t)sy;
        dev->touch_start_ms = now_ms(dev);
    } else if (down) {
        if (dev->touch_tracking) {
            dev->last_activity_ms = now_ms(dev);
            dev->touch_x = (int16_t)clampi(sx, 0, l->strip_w - 1);
            dev->touch_y = (int16_t)clampi(sy, 0, l->strip_h - 1);
            if (abs(dev->touch_x - dev->touch_x0) >= DRAG_THRESHOLD_PX ||
                abs(dev->touch_y - dev->touch_y0) >= DRAG_THRESHOLD_PX) {
                dev->touch_moved = true;
            }
        }
    } else if (dev->touch_down) {
        dev->touch_down = false;
        if (dev->touch_tracking) {
            if (dev->touch_moved) {
                touch_event(dev, TD_TOUCH_DRAG);
            } else if (!dev->touch_long_sent) {
                touch_event(dev, TD_TOUCH_TAP);
            }
        }
        dev->touch_tracking = false;
        maybe_unswallow(dev);
    }
}

/* ---- lifecycle --------------------------------------------------------- */

void td_dev_init(td_device *dev, const td_layout *layout, const td_port *port, uint8_t *rxbuf, size_t rxcap)
{
    memset(dev, 0, sizeof(*dev));
    dev->layout = layout;
    dev->port = *port;
    td_parser_init(&dev->parser, rxbuf, (uint32_t)rxcap);
    memset(dev->enc_prev, 0xFF, sizeof(dev->enc_prev));
    td_config_defaults(&dev->cfg);
}

void td_dev_boot(td_device *dev)
{
    uint8_t b[TD_CONFIG_LEN];
    if (!(dev->port.nv_load && dev->port.nv_load(dev->port.ctx, b, sizeof(b)) && config_unpack(&dev->cfg, b))) {
        td_config_defaults(&dev->cfg);
    }
    dev->brightness = dev->cfg.default_brightness;
    dev->awake = true;
    dev->last_activity_ms = now_ms(dev);
    show_splash(dev);
    apply_backlight(dev);
}

void td_dev_rx(td_device *dev, const uint8_t *data, size_t len)
{
    td_parser_feed(&dev->parser, data, len, now_ms(dev), on_frame, dev);
}

void td_dev_tick(td_device *dev)
{
    uint32_t now = now_ms(dev);

    /* Finish debounces even if the port only feeds inputs on change. */
    td_dev_feed_keys(dev, dev->key_raw);
    td_dev_feed_enc_buttons(dev, (uint8_t)dev->btn_raw);

    if (dev->touch_tracking && !dev->touch_moved && !dev->touch_long_sent &&
        (uint32_t)(now - dev->touch_start_ms) >= dev->cfg.long_press_ms) {
        dev->touch_long_sent = true;
        touch_event(dev, TD_TOUCH_LONG);
    }

    bool held = dev->key_stable || dev->btn_stable || dev->touch_tracking;
    if (held) {
        dev->last_activity_ms = now;
    } else if (dev->awake && dev->cfg.sleep_timeout_s &&
               (uint32_t)(now - dev->last_activity_ms) >= (uint32_t)dev->cfg.sleep_timeout_s * 1000u) {
        set_awake(dev, false);
    }
}

void td_dev_host_link(td_device *dev, bool connected)
{
    td_parser_reset(&dev->parser);
    if (!connected && (dev->cfg.flags & TD_CFG_FLAG_SPLASH_ON_UNLINK)) {
        dev->brightness = dev->cfg.default_brightness;
        show_splash(dev);
        apply_backlight(dev);
    }
}

void td_dev_host_suspend(td_device *dev, bool suspended)
{
    if (dev->host_suspended != suspended) {
        dev->host_suspended = suspended;
        apply_backlight(dev);
    }
}
