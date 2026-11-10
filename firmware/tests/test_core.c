/* Unit tests for the portable firmware core. Build and run: see firmware/README.md
 * SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "jpeg_fixture.h"
#include "td_device.h"
#include "td_fb.h"

static int failures, checks;
#define CHECK(cond)                                                              \
    do {                                                                         \
        checks++;                                                                \
        if (!(cond)) {                                                           \
            failures++;                                                          \
            printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);               \
        }                                                                        \
    } while (0)

/* ---- mock port --------------------------------------------------------- */

#define W 800
#define H 480
static uint16_t master[W * H], scan[W * H];
static td_fb fb;
static uint32_t clock_ms;
static int bl_on;
static uint8_t bl_pct;
static uint8_t nv[TD_CONFIG_LEN];
static int nv_valid, nv_fail;
static uint16_t splash[W * H];
static int splash_valid;
static int rebooted = -1;

typedef struct {
    uint8_t type, seq;
    uint8_t payload[300];
    uint32_t len;
} frame;
static frame out[64];
static int out_n;
static td_parser out_parser;
static uint8_t out_buf[1024];

static void out_cb(void *c, uint8_t type, uint8_t seq, const uint8_t *p, uint32_t len)
{
    (void)c;
    if (out_n < 64 && len <= sizeof(out[0].payload)) {
        out[out_n].type = type;
        out[out_n].seq = seq;
        out[out_n].len = len;
        memcpy(out[out_n].payload, p, len);
        out_n++;
    }
}

static void m_blit(void *c, int x, int y, int w, int h, const uint16_t *px) { (void)c; td_fb_blit(&fb, x, y, w, h, px); }
static void m_fill(void *c, int x, int y, int w, int h, uint16_t col) { (void)c; td_fb_fill(&fb, x, y, w, h, col); }
static void m_bl(void *c, bool on, uint8_t pct) { (void)c; bl_on = on; bl_pct = pct; td_fb_set_level(&fb, pct); }
static void m_tx(void *c, const uint8_t *d, size_t n) { (void)c; td_parser_feed(&out_parser, d, n, clock_ms, out_cb, NULL); }
static uint32_t m_ms(void *c) { (void)c; return clock_ms; }
static bool m_nv_load(void *c, uint8_t *d, size_t n) { (void)c; if (!nv_valid) return false; memcpy(d, nv, n); return true; }
static bool m_nv_save(void *c, const uint8_t *d, size_t n) { (void)c; if (nv_fail) return false; memcpy(nv, d, n); nv_valid = 1; return true; }
static bool m_sp_save(void *c) { (void)c; memcpy(splash, master, sizeof(splash)); splash_valid = 1; return true; }
static bool m_sp_load(void *c) { (void)c; if (!splash_valid) return false; td_fb_blit(&fb, 0, 0, W, H, splash); return true; }
static bool m_sp_clear(void *c) { (void)c; splash_valid = 0; return true; }
static void m_reboot(void *c, uint8_t mode) { (void)c; rebooted = mode; }
static void m_serial(void *c, char o[16]) { (void)c; memcpy(o, "TEST000000000042", 16); }

static td_device dev;
static uint8_t *rx;
static const td_layout *L = &td_layout_rev_a;

static void start(void)
{
    td_port port = {
        .blit = m_blit, .fill = m_fill, .set_backlight = m_bl, .tx = m_tx, .millis = m_ms,
        .nv_load = m_nv_load, .nv_save = m_nv_save,
        .splash_save = m_sp_save, .splash_load = m_sp_load, .splash_clear = m_sp_clear,
        .reboot = m_reboot, .serial = m_serial, .info_flags = TD_INFO_FLAG_TOUCH,
    };
    size_t cap = td_dev_rx_buffer_size(L);
    free(rx);
    rx = malloc(cap);
    td_fb_init(&fb, master, scan, W, H, true);
    td_parser_init(&out_parser, out_buf, sizeof(out_buf));
    td_dev_init(&dev, L, &port, rx, cap);
    td_dev_boot(&dev);
    for (unsigned i = 0; i < L->encoders; i++) {
        td_dev_feed_enc_ab(&dev, i, true, true);
    }
    out_n = 0;
}

