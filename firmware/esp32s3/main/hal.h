/* Glue between the portable core and the ESP32-S3 board.
 * SPDX-License-Identifier: MIT */
#ifndef HAL_H
#define HAL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "driver/i2c_master.h"
#include "td_device.h"
#include "td_fb.h"

extern td_device g_dev;
extern td_fb g_fb;

/* One lock serialises every call into the core. */
void hal_lock(void);
bool hal_try_lock(void);
void hal_unlock(void);

/* I2C bus shared by the on-board chips and the Tessera boards. After start-up
 * only the input task touches it. */
i2c_master_bus_handle_t hal_i2c_bus(void);
bool hal_i2c_add(uint8_t addr, i2c_master_dev_handle_t *out);

void hal_display_init(void);
/* Called from the core (any task): only records what is wanted. */
void hal_backlight_request(bool on);
/* Called from the input task: performs the I2C write if something changed. */
void hal_backlight_apply(void);

void hal_inputs_start(void);
void hal_request_reboot(uint8_t mode);

void hal_usb_start(void);
void hal_usb_tx(const uint8_t *data, size_t len);

void hal_storage_init(void);
bool hal_nv_load(void *ctx, uint8_t *data, size_t len);
bool hal_nv_save(void *ctx, const uint8_t *data, size_t len);
bool hal_splash_save(void *ctx);
bool hal_splash_load(void *ctx);
bool hal_splash_clear(void *ctx);

#endif
