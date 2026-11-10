/* Built-in boot splash, drawn with rectangle fills only.
 * SPDX-License-Identifier: MIT */
#include "td_device.h"

#define RGB565(r, g, b) ((uint16_t)((((r) & 0xF8) << 8) | (((g) & 0xFC) << 3) | ((b) >> 3)))

/* 5x7 glyphs, one byte per row, bit 4 = leftmost column */
static const uint8_t glyph_T[7] = {0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04};
static const uint8_t glyph_E[7] = {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F};
static const uint8_t glyph_S[7] = {0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E};
static const uint8_t glyph_R[7] = {0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11};
static const uint8_t glyph_A[7] = {0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11};

static void glyph(td_device *dev, const uint8_t *g, int x, int y, int s, uint16_t color)
{
    for (int r = 0; r < 7; r++) {
        for (int c = 0; c < 5; c++) {
            if (g[r] & (0x10 >> c)) {
                dev->port.fill(dev->port.ctx, x + c * s, y + r * s, s, s, color);
            }
        }
    }
}

void td_splash_draw(td_device *dev)
{
    const td_layout *l = dev->layout;
    const uint16_t bg = RGB565(10, 12, 16);
    dev->port.fill(dev->port.ctx, 0, 0, l->panel_w, l->panel_h, bg);

    /* One tile per key, shading from teal to amber across the grid. */
    unsigned n = (unsigned)l->rows * l->cols;
    for (unsigned k = 0; k < n; k++) {
        int x, y;
        td_dev_key_rect(l, k, &x, &y);
        unsigned t = n > 1 ? k * 255u / (n - 1) : 0;
        uint16_t edge = RGB565(20 + t * 200 / 255, 150 - t * 30 / 255, 150 - t * 120 / 255);
        dev->port.fill(dev->port.ctx, x, y, l->key_w, l->key_h, edge);
        dev->port.fill(dev->port.ctx, x + 3, y + 3, l->key_w - 6, l->key_h - 6, RGB565(22, 26, 32));
        int d = l->key_w / 4;
        dev->port.fill(dev->port.ctx, x + (l->key_w - d) / 2, y + (l->key_h - d) / 2, d, d, edge);
    }

    if (l->strip_w && l->strip_h >= 60) {
        static const uint8_t *const word[7] = {glyph_T, glyph_E, glyph_S, glyph_S, glyph_E, glyph_R, glyph_A};
        int s = (l->strip_h - 30) / 7;
        if (s > 8) {
            s = 8;
        }
        int adv = 7 * s, total = 7 * adv - 2 * s;
        int x = l->strip_x + (l->strip_w - total) / 2;
        int y = l->strip_y + (l->strip_h - 7 * s) / 2;
        dev->port.fill(dev->port.ctx, l->strip_x, l->strip_y, l->strip_w, l->strip_h, RGB565(16, 19, 24));
        for (int i = 0; i < 7; i++) {
            glyph(dev, word[i], x + i * adv, y, s, RGB565(225, 230, 235));
        }
    }
}
