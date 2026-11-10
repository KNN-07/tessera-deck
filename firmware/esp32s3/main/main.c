/* Tessera Deck firmware entry point for the ESP32-S3.
 * SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <string.h>

#include "board.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "hal.h"

static const char *TAG = "tessera";

td_device g_dev;
td_fb g_fb;

static SemaphoreHandle_t s_lock;
static i2c_master_bus_handle_t s_i2c;

void hal_lock(void) { xSemaphoreTakeRecursive(s_lock, portMAX_DELAY); }
bool hal_try_lock(void) { return xSemaphoreTakeRecursive(s_lock, 0) == pdTRUE; }
void hal_unlock(void) { xSemaphoreGiveRecursive(s_lock); }

i2c_master_bus_handle_t hal_i2c_bus(void) { return s_i2c; }

bool hal_i2c_add(uint8_t addr, i2c_master_dev_handle_t *out)
{
    const i2c_device_config_t cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = addr,
        .scl_speed_hz = BOARD_I2C_HZ,
    };
    return i2c_master_bus_add_device(s_i2c, &cfg, out) == ESP_OK;
}

/* ---- td_port ----------------------------------------------------------- */

static void p_blit(void *c, int x, int y, int w, int h, const uint16_t *px) { (void)c; td_fb_blit(&g_fb, x, y, w, h, px); }
static void p_fill(void *c, int x, int y, int w, int h, uint16_t col) { (void)c; td_fb_fill(&g_fb, x, y, w, h, col); }

static void p_backlight(void *c, bool on, uint8_t pct)
{
    (void)c;
    td_fb_set_level(&g_fb, pct);
    hal_backlight_request(on);
}

static void p_tx(void *c, const uint8_t *data, size_t len) { (void)c; hal_usb_tx(data, len); }
static uint32_t p_millis(void *c) { (void)c; return (uint32_t)(esp_timer_get_time() / 1000); }
static void p_reboot(void *c, uint8_t mode) { (void)c; hal_request_reboot(mode); }

static void p_serial(void *c, char out[16])
{
    (void)c;
    uint8_t mac[6] = {0};
    char text[17];
    esp_efuse_mac_get_default(mac);
    snprintf(text, sizeof(text), "TD%02X%02X%02X%02X%02X%02X", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    memset(out, 0, 16);
    memcpy(out, text, strlen(text));
}

void app_main(void)
{
    s_lock = xSemaphoreCreateRecursiveMutex();

    const i2c_master_bus_config_t bus_cfg = {
        .i2c_port = -1,
        .sda_io_num = BOARD_I2C_SDA,
        .scl_io_num = BOARD_I2C_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_ERROR_CHECK(i2c_new_master_bus(&bus_cfg, &s_i2c));

    hal_storage_init();
    hal_display_init();

    const td_layout *layout = &td_layout_rev_a;
    size_t rx_cap = td_dev_rx_buffer_size(layout);
    uint8_t *rx = heap_caps_malloc(rx_cap, MALLOC_CAP_SPIRAM);
    ESP_ERROR_CHECK(rx ? ESP_OK : ESP_ERR_NO_MEM);

    const td_port port = {
        .blit = p_blit,
        .fill = p_fill,
        .set_backlight = p_backlight,
        .tx = p_tx,
        .millis = p_millis,
        .nv_load = hal_nv_load,
        .nv_save = hal_nv_save,
        .splash_save = hal_splash_save,
        .splash_load = hal_splash_load,
        .splash_clear = hal_splash_clear,
        .reboot = p_reboot,
        .serial = p_serial,
        .info_flags = TD_INFO_FLAG_TOUCH | TD_INFO_FLAG_SPLASH_STORE | (BOARD_SOFTWARE_DIMMING ? 0 : TD_INFO_FLAG_HW_BACKLIGHT),
    };

    hal_lock();
    td_dev_init(&g_dev, layout, &port, rx, rx_cap);
    td_dev_boot(&g_dev); /* draws the splash and asks for the backlight */
    hal_unlock();

    hal_usb_start();
    hal_inputs_start(); /* the input task also switches the backlight on */
    ESP_LOGI(TAG, "Tessera Deck %d.%d.%d ready", TD_FW_MAJOR, TD_FW_MINOR, TD_FW_PATCH);
}