static void fresh(void)
{
    nv_valid = nv_fail = splash_valid = 0;
    rebooted = -1;
    clock_ms = 1000;
    start();
}

static void advance(uint32_t ms)
{
    while (ms--) {
        clock_ms++;
        td_dev_tick(&dev);
    }
}

static uint8_t seq_counter;
static uint8_t *big;

/* Sends a request and returns the first frame it produced (NULL if none). */
static frame *request(uint8_t type, const uint8_t *payload, uint32_t len)
{
    if (!big) {
        big = malloc(200000);
    }
    out_n = 0;
    size_t n = td_frame_encode(big, type, ++seq_counter, payload, len);
    td_dev_rx(&dev, big, n);
    return out_n ? &out[0] : NULL;
}

static int status(uint8_t type, const uint8_t *payload, uint32_t len)
{
    frame *f = request(type, payload, len);
    if (!f || f->type != TD_RSP_ACK || f->seq != seq_counter || f->len < 1) {
        return -1;
    }
    return f->payload[0];
}

static uint16_t px(int x, int y) { return master[y * W + x]; }

/* ---- tests ------------------------------------------------------------- */

static void test_crc(void)
{
    CHECK(td_crc32(0, (const uint8_t *)"123456789", 9) == 0xCBF43926u);
    uint32_t c = td_crc32(0, (const uint8_t *)"1234", 4);
    CHECK(td_crc32(c, (const uint8_t *)"56789", 5) == 0xCBF43926u);
}

static int got_n;
static uint8_t got_type, got_seq;
static uint32_t got_len;
static void got_cb(void *c, uint8_t type, uint8_t seq, const uint8_t *p, uint32_t len)
{
    (void)c; (void)p;
    got_n++; got_type = type; got_seq = seq; got_len = len;
}

static void test_parser(void)
{
    uint8_t buf[64], frame_bytes[80];
    td_parser p;
    td_parser_init(&p, buf, sizeof(buf));
    uint8_t payload[10] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10};
    size_t n = td_frame_encode(frame_bytes, 0x42, 7, payload, 10);
    CHECK(n == 22);

    got_n = 0;
    td_parser_feed(&p, frame_bytes, n, 0, got_cb, NULL);
    CHECK(got_n == 1 && got_type == 0x42 && got_seq == 7 && got_len == 10);

    /* byte at a time, with leading garbage that includes a lone 'T' */
    got_n = 0;
    uint8_t junk[5] = {0x00, 0x54, 0x99, 0x54, 0x54};
    td_parser_feed(&p, junk, 3, 0, got_cb, NULL);
    for (size_t i = 0; i < n; i++) {
        td_parser_feed(&p, frame_bytes + i, 1, 0, got_cb, NULL);
    }
    CHECK(got_n == 1 && p.errors == 0);

    /* corrupted payload is dropped and counted, the next frame still parses */
    got_n = 0;
    frame_bytes[10] ^= 0x01;
    td_parser_feed(&p, frame_bytes, n, 0, got_cb, NULL);
    CHECK(got_n == 0 && p.errors == 1);
    frame_bytes[10] ^= 0x01;
    td_parser_feed(&p, frame_bytes, n, 0, got_cb, NULL);
    CHECK(got_n == 1);

    /* a length beyond the buffer is rejected */
    uint8_t huge[8] = {0x54, 0x44, 1, 1, 0xFF, 0xFF, 0xFF, 0x7F};
    td_parser_feed(&p, huge, 8, 0, got_cb, NULL);
    CHECK(p.errors == 2);

    /* half a frame, then silence: the stale half is discarded */
    got_n = 0;
    td_parser_feed(&p, frame_bytes, 12, 100, got_cb, NULL);
    td_parser_feed(&p, frame_bytes, n, 100 + TD_FRAME_TIMEOUT_MS + 1, got_cb, NULL);
    CHECK(got_n == 1 && p.errors == 3);

    /* zero-length payload */
    got_n = 0;
    n = td_frame_encode(frame_bytes, 0x02, 9, NULL, 0);
    td_parser_feed(&p, frame_bytes, n, 5000, got_cb, NULL);
    CHECK(got_n == 1 && got_len == 0);
}

