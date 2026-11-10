# TJpgDec R0.03

Tiny JPEG decompressor, (C) ChaN 2021. Licence terms are in the header of
`tjpgd.c` (free for any use, keep the copyright notice).

`tjpgd.c` and `tjpgd.h` are copied unmodified from Espressif's
`idf-extra-components/esp_jpeg/tjpgd` (commit `c8bdc26d`), which adds an
optional default-Huffman-table path (Apache-2.0) that Tessera leaves disabled.
`tjpgdcnf.h` is Tessera's own configuration.
