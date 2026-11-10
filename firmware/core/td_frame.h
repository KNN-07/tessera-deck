/* TDP framing: CRC32, frame encoder and a streaming parser.
 * SPDX-License-Identifier: MIT */
#ifndef TD_FRAME_H
#define TD_FRAME_H

#include <stddef.h>
#include <stdint.h>

#include "td_proto.h"

uint32_t td_crc32(uint32_t crc, const uint8_t *data, size_t len);

/* Writes a complete frame into out (needs len + TD_FRAME_OVERHEAD bytes).
 * Returns the number of bytes written. */
size_t td_frame_encode(uint8_t *out, uint8_t type, uint8_t seq, const uint8_t *payload, uint32_t len);

typedef void (*td_frame_cb)(void *ctx, uint8_t type, uint8_t seq, const uint8_t *payload, uint32_t len);

typedef struct {
    uint8_t *buf;        /* holds one payload + crc */
    uint32_t cap;        /* capacity of buf */
    uint32_t max_payload;
    uint8_t state;
    uint8_t hdr[6];      /* type, seq, len */
    uint8_t hdr_have;
    uint32_t need;       /* payload + crc bytes expected */
    uint32_t have;
    uint32_t last_rx_ms;
    uint16_t errors;     /* dropped frames: bad crc, oversize, timeout */
} td_parser;

/* Bytes of a partial frame older than this are discarded. */
#define TD_FRAME_TIMEOUT_MS 1000

void td_parser_init(td_parser *p, uint8_t *buf, uint32_t cap);
void td_parser_reset(td_parser *p);
void td_parser_feed(td_parser *p, const uint8_t *data, size_t n, uint32_t now_ms, td_frame_cb cb, void *ctx);

static inline uint16_t td_rd16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static inline uint32_t td_rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static inline void td_wr16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static inline void td_wr32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}

#endif