static void test_info_and_ping(void)
{
    fresh();
    frame *f = request(TD_CMD_GET_INFO, NULL, 0);
    CHECK(f && f->type == TD_RSP_INFO && f->len == TD_INFO_LEN);
    if (f) {
        const uint8_t *b = f->payload;
        CHECK(b[0] == TD_PROTO_MAJOR && b[5] == 3 && b[6] == 5);
        CHECK(td_rd16(b + 7) == 96 && td_rd16(b + 9) == 96 && b[11] == 4);
        CHECK(td_rd16(b + 12) == 800 && td_rd16(b + 14) == 100 && b[16] == 4);
        CHECK(b[17] == 3);
        CHECK(td_rd32(b + 20) == 800 * 100 * 2 + 10);
        CHECK(memcmp(b + 24, "TEST000000000042", 16) == 0);
        CHECK(strncmp((const char *)b + 40, "tessera-a5", 14) == 0);
    }
    uint8_t echo[5] = {9, 8, 7, 6, 5};
    f = request(TD_CMD_PING, echo, 5);
    CHECK(f && f->type == TD_RSP_ACK && f->len == 6 && f->payload[0] == TD_OK && memcmp(f->payload + 1, echo, 5) == 0);
    CHECK(status(0x7E, NULL, 0) == TD_ERR_UNKNOWN_CMD);
}

