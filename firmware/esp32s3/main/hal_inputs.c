/* Keys, encoders and touch: one 1 kHz task that owns the I2C bus.
 * SPDX-License-Identifier: MIT */
#include <stdatomic.h>

#include "board.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal.h"
#include "soc/rtc_cntl_reg.h"
#include "soc/soc.h"

static const char *TAG = "inputs";

#define ENC_BACKLOG 128
#define KEY_SCAN_DIV 4    /* keys every 4 ms */
#define TOUCH_SCAN_DIV 10 /* touch every 10 ms */
#define TICK_DIV 5
#define TOUCH_STALE_MS 300

static i2c_master_dev_handle_t s_keys, s_enc, s_touch;
static atomic_int s_reboot = -1;

void hal_request_reboot(uint8_t mode) { atomic_store(&s_reboot, mode); }

static bool probe_add(uint8_t addr, i2c_master_dev_handle_t *dev, const char *what)
{
    if (i2c_master_probe(hal_i2c_bus(), addr, 50) != ESP_OK || !hal_i2c_add(addr, dev)) {
        ESP_LOGE(TAG, "%s not found at I2C address 0x%02X", what, addr);
        *dev = NULL;
        return false;
    }
    return true;
}

/* TCA9539 input ports 0 and 1. Returns pressed/low bits as 1. */
static bool tca_read(i2c_master_dev_handle_t dev, uint16_t *pressed)
{
    const uint8_t reg = 0x00;
    uint8_t in[2];
    if (!dev || i2c_master_transmit_receive(dev, &reg, 1, in, 2, 10) != ESP_OK) {
        return false;
    }
    *pressed = (uint16_t)~(in[0] | (in[1] << 8));
    return true;
}

/* GT911: returns true when the controller had a new report. */
static bool gt911_read(bool *down, int *x, int *y)
{
    const uint8_t status_reg[2] = {0x81, 0x4E};
    uint8_t status;
    if (!s_touch || i2c_master_transmit_receive(s_touch, status_reg, 2, &status, 1, 10) != ESP_OK || !(status & 0x80)) {
        return false;
    }
    *down = (status & 0x0F) > 0;
    if (*down) {
        const uint8_t point_reg[2] = {0x81, 0x50};
        uint8_t p[4];
        if (i2c_master_transmit_receive(s_touch, point_reg, 2, p, 4, 10) != ESP_OK) {
            return false;
        }
        *x = p[0] | (p[1] << 8);
        *y = p[2] | (p[3] << 8);
    }
    const uint8_t clear[3] = {0x81, 0x4E, 0x00};
    i2c_master_transmit(s_touch, clear, sizeof(clear), 10);
    return true;
}

static void do_reboot(int mode)
{
    vTaskDelay(pdMS_TO_TICKS(100)); /* let the ACK leave */
    if (mode == TD_REBOOT_BOOTLOADER) {
        /* Next reset enters the ROM download mode (USB serial / DFU). */
        REG_WRITE(RTC_CNTL_OPTION1_REG, RTC_CNTL_FORCE_DOWNLOAD_BOOT);
    }
    esp_restart();
}

static void input_task(void *arg)
{
    (void)arg;
    /* Encoder samples wait here while another task holds the core lock (for
     * example during a JPEG decode), so no quadrature edge is lost. */
    static uint16_t backlog[ENC_BACKLOG];
    unsigned backlog_n = 0;
    uint16_t keys = 0;
    bool have_keys = false, have_touch = false, touch_down = false;
    int touch_x = 0, touch_y = 0;
    uint32_t touch_seen = 0;
    TickType_t last = xTaskGetTickCount();

    for (uint32_t tick = 0;; tick++) {
        vTaskDelayUntil(&last, 1);

        uint16_t enc;
        if (tca_read(s_enc, &enc)) {
            if (backlog_n == ENC_BACKLOG) {
                backlog_n = 0; /* stalled for 128 ms: start over */
            }
            backlog[backlog_n++] = enc;
        }
        if (tick % KEY_SCAN_DIV == 0) {
            have_keys |= tca_read(s_keys, &keys);
        }
        if (tick % TOUCH_SCAN_DIV == 0) {
            bool down;
            if (gt911_read(&down, &touch_x, &touch_y)) {
                touch_down = down;
                touch_seen = tick;
                have_touch = true;
            } else if (touch_down && tick - touch_seen > TOUCH_STALE_MS) {
                touch_down = false; /* the release report never came */
                have_touch = true;
            }
        }

        if (hal_try_lock()) {
            for (unsigned i = 0; i < backlog_n; i++) {
                /* pressed bit = pin low; the decoder wants pin levels */
                uint16_t level = (uint16_t)~backlog[i];
                for (unsigned e = 0; e < 4; e++) {
                    td_dev_feed_enc_ab(&g_dev, e, level & (1u << (2 * e)), level & (2u << (2 * e)));
                }
            }
            if (backlog_n) {
                td_dev_feed_enc_buttons(&g_dev, (uint8_t)((backlog[backlog_n - 1] >> 8) & 0x0F));
                backlog_n = 0;
            }
            if (have_keys) {
                td_dev_feed_keys(&g_dev, keys & 0x7FFF);
                have_keys = false;
            }
            if (have_touch) {
                td_dev_feed_touch(&g_dev, touch_down, touch_x, touch_y);
                have_touch = false;
            }
            if (tick % TICK_DIV == 0) {
                td_dev_tick(&g_dev);
            }
            hal_unlock();
        }

        hal_backlight_apply();
        int reboot = atomic_load(&s_reboot);
        if (reboot >= 0) {
            do_reboot(reboot);
        }
    }
}

void hal_inputs_start(void)
{
    probe_add(BOARD_TCA_KEYS_ADDR, &s_keys, "key expander");
    probe_add(BOARD_TCA_ENC_ADDR, &s_enc, "encoder expander");
    if (!probe_add(BOARD_GT911_ADDR, &s_touch, "touch controller")) {
        probe_add(BOARD_GT911_ADDR_ALT, &s_touch, "touch controller (alternate address)");
    }
    xTaskCreatePinnedToCore(input_task, "inputs", 4096, NULL, 6, NULL, 0);
}
