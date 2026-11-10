/* Framebuffer helper shared by ports: keeps the undimmed image ("master") and
 * the pixels actually scanned out ("scan"), so brightness can be changed in
 * software on boards whose backlight is only on/off.
 * SPDX-License-Identifier: MIT */
#ifndef TD_FB_H
#define TD_FB_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint16_t *master;
    uint16_t *scan; /* may equal master when software dimming is off */
    int w, h;
    bool sw_dim;
    uint8_t level; /* percent */
    uint8_t lut5[32];
    uint8_t lut6[64];
} td_fb;

void td_fb_init(td_fb *fb, uint16_t *master, uint16_t *scan, int w, int h, bool sw_dim);
void td_fb_blit(td_fb *fb, int x, int y, int w, int h, const uint16_t *px);
void td_fb_fill(td_fb *fb, int x, int y, int w, int h, uint16_t color);
/* Software brightness, 0..100. Re-renders scan from master. No-op without sw_dim. */
void td_fb_set_level(td_fb *fb, uint8_t pct);
/* Re-renders scan from master, e.g. after loading master from storage. */
void td_fb_refresh(td_fb *fb);

#endif