static void test_images(void)
{
    fresh();
    static uint8_t msg[200000];

    /* raw key image: key 7 = row 1, column 2 */
    msg[0] = 7;
    msg[1] = TD_FMT_RGB565;
    for (int i = 0; i < 96 * 96; i++) {
        td_wr16(msg + 2 + 2 * i, (uint16_t)(i == 0 ? 0xF800 : 0x07E0));
    }
    CHECK(status(TD_CMD_SET_KEY_IMAGE, msg, 2 + 96 * 96 * 2) == TD_OK);
    int kx = 32 + 2 * 160, ky = 8 + 124;
    CHECK(px(kx, ky) == 0xF800 && px(kx + 95, ky + 95) == 0x07E0);
    CHECK(px(kx - 1, ky) != 0x07E0 && px(kx + 96, ky) != 0x07E0);

    CHECK(status(TD_CMD_SET_KEY_IMAGE, msg, 2 + 96 * 96 * 2 - 2) == TD_ERR_SIZE_MISMATCH);
    msg[0] = 15;
    CHECK(status(TD_CMD_SET_KEY_IMAGE, msg, 2 + 96 * 96 * 2) == TD_ERR_BAD_PARAM);
    msg[0] = 0;
    msg[1] = 9;
    CHECK(status(TD_CMD_SET_KEY_IMAGE, msg, 10) == TD_ERR_BAD_PARAM);

    /* JPEG key image: red with a white top-left quadrant */
    msg[0] = 14;
    msg[1] = TD_FMT_JPEG;
    memcpy(msg + 2, jpeg_key_96, sizeof(jpeg_key_96));
    CHECK(status(TD_CMD_SET_KEY_IMAGE, msg, 2 + sizeof(jpeg_key_96)) == TD_OK);
    kx = 32 + 4 * 160;
    ky = 8 + 2 * 124;
    uint16_t white = px(kx + 10, ky + 10), red = px(kx + 80, ky + 80);
    CHECK((white >> 11) >= 29 && ((white >> 5) & 63) >= 58 && (white & 31) >= 29);
    CHECK((red >> 11) >= 22 && ((red >> 5) & 63) <= 14 && (red & 31) <= 7);

    memcpy(msg + 2, jpeg_wrong_72, sizeof(jpeg_wrong_72));
    CHECK(status(TD_CMD_SET_KEY_IMAGE, msg, 2 + sizeof(jpeg_wrong_72)) == TD_ERR_SIZE_MISMATCH);
    memcpy(msg + 2, jpeg_progressive_96, sizeof(jpeg_progressive_96));
    CHECK(status(TD_CMD_SET_KEY_IMAGE, msg, 2 + sizeof(jpeg_progressive_96)) == TD_ERR_BAD_IMAGE);
    memcpy(msg + 2, jpeg_key_96, sizeof(jpeg_key_96));
    CHECK(status(TD_CMD_SET_KEY_IMAGE, msg, 2 + sizeof(jpeg_key_96) / 2) == TD_ERR_BAD_IMAGE);

    /* strip: JPEG into segment 2, then a full-width raw image */
    td_wr16(msg, 400); td_wr16(msg + 2, 0); td_wr16(msg + 4, 200); td_wr16(msg + 6, 100);
    msg[8] = TD_FMT_JPEG; msg[9] = 0;
    memcpy(msg + 10, jpeg_strip_200x100, sizeof(jpeg_strip_200x100));
    CHECK(status(TD_CMD_SET_STRIP_IMAGE, msg, 10 + sizeof(jpeg_strip_200x100)) == TD_OK);
    uint16_t blue = px(400 + 180, 380 + 90);
    CHECK((blue & 31) >= 25 && (blue >> 11) <= 5);
    td_wr16(msg, 700);
    CHECK(status(TD_CMD_SET_STRIP_IMAGE, msg, 10 + sizeof(jpeg_strip_200x100)) == TD_ERR_BAD_PARAM);

    td_wr16(msg, 0); td_wr16(msg + 4, 800);
    msg[8] = TD_FMT_RGB565;
    for (int i = 0; i < 800 * 100; i++) {
        td_wr16(msg + 10 + 2 * i, 0x1234);
    }
    CHECK(status(TD_CMD_SET_STRIP_IMAGE, msg, 10 + 800 * 100 * 2) == TD_OK);
    CHECK(px(0, 380) == 0x1234 && px(799, 479) == 0x1234 && px(0, 379) != 0x1234);

    /* fills */
    uint8_t fill[4] = {TD_FILL_KEY, 0, 0xAA, 0x55};
    CHECK(status(TD_CMD_FILL, fill, 4) == TD_OK && px(32, 8) == 0x55AA);
    fill[0] = TD_FILL_ALL_KEYS; fill[2] = 0x11; fill[3] = 0x11;
    CHECK(status(TD_CMD_FILL, fill, 4) == TD_OK && px(32 + 4 * 160 + 95, 8 + 2 * 124 + 95) == 0x1111 && px(0, 0) != 0x1111);
    fill[0] = TD_FILL_STRIP; fill[2] = 0x22; fill[3] = 0x22;
    CHECK(status(TD_CMD_FILL, fill, 4) == TD_OK && px(400, 400) == 0x2222);
    fill[0] = TD_FILL_ALL; fill[2] = 0; fill[3] = 0;
    CHECK(status(TD_CMD_FILL, fill, 4) == TD_OK && px(0, 0) == 0 && px(799, 479) == 0);
    fill[0] = TD_FILL_KEY; fill[1] = 15;
    CHECK(status(TD_CMD_FILL, fill, 4) == TD_ERR_BAD_PARAM);
}

static void test_brightness(void)
{
    fresh();
    CHECK(bl_on && bl_pct == 70);
    uint8_t fill[4] = {TD_FILL_ALL, 0, 0xFF, 0xFF};
    status(TD_CMD_FILL, fill, 4);
    uint8_t v = 100;
    CHECK(status(TD_CMD_SET_BRIGHTNESS, &v, 1) == TD_OK && bl_pct == 100 && scan[0] == 0xFFFF);
    v = 25;
    CHECK(status(TD_CMD_SET_BRIGHTNESS, &v, 1) == TD_OK && bl_pct == 25);
    /* software dimming: master untouched, scanned pixels darker but not black */
    CHECK(master[0] == 0xFFFF && scan[0] != 0xFFFF && (scan[0] >> 11) > 8 && (scan[0] >> 11) < 24);
    v = 101;
    CHECK(status(TD_CMD_SET_BRIGHTNESS, &v, 1) == TD_ERR_BAD_PARAM);
}

