/* SPDX-License-Identifier: MIT */
#include "td_fb.h"

#include <math.h>
#include <string.h>

static inline uint16_t dim(const td_fb *fb, uint16_t c)
{
    return (uint16_t)((fb->lut5[c >> 11] << 11) | (fb->lut6[(c >> 5) & 63] << 5) | fb->lut5[c & 31]);
}

static void build_lut(td_fb *fb)
{
    /* The panel is roughly gamma 2.2, so a luminance ratio L needs code values
     * scaled by L^(1/2.2). */
    float f = powf((float)fb->level / 100.0f, 1.0f / 2.2f);
    for (int i = 0; i < 32; i++) {
        fb->lut5[i] = (uint8_t)((float)i * f + 0.5f);
    }
    for (int i = 0; i < 64; i++) {
        fb->lut6[i] = (uint8_t)((float)i * f + 0.5f);
    }
}

void td_fb_init(td_fb *fb, uint16_t *master, uint16_t *scan, int w, int h, bool sw_dim)
{
    fb->master = master;
    fb->scan = scan;
    fb->w = w;
    fb->h = h;
    fb->sw_dim = sw_dim && scan != master;
    fb->level = 100;
    build_lut(fb);
    memset(master, 0, (size_t)w * h * 2);
    if (scan != master) {
        memset(scan, 0, (size_t)w * h * 2);
    }
}

static bool clip(const td_fb *fb, int *x, int *y, int *w, int *h, int *sx, int *sy)
{
    *sx = *sy = 0;
    if (*x < 0) { *sx = -*x; *w += *x; *x = 0; }
    if (*y < 0) { *sy = -*y; *h += *y; *y = 0; }
    if (*x + *w > fb->w) { *w = fb->w - *x; }
    if (*y + *h > fb->h) { *h = fb->h - *y; }
    return *w > 0 && *h > 0;
}

static void render_rows(td_fb *fb, int x, int y, int w, int h)
{
    if (fb->scan == fb->master) {
        return;
    }
    for (int r = 0; r < h; r++) {
        const uint16_t *s = fb->master + (size_t)(y + r) * fb->w + x;
        uint16_t *d = fb->scan + (size_t)(y + r) * fb->w + x;
        if (fb->sw_dim && fb->level < 100) {
            for (int i = 0; i < w; i++) {
                d[i] = dim(fb, s[i]);
            }
        } else {
            memcpy(d, s, (size_t)w * 2);
        }
    }
}

void td_fb_blit(td_fb *fb, int x, int y, int w, int h, const uint16_t *px)
{
    int src_w = w, sx, sy;
    if (!clip(fb, &x, &y, &w, &h, &sx, &sy)) {
        return;
    }
    for (int r = 0; r < h; r++) {
        memcpy(fb->master + (size_t)(y + r) * fb->w + x, px + (size_t)(sy + r) * src_w + sx, (size_t)w * 2);
    }
    render_rows(fb, x, y, w, h);
}

void td_fb_fill(td_fb *fb, int x, int y, int w, int h, uint16_t color)
{
    int sx, sy;
    if (!clip(fb, &x, &y, &w, &h, &sx, &sy)) {
        return;
    }
    for (int r = 0; r < h; r++) {
        uint16_t *d = fb->master + (size_t)(y + r) * fb->w + x;
        for (int i = 0; i < w; i++) {
            d[i] = color;
        }
    }
    render_rows(fb, x, y, w, h);
}

void td_fb_refresh(td_fb *fb) { render_rows(fb, 0, 0, fb->w, fb->h); }

void td_fb_set_level(td_fb *fb, uint8_t pct)
{
    if (pct > 100) {
        pct = 100;
    }
    if (!fb->sw_dim || pct == fb->level) {
        return;
    }
    fb->level = pct;
    build_lut(fb);
    td_fb_refresh(fb);
}
