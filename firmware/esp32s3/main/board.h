/* Board definition: Waveshare ESP32-S3-Touch-LCD-5 (800 x 480) plus the
 * Tessera key-frame and encoder boards on its I2C terminal.
 * Pin and timing values are from Waveshare's schematic and ESP-IDF demo.
 * SPDX-License-Identifier: MIT */
#ifndef BOARD_H
#define BOARD_H

#define BOARD_LCD_H_RES 800
#define BOARD_LCD_V_RES 480
#define BOARD_LCD_PCLK_HZ (16 * 1000 * 1000)
#define BOARD_LCD_HSYNC_PULSE 4
#define BOARD_LCD_HSYNC_BACK 8
#define BOARD_LCD_HSYNC_FRONT 8
#define BOARD_LCD_VSYNC_PULSE 4
#define BOARD_LCD_VSYNC_BACK 8
#define BOARD_LCD_VSYNC_FRONT 8
#define BOARD_LCD_PCLK_ACTIVE_NEG 1
#define BOARD_LCD_BOUNCE_LINES 10

#define BOARD_LCD_GPIO_VSYNC 3
#define BOARD_LCD_GPIO_HSYNC 46
#define BOARD_LCD_GPIO_DE 5
#define BOARD_LCD_GPIO_PCLK 7
/* D0..D15 = B3 B4 B5 B6 B7  G2 G3 G4 G5 G6 G7  R3 R4 R5 R6 R7 */
#define BOARD_LCD_GPIO_DATA {14, 38, 18, 17, 10, 39, 0, 45, 48, 47, 21, 1, 2, 42, 41, 40}

#define BOARD_I2C_SDA 8
#define BOARD_I2C_SCL 9
#define BOARD_I2C_HZ 400000
#define BOARD_TOUCH_IRQ_GPIO 4

/* CH422G I/O expander: not register-based, each function has its own address. */
#define BOARD_CH422G_ADDR_MODE 0x24
#define BOARD_CH422G_ADDR_OUT 0x38
#define BOARD_CH422G_MODE_OUTPUT 0x01
#define BOARD_EXIO_TOUCH_RST (1u << 1)
#define BOARD_EXIO_BACKLIGHT (1u << 2)
#define BOARD_EXIO_LCD_RST (1u << 3)
#define BOARD_EXIO_SD_CS (1u << 4)

#define BOARD_GT911_ADDR 0x5D
#define BOARD_GT911_ADDR_ALT 0x14

/* TCA9539 expanders on the Tessera boards. 0x74/0x75 stay clear of the
 * CH422G (0x20-0x27, 0x30-0x3F), the GT911 (0x5D) and the RTC (0x51).
 *   keys:     P00-P07 = keys 0-7, P10-P16 = keys 8-14
 *   encoders: P00/P01 = encoder 0 A/B ... P06/P07 = encoder 3 A/B, P10-P13 = push 0-3
 * Every input is pulled up and switches to ground. */
#define BOARD_TCA_KEYS_ADDR 0x74
#define BOARD_TCA_ENC_ADDR 0x75

/* The backlight of this board is on/off only, so brightness is done by
 * scaling pixel values (td_fb). A board with a PWM backlight would set this
 * to 0 and report TD_INFO_FLAG_HW_BACKLIGHT. */
#define BOARD_SOFTWARE_DIMMING 1

#endif