static void test_keys(void)
{
    fresh();
    /* 3 ms of bounce is ignored */
    td_dev_feed_keys(&dev, 1u << 3);
    advance(3);
    td_dev_feed_keys(&dev, 0);
    advance(20);
    CHECK(out_n == 0);

    td_dev_feed_keys(&dev, 1u << 3);
    advance(7);
    CHECK(out_n == 0);
    advance(2);
    CHECK(out_n == 1 && out[0].type == TD_EVT_KEY && out[0].payload[0] == 3 && out[0].payload[1] == 1);
    td_dev_feed_keys(&dev, (1u << 3) | (1u << 14));
    advance(10);
    CHECK(out_n == 2 && out[1].payload[0] == 14 && out[1].payload[1] == 1);
    CHECK((uint8_t)(out[1].seq - out[0].seq) == 1);
    td_dev_feed_keys(&dev, 0);
    advance(10);
    CHECK(out_n == 4 && out[2].payload[1] == 0 && out[3].payload[1] == 0);

    td_dev_feed_enc_buttons(&dev, 1u << 2);
    advance(10);
    CHECK(out_n == 5 && out[4].type == TD_EVT_ENC_BUTTON && out[4].payload[0] == 2 && out[4].payload[1] == 1);

    frame *f = request(TD_CMD_GET_STATE, NULL, 0);
    CHECK(f && f->type == TD_RSP_STATE && f->len == TD_STATE_LEN && f->payload[0] == 1 && f->payload[6] == 4);
}

static void turn(unsigned idx, int detents)
{
    static const uint8_t cw[4] = {2, 0, 1, 3}, ccw[4] = {1, 0, 2, 3};
    const uint8_t *s = detents >= 0 ? cw : ccw;
    for (int d = abs(detents); d > 0; d--) {
        for (int i = 0; i < 4; i++) {
            td_dev_feed_enc_ab(&dev, idx, s[i] & 2, s[i] & 1);
        }
    }
}

static void test_encoders(void)
{
    fresh();
    turn(1, 2);
    CHECK(out_n == 2 && out[0].type == TD_EVT_ENC_ROTATE && out[0].payload[0] == 1 && (int8_t)out[0].payload[1] == 1);
    turn(1, -1);
    CHECK(out_n == 3 && (int8_t)out[2].payload[1] == -1);

    /* contact bounce on one pin: no detent */
    out_n = 0;
    for (int i = 0; i < 20; i++) {
        td_dev_feed_enc_ab(&dev, 0, true, i & 1);
    }
    td_dev_feed_enc_ab(&dev, 0, true, true);
    CHECK(out_n == 0);
    turn(0, 1);
    CHECK(out_n == 1);

    /* inverted, two transitions per detent */
    uint8_t cfg[TD_CONFIG_LEN] = {TD_CONFIG_VERSION, 70, 0, 0, 0xF4, 0x01, 8, 2, 0x08, 0, 0, 0};
    CHECK(status(TD_CMD_SET_CONFIG, cfg, sizeof(cfg)) == TD_OK);
    out_n = 0;
    turn(3, 1);
    CHECK(out_n == 2 && (int8_t)out[0].payload[1] == -1);
}

static void test_touch(void)
{
    fresh();
    /* tap */
    td_dev_feed_touch(&dev, true, 300, 400);
    advance(80);
    td_dev_feed_touch(&dev, true, 305, 402);
    td_dev_feed_touch(&dev, false, 0, 0);
    CHECK(out_n == 1 && out[0].type == TD_EVT_TOUCH && out[0].payload[0] == TD_TOUCH_TAP);
    CHECK(td_rd16(out[0].payload + 1) == 300 && td_rd16(out[0].payload + 3) == 20);

    /* long press fires at the threshold, release adds nothing */
    out_n = 0;
    td_dev_feed_touch(&dev, true, 10, 479);
    advance(499);
    CHECK(out_n == 0);
    advance(2);
    CHECK(out_n == 1 && out[0].payload[0] == TD_TOUCH_LONG && td_rd16(out[0].payload + 3) == 99);
    advance(300);
    td_dev_feed_touch(&dev, false, 0, 0);
    CHECK(out_n == 1);

    /* drag reports start and end, clamped to the strip */
    out_n = 0;
    td_dev_feed_touch(&dev, true, 100, 430);
    advance(30);
    td_dev_feed_touch(&dev, true, 400, 300);
    advance(700);
    td_dev_feed_touch(&dev, false, 0, 0);
    CHECK(out_n == 1 && out[0].payload[0] == TD_TOUCH_DRAG);
    CHECK(td_rd16(out[0].payload + 1) == 100 && td_rd16(out[0].payload + 3) == 50);
    CHECK(td_rd16(out[0].payload + 5) == 400 && td_rd16(out[0].payload + 7) == 0);

    /* touches that start above the strip are ignored */
    out_n = 0;
    td_dev_feed_touch(&dev, true, 100, 100);
    td_dev_feed_touch(&dev, true, 100, 420);
    td_dev_feed_touch(&dev, false, 0, 0);
    CHECK(out_n == 0);
}

