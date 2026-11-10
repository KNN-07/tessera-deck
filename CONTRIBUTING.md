# Contributing

Issues and pull requests are welcome. What helps most right now is listed in
the README under "Status".

## Ground rules

- **One source of truth per fact.** Pixel layout lives in
  `firmware/core/td_device.c`; millimetres in `hardware/mech/geometry.py`;
  wire constants in `firmware/core/td_proto.h` with `docs/protocol.md` as the
  normative text. The generators assert that these agree; keep it that way.
- **Protocol changes** go into `docs/protocol.md` in the same pull request.
  Additions bump the minor version; anything that breaks an existing host
  bumps the major version.
- **Run the checks** before sending a pull request (see `docs/building.md`).
  CI runs the same ones.
- **Generated hardware files** in `hardware/out` and `hardware/pcb` are committed so people can
  fetch STLs without installing CadQuery. Regenerate them when you change a
  generator, and commit both.
- Firmware core code is C11, no dynamic allocation, no hardware includes, and
  must build with `-Wall -Wextra -Wshadow -Wconversion -Werror`.

## Reporting a build

If you build a deck, please open an issue with photos and what you measured,
above all the position of the pixel area in the display window and the feel
of the keys. Those numbers decide rev B.

By contributing you agree that your contribution is licensed under the
licence of the part it touches (see `LICENSES.md`).
