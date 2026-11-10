set(CORE ${CMAKE_CURRENT_LIST_DIR}/../../core)

idf_component_register(
    SRCS
        main.c
        hal_display.c
        hal_inputs.c
        hal_usb.c
        hal_storage.c
        ${CORE}/td_frame.c
        ${CORE}/td_device.c
        ${CORE}/td_fb.c
        ${CORE}/td_splash.c
        ${CORE}/third_party/tjpgd/tjpgd.c
    INCLUDE_DIRS . ${CORE}
    PRIV_REQUIRES esp_lcd esp_driver_i2c esp_driver_gpio nvs_flash esp_partition esp_timer esp_hw_support
)

# The ESP32-S3 ROM contains an older TJpgDec with a different JDEC layout.
# Give our copy private names so the two can never be mixed up at link time.
target_compile_definitions(${COMPONENT_LIB} PRIVATE jd_prepare=td_jd_prepare jd_decomp=td_jd_decomp)