static void test_sleep(void)
{
    fresh();
    uint8_t cfg[TD_CONFIG_LEN] = {TD_CONFIG_VERSION, 60, 5, 0, 0xF4, 0x01, 8, 4, 0, TD_CFG_FLAG_SWALLOW_WAKE, 0, 0};
    CHECK(status(TD_CMD_SET_CONFIG, cfg, sizeof(cfg)) == TD_OK);
    out_n = 0;
    advance(4999);
    CHECK(out_n == 0 && bl_on);
    advance(2);
    CHECK(out_n == 1 && out[0].type == TD_EVT_POWER && out[0].payload[0] == 0 && !bl_on);

    /* the waking press and its release are swallowed */
    out_n = 0;
    td_dev_feed_keys(&dev, 1);
    advance(10);
    td_dev_feed_keys(&dev, 0);
    advance(10);
    CHECK(out_n == 1 && out[0].type == TD_EVT_POWER && out[0].payload[0] == 1 && bl_on);
    out_n = 0;
    td_dev_feed_keys(&dev, 1);
    advance(10);
    CHECK(out_n == 1 && out[0].type == TD_EVT_KEY);

    /* a held key keeps the deck awake */
    out_n = 0;
    advance(8000);
    CHECK(out_n == 0 && bl_on);
    td_dev_feed_keys(&dev, 0);
    advance(10);
    out_n = 0;
    advance(5000);
    CHECK(out_n == 1 && !bl_on);

    /* an encoder turn wakes without reporting; host commands work too */
    out_n = 0;
    turn(0, 1);
    CHECK(out_n == 1 && out[0].type == TD_EVT_POWER && bl_on);
    turn(0, 1);
    CHECK(out_n == 2 && out[1].type == TD_EVT_ENC_ROTATE);
    uint8_t v = 0;
    CHECK(status(TD_CMD_SET_POWER, &v, 1) == TD_OK && !bl_on);
    v = 1;
    CHECK(status(TD_CMD_SET_POWER, &v, 1) == TD_OK && bl_on);

    /* host suspend blanks without changing the awake state */
    td_dev_host_suspend(&dev, true);
    CHECK(!bl_on);
    td_dev_host_suspend(&dev, false);
    CHECK(bl_on);
}

