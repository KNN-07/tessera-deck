/* RGB panel, framebuffers and backlight for the Waveshare ESP32-S3-Touch-LCD-5.
 * SPDX-License-Identifier: MIT */
#include <stdatomic.h>

#include "board.h"
#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_rgb.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal.h"

static const char *TAG = "display";

static i2c_master_dev_handle_t s_ch_mode, s_ch_out;
static uint8_t s_exio;
static atomic_bool s_bl_want;
static bool s_bl_is;

static void exio_write(uint8_t value)
{
    s_exio = value;
    esp_err_t err = i2c_master_transmit(s_ch_out, &value, 1, 100);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "CH422G write failed: %s", esp_err_to_name(err));
    }
}

void hal_backlight_request(bool on) { atomic_store(&s_bl_want, on); }

void hal_backlight_apply(void)
{
    bool want = atomic_load(&s_bl_want);
    if (want != s_bl_is) {
        s_bl_is = want;
        exio_write(want ? (s_exio | BOARD_EXIO_BACKLIGHT) : (s_exio & (uint8_t)~BOARD_EXIO_BACKLIGHT));
    }
}

void hal_display_init(void)
{
    ESP_ERROR_CHECK(hal_i2c_add(BOARD_CH422G_ADDR_MODE, &s_ch_mode) ? ESP_OK : ESP_FAIL);
    ESP_ERROR_CHECK(hal_i2c_add(BOARD_CH422G_ADDR_OUT, &s_ch_out) ? ESP_OK : ESP_FAIL);
    const uint8_t mode = BOARD_CH422G_MODE_OUTPUT;
    ESP_ERROR_CHECK(i2c_master_transmit(s_ch_mode, &mode, 1, 100));

    /* Reset the panel and the touch controller with the backlight off. Holding
     * the touch interrupt line low while its reset is released selects I2C
     * address 0x5D on the GT911. */
    const gpio_config_t irq_out = {.pin_bit_mask = 1ULL << BOARD_TOUCH_IRQ_GPIO, .mode = GPIO_MODE_OUTPUT};
    gpio_config(&irq_out);
    gpio_set_level(BOARD_TOUCH_IRQ_GPIO, 0);
    exio_write(BOARD_EXIO_SD_CS);
    vTaskDelay(pdMS_TO_TICKS(20));
    exio_write(BOARD_EXIO_SD_CS | BOARD_EXIO_LCD_RST);
    vTaskDelay(pdMS_TO_TICKS(100));
    exio_write(BOARD_EXIO_SD_CS | BOARD_EXIO_LCD_RST | BOARD_EXIO_TOUCH_RST);
    vTaskDelay(pdMS_TO_TICKS(200));
    gpio_set_direction(BOARD_TOUCH_IRQ_GPIO, GPIO_MODE_INPUT);

    const esp_lcd_rgb_panel_config_t cfg = {
        .clk_src = LCD_CLK_SRC_DEFAULT,
        .timings = {
            .pclk_hz = BOARD_LCD_PCLK_HZ,
            .h_res = BOARD_LCD_H_RES,
            .v_res = BOARD_LCD_V_RES,
            .hsync_pulse_width = BOARD_LCD_HSYNC_PULSE,
            .hsync_back_porch = BOARD_LCD_HSYNC_BACK,
            .hsync_front_porch = BOARD_LCD_HSYNC_FRONT,
            .vsync_pulse_width = BOARD_LCD_VSYNC_PULSE,
            .vsync_back_porch = BOARD_LCD_VSYNC_BACK,
            .vsync_front_porch = BOARD_LCD_VSYNC_FRONT,
            .flags.pclk_active_neg = BOARD_LCD_PCLK_ACTIVE_NEG,
        },
        .data_width = 16,
        .bits_per_pixel = 16,
        .num_fbs = 1,
        /* With bounce buffers the driver copies each band of lines out of the
         * PSRAM framebuffer just before it is sent, so drawing straight into
         * the framebuffer is safe and needs no cache flushing. */
        .bounce_buffer_size_px = BOARD_LCD_H_RES * BOARD_LCD_BOUNCE_LINES,
        .hsync_gpio_num = BOARD_LCD_GPIO_HSYNC,
        .vsync_gpio_num = BOARD_LCD_GPIO_VSYNC,
        .de_gpio_num = BOARD_LCD_GPIO_DE,
        .pclk_gpio_num = BOARD_LCD_GPIO_PCLK,
        .disp_gpio_num = -1,
        .data_gpio_nums = BOARD_LCD_GPIO_DATA,
        .flags.fb_in_psram = 1,
    };
    esp_lcd_panel_handle_t panel = NULL;
    ESP_ERROR_CHECK(esp_lcd_new_rgb_panel(&cfg, &panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(panel));

    void *scan = NULL;
    ESP_ERROR_CHECK(esp_lcd_rgb_panel_get_frame_buffer(panel, 1, &scan));
    size_t bytes = (size_t)BOARD_LCD_H_RES * BOARD_LCD_V_RES * 2;
    uint16_t *master = BOARD_SOFTWARE_DIMMING ? heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM) : scan;
    ESP_ERROR_CHECK(master ? ESP_OK : ESP_ERR_NO_MEM);
    td_fb_init(&g_fb, master, scan, BOARD_LCD_H_RES, BOARD_LCD_V_RES, BOARD_SOFTWARE_DIMMING);
}
