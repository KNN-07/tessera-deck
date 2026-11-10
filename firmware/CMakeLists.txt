# Host build: the portable core, the simulator and the unit tests.
# The ESP32-S3 firmware is a separate ESP-IDF project in esp32s3/.
cmake_minimum_required(VERSION 3.16)
project(tessera_firmware_host C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

option(TESSERA_SANITIZE "Build with ASan and UBSan" OFF)
if(TESSERA_SANITIZE)
  add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer)
  add_link_options(-fsanitize=address,undefined)
endif()

add_library(tjpgd STATIC core/third_party/tjpgd/tjpgd.c)
target_include_directories(tjpgd PUBLIC core/third_party/tjpgd)

add_library(tessera_core STATIC
  core/td_frame.c
  core/td_device.c
  core/td_fb.c
  core/td_splash.c)
target_include_directories(tessera_core PUBLIC core)
target_link_libraries(tessera_core PUBLIC tjpgd m)
target_compile_options(tessera_core PRIVATE -Wall -Wextra -Wshadow -Wconversion -Wno-sign-conversion -Werror)

add_executable(tessera-sim sim/sim.c)
target_link_libraries(tessera-sim PRIVATE tessera_core)
target_compile_options(tessera-sim PRIVATE -Wall -Wextra)

enable_testing()
add_executable(test_core tests/test_core.c)
target_link_libraries(test_core PRIVATE tessera_core)
target_compile_options(test_core PRIVATE -Wall -Wextra)
add_test(NAME core COMMAND test_core)