static void test_config_and_splash(void)
{
    fresh();
    frame *f = request(TD_CMD_GET_CONFIG, NULL, 0);
    CHECK(f && f->type == TD_RSP_CONFIG && f->len == TD_CONFIG_LEN && f->payload[1] == 70 && f->payload[7] == 4);

    uint8_t cfg[TD_CONFIG_LEN] = {TD_CONFIG_VERSION, 35, 0x2C, 0x01, 0x20, 0x03, 12, 2, 5, 3, 0, 0};
    CHECK(status(TD_CMD_SET_CONFIG, cfg, sizeof(cfg)) == TD_OK);
    uint8_t bad[TD_CONFIG_LEN];
    memcpy(bad, cfg, sizeof(bad));
    bad[7] = 3;
    CHECK(status(TD_CMD_SET_CONFIG, bad, sizeof(bad)) == TD_ERR_BAD_PARAM);
    bad[7] = 2; bad[1] = 150;
    CHECK(status(TD_CMD_SET_CONFIG, bad, sizeof(bad)) == TD_ERR_BAD_PARAM);
    CHECK(status(TD_CMD_SET_CONFIG, cfg, 5) == TD_ERR_BAD_LENGTH);

    /* custom splash: paint, save, reboot */
    uint8_t fill[4] = {TD_FILL_ALL, 0, 0xCD, 0xAB};
    status(TD_CMD_FILL, fill, 4);
    CHECK(status(TD_CMD_SAVE_SPLASH, NULL, 0) == TD_OK);
    uint8_t mode = TD_REBOOT_BOOTLOADER;
    CHECK(status(TD_CMD_REBOOT, &mode, 1) == TD_OK && rebooted == TD_REBOOT_BOOTLOADER);

    start(); /* power cycle: config and splash survive */
    f = request(TD_CMD_GET_CONFIG, NULL, 0);
    CHECK(f && memcmp(f->payload, cfg, 10) == 0);
    CHECK(bl_pct == 35 && px(5, 5) == 0xABCD);

    /* host goes away: brightness and splash come back */
    uint8_t v = 90;
    status(TD_CMD_SET_BRIGHTNESS, &v, 1);
    fill[2] = fill[3] = 0;
    status(TD_CMD_FILL, fill, 4);
    td_dev_host_link(&dev, false);
    CHECK(bl_pct == 35 && px(5, 5) == 0xABCD);

    CHECK(status(TD_CMD_CLEAR_SPLASH, NULL, 0) == TD_OK);
    CHECK(status(TD_CMD_RESET_DISPLAY, NULL, 0) == TD_OK);
    CHECK(px(5, 5) != 0xABCD && px(32, 8) != px(5, 5));

    nv_fail = 1;
    CHECK(status(TD_CMD_SET_CONFIG, cfg, sizeof(cfg)) == TD_ERR_STORAGE);
}

/* Random and mutated input must never crash or read out of bounds (run under ASan). */
static void test_fuzz(void)
{
    fresh();
    static uint8_t msg[4096], framebuf[4200];
    srand(12345);
    for (int iter = 0; iter < 20000; iter++) {
        uint32_t len = (uint32_t)(rand() % 64);
        uint8_t type = (uint8_t)(rand() % 0x24);
        for (uint32_t i = 0; i < len; i++) {
            msg[i] = (uint8_t)rand();
        }
        if (type == TD_CMD_SET_KEY_IMAGE && (rand() & 1)) {
            /* a real JPEG with a few bytes flipped */
            msg[0] = (uint8_t)(rand() % 15);
            msg[1] = TD_FMT_JPEG;
            memcpy(msg + 2, jpeg_key_96, sizeof(jpeg_key_96));
            len = 2 + (uint32_t)sizeof(jpeg_key_96);
            for (int k = 0; k < 3; k++) {
                msg[2 + rand() % (int)sizeof(jpeg_key_96)] ^= (uint8_t)(1 << (rand() % 8));
            }
            if (rand() % 4 == 0) {
                len = 2 + (uint32_t)(rand() % (int)sizeof(jpeg_key_96));
            }
        }
        if (type == TD_CMD_REBOOT) {
            continue;
        }
        size_t n = td_frame_encode(framebuf, type, (uint8_t)iter, msg, len);
        if (rand() % 10 == 0) {
            framebuf[rand() % (int)n] ^= 0xFF; /* framing damage */
        }
        td_dev_rx(&dev, framebuf, n);
        clock_ms += 3;
        td_dev_feed_keys(&dev, (uint32_t)rand() & 0x7FFF);
        td_dev_feed_enc_ab(&dev, (unsigned)rand() % 4, rand() & 1, rand() & 1);
        td_dev_feed_touch(&dev, rand() & 1, rand() % 1000 - 100, rand() % 600 - 50);
        td_dev_tick(&dev);
    }
    /* still answers afterwards */
    clock_ms += 2000;
    td_dev_feed_keys(&dev, 0);
    frame *f = request(TD_CMD_GET_INFO, NULL, 0);
    CHECK(f && f->type == TD_RSP_INFO);
}

int main(void)
{
    test_crc();
    test_parser();
    test_info_and_ping();
    test_images();
    test_brightness();
    test_keys();
    test_encoders();
    test_touch();
    test_sleep();
    test_config_and_splash();
    test_fuzz();
    printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
