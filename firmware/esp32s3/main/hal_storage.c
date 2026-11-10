/* Persistent storage: the config in NVS, the custom splash in its own partition.
 * SPDX-License-Identifier: MIT */
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "hal.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "storage";
static const esp_partition_t *s_splash;

#define SPLASH_MAGIC 0x50534454u /* "TDSP" */
#define SPLASH_HEADER 16
#define CHUNK 4096

void hal_storage_init(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "NVS unavailable: %s", esp_err_to_name(err));
    }
    s_splash = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "splash");
}

bool hal_nv_load(void *ctx, uint8_t *data, size_t len)
{
    (void)ctx;
    nvs_handle_t h;
    if (nvs_open("tessera", NVS_READONLY, &h) != ESP_OK) {
        return false;
    }
    size_t got = len;
    bool ok = nvs_get_blob(h, "config", data, &got) == ESP_OK && got == len;
    nvs_close(h);
    return ok;
}

bool hal_nv_save(void *ctx, const uint8_t *data, size_t len)
{
    (void)ctx;
    nvs_handle_t h;
    if (nvs_open("tessera", NVS_READWRITE, &h) != ESP_OK) {
        return false;
    }
    bool ok = nvs_set_blob(h, "config", data, len) == ESP_OK && nvs_commit(h) == ESP_OK;
    nvs_close(h);
    return ok;
}

static size_t frame_bytes(void) { return (size_t)g_fb.w * g_fb.h * 2; }

/* Takes a few seconds: the flash has to be erased first. */
bool hal_splash_save(void *ctx)
{
    (void)ctx;
    size_t bytes = frame_bytes();
    if (!s_splash || SPLASH_HEADER + bytes > s_splash->size) {
        return false;
    }
    size_t erase = (SPLASH_HEADER + bytes + s_splash->erase_size - 1) / s_splash->erase_size * s_splash->erase_size;
    if (esp_partition_erase_range(s_splash, 0, erase) != ESP_OK) {
        return false;
    }
    /* Pixels first, header last: an interrupted save leaves no valid header. */
    const uint8_t *src = (const uint8_t *)g_fb.master;
    for (size_t off = 0; off < bytes; off += CHUNK) {
        size_t n = bytes - off < CHUNK ? bytes - off : CHUNK;
        if (esp_partition_write(s_splash, SPLASH_HEADER + off, src + off, n) != ESP_OK) {
            return false;
        }
    }
    uint8_t header[SPLASH_HEADER] = {0};
    td_wr32(header, SPLASH_MAGIC);
    td_wr16(header + 4, (uint16_t)g_fb.w);
    td_wr16(header + 6, (uint16_t)g_fb.h);
    td_wr32(header + 8, td_crc32(0, src, bytes));
    return esp_partition_write(s_splash, 0, header, sizeof(header)) == ESP_OK;
}

bool hal_splash_load(void *ctx)
{
    (void)ctx;
    uint8_t header[SPLASH_HEADER];
    size_t bytes = frame_bytes();
    if (!s_splash || esp_partition_read(s_splash, 0, header, sizeof(header)) != ESP_OK ||
        td_rd32(header) != SPLASH_MAGIC || td_rd16(header + 4) != g_fb.w || td_rd16(header + 6) != g_fb.h) {
        return false;
    }
    /* Read into the master framebuffer, check, then show. On a mismatch the
     * caller draws the built-in splash over it. */
    uint8_t *dst = (uint8_t *)g_fb.master;
    for (size_t off = 0; off < bytes; off += CHUNK) {
        size_t n = bytes - off < CHUNK ? bytes - off : CHUNK;
        if (esp_partition_read(s_splash, SPLASH_HEADER + off, dst + off, n) != ESP_OK) {
            return false;
        }
    }
    if (td_crc32(0, dst, bytes) != td_rd32(header + 8)) {
        return false;
    }
    td_fb_refresh(&g_fb);
    return true;
}

bool hal_splash_clear(void *ctx)
{
    (void)ctx;
    return s_splash && esp_partition_erase_range(s_splash, 0, s_splash->erase_size) == ESP_OK;
}
