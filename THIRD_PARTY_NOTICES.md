# Third-party notices

## Vendored: ZMK Studio protocol definitions

The protobuf files under `backend/proto/zmk/` are copied verbatim from
[zmkfirmware/zmk-studio-messages](https://github.com/zmkfirmware/zmk-studio-messages)
at the commit recorded in `backend/proto/PINNED_COMMIT.txt`. The generated
Python bindings under `backend/src/zmk_proto/` derive from them. They are
distributed under the MIT license:

> MIT License
>
> Copyright (c) 2024 The ZMK Contributors
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

## Build-time upstreams (nothing vendored)

The firmware sources this app generates are compiled (on GitHub Actions, in
the user's own repository) against:

- [zmkfirmware/zmk](https://github.com/zmkfirmware/zmk) — MIT,
  © The ZMK Contributors
- [Zephyr RTOS](https://github.com/zephyrproject-rtos/zephyr) — Apache-2.0
- [Cyboard-DigitalTailor/zmk-keyboards](https://github.com/Cyboard-DigitalTailor/zmk-keyboards)
  and [Cyboard-DigitalTailor/zmk-pmw3610-driver](https://github.com/Cyboard-DigitalTailor/zmk-pmw3610-driver)
  — board/shield definitions and trackball driver for the Cyboard Imprint,
  fetched by the build from their own repositories

No source from those projects is redistributed in this repository. If you
redistribute **built firmware binaries**, attach the ZMK MIT notice and the
Zephyr Apache-2.0 license and NOTICE contents to that distribution.

## Runtime dependencies

Python packages (see `backend/requirements.txt`) and Flutter/pub packages
(see `frontend/pubspec.yaml`) are installed from their respective registries
under their own permissive licenses (MIT/BSD/Apache/PSF/MPL-2.0); they are
not vendored here.
