/* USB CDC-ACM transport for the Tessera Deck Protocol.
 * SPDX-License-Identifier: MIT */
#include <stdatomic.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal.h"
#include "tinyusb.h"
#include "tinyusb_cdc_acm.h"
#include "tinyusb_default_config.h"
#include "tusb.h"

#define ITF TINYUSB_CDC_ACM_0

static TaskHandle_t s_task;
static atomic_bool s_dtr, s_suspended;

static void on_rx(int itf, cdcacm_event_t *event)
{
    (void)itf;
    (void)event;
    xTaskNotifyGive(s_task);
}

static void on_line_state(int itf, cdcacm_event_t *event)
{
    (void)itf;
    atomic_store(&s_dtr, event->line_state_changed_data.dtr);
    xTaskNotifyGive(s_task);
}

static void on_usb_event(tinyusb_event_t *event, void *arg)
{
    (void)arg;
    switch (event->id) {
    case TINYUSB_EVENT_SUSPENDED:
        atomic_store(&s_suspended, true);
        break;
    case TINYUSB_EVENT_RESUMED:
    case TINYUSB_EVENT_ATTACHED:
        atomic_store(&s_suspended, false);
        break;
    case TINYUSB_EVENT_DETACHED:
        atomic_store(&s_dtr, false);
        break;
    default:
        break;
    }
    if (s_task) {
        xTaskNotifyGive(s_task);
    }
}

void hal_usb_tx(const uint8_t *data, size_t len)
{
    bool is_event = len > 2 && data[2] >= TD_EVT_KEY;
    if (is_event) {
        /* Input events are only worth sending to a host that is listening,
         * and never in pieces. */
        if (!atomic_load(&s_dtr) || tud_cdc_n_write_available(ITF) < len) {
            return;
        }
    }
    for (int tries = 0; len && tries < 20; tries++) {
        size_t n = tinyusb_cdcacm_write_queue(ITF, data, len);
        data += n;
        len -= n;
        tinyusb_cdcacm_write_flush(ITF, len ? pdMS_TO_TICKS(5) : 0);
    }
}

static void proto_task(void *arg)
{
    (void)arg;
    static uint8_t buf[4096];
    bool link = false, suspended = false;

    for (;;) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(100));

        bool dtr = atomic_load(&s_dtr), susp = atomic_load(&s_suspended);
        if (dtr != link || susp != suspended) {
            hal_lock();
            if (susp != suspended) {
                td_dev_host_suspend(&g_dev, susp);
            }
            if (dtr != link) {
                td_dev_host_link(&g_dev, dtr);
            }
            hal_unlock();
            link = dtr;
            suspended = susp;
        }

        for (;;) {
            size_t n = 0;
            if (tinyusb_cdcacm_read(ITF, buf, sizeof(buf), &n) != ESP_OK || n == 0) {
                break;
            }
            hal_lock();
            td_dev_rx(&g_dev, buf, n);
            hal_unlock();
        }
    }
}

void hal_usb_start(void)
{
    xTaskCreatePinnedToCore(proto_task, "proto", 8192, NULL, 5, &s_task, 1);

    /* VID, PID and strings come from sdkconfig (CONFIG_TINYUSB_DESC_*); the
     * USB serial number is derived from the chip's MAC address. */
    const tinyusb_config_t usb_cfg = TINYUSB_DEFAULT_CONFIG(on_usb_event);
    ESP_ERROR_CHECK(tinyusb_driver_install(&usb_cfg));

    const tinyusb_config_cdcacm_t cdc_cfg = {
        .cdc_port = ITF,
        .callback_rx = on_rx,
        .callback_line_state_changed = on_line_state,
    };
    ESP_ERROR_CHECK(tinyusb_cdcacm_init(&cdc_cfg));
}
