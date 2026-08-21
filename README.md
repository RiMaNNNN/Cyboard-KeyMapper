# KeyMapper

Local configuration app for the **Cyboard Imprint (wireless)** keyboard — an alternative to
Cyboard Studio built on the open [ZMK Studio protocol](https://zmk.dev/docs/development/studio-rpc-protocol),
plus everything Studio can't do: key→sequence mappings (macros), bulk operations, real
backups, built-in help, and a firmware pipeline that removes the A+F unlock ritual
permanently.

> **Unofficial project.** KeyMapper is an independent, community-developed, open-source
> tool. It is not affiliated with, endorsed by, or supported by Cyboard LLC. "Cyboard" and
> "Cyboard Imprint" are trademarks of their respective owner, used here only to identify
> the hardware this tool works with.

## Requirements

- **Windows, macOS, or Linux.** The UI is a browser page; the server is plain Python. On
  Windows the Bluetooth features use the native WinRT stack (tested daily on real hardware);
  on macOS and Linux they use the cross-platform [Bleak](https://github.com/hbldh/bleak)
  backend (CoreBluetooth / BlueZ) — code-complete but not yet validated on a physical
  keyboard, so feedback is welcome. The USB configuration link (pyserial) and the firmware
  pipeline are cross-platform.
- **Python 3.12 or newer** ([python.org](https://www.python.org/downloads/)) — needed once,
  for the setup below. After that, nothing has to be on your PATH.
- The **left** keyboard half connected over USB-C with a **data** cable.
- **Linux only:** your user must be allowed to open the USB serial device — usually
  `sudo usermod -aG dialout $USER` (log out and in) or an equivalent udev rule.

## One-time setup

From the repository root — Windows:

```bat
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

macOS / Linux:

```sh
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

This creates a private Python environment inside the `backend` folder — nothing is installed
system-wide, and deleting the folder removes everything. Windows-only Bluetooth packages are
skipped automatically on other platforms.

## Start

**Windows:** double-click `KeyMapper.bat` (repository root). Prefer to know what a script
does before running it? It is 40 lines of plain batch, readable in Notepad, and does exactly
this: reuse an already-running server if one answers on the configured port, otherwise start
the server minimized using the virtualenv's own Python (no PATH involved, no admin rights,
no downloads), open your browser at `http://127.0.0.1:8756`, and exit after the server shuts
itself down — which it does a few seconds after you close the last browser tab.

**macOS / Linux:** `./keymapper.sh` (same behavior; `chmod +x keymapper.sh` once if needed).

No launcher script at all, on any OS:

```sh
cd backend
.venv/bin/python -m src --open      # Windows: .venv\Scripts\python.exe -m src --open
```

`--open` makes the server open your browser once it is up; leave it off and browse to
`http://127.0.0.1:8756` yourself. Either way the server stops on its own a few seconds after
the last tab closes.

Demo mode without hardware: set `KEYMAPPER_FAKE_DEVICE=1` before starting the server.
Advanced: `KEYMAPPER_BLE_BACKEND=bleak|winrt` overrides the Bluetooth backend (the default
is WinRT on Windows, Bleak elsewhere). Note that forcing `bleak` **on Windows** only reaches
the keyboard while it is advertising (Bleak's WinRT wrapper cannot open a bonded, silent
device) — Linux's BlueZ connects to bonded devices by address without advertising, which is
why the Bleak backend is the right default there.

**Platform notes:** a few *sequence* features type keystrokes that only mean something on
the computer the keyboard is plugged into — Alt-code accent sequences and the Win+R
"open program/file" launcher target Windows hosts. Everything else (keymap editing, layers,
colors, trackballs, firmware builds) is host-independent.

## First session — rescue your layout (do this once, soon)

Your layout currently exists **only inside the keyboard** (the `CURRENT.UF2` files copied from
the bootloader drive do *not* contain it — that flash region isn't exposed over USB).

1. Plug the **left** half into the PC with a USB-C **data** cable (the port closest to you).
2. Open KeyMapper. It finds and connects to the keyboard automatically.
3. The Home screen shows a one-time unlock banner: hold the two left-half home-row keys at the
   factory **A** and **F** positions for ~3 seconds (works regardless of your remapping — the
   combo reads physical positions).
4. Click **Back up now** (Backups tab). Your full keymap, layouts, and behavior catalog are saved to
   `backend/data/backups/keymap_backup_<UTC>.json`.

From then on KeyMapper re-backs-up automatically before every save, reset, and firmware build.

## Screens

- **Home** — status dashboard: connection state and battery gauges for both halves.
- **Editor** — full keymap editing: layer list (add/rename/reorder/disable/restore), the board
  drawn from the keyboard's own geometry, click a key to change its binding (behavior picker is
  driven by the keyboard's own metadata), **Set all to… / All TRANSPARENT / All NONE** bulk
  operations, Save/Discard with a live unsaved indicator.
- **Sequences** — compose macros with unlimited "Add behavior" steps. Presets included for
  **€ è é à ì ù ò** (Windows Alt-codes; Num Lock must be ON) and you can build
  layer-switch+underglow-color sequences (e.g. `&to 1` + `&rgb_ug RGB_COLOR_HSB(60,100,100)`
  = go to layer 1 and turn both halves yellow).
- **Advanced** — Studio locking on/off (off by default — no unlock ritual; keep it on and the
  physical A+F unlock is required after every restart), per-ball trackball behavior (cursor /
  vertical scroll / horizontal scroll / disabled, speed, scroll direction — applied LIVE over
  Bluetooth on firmware with the config channel; sensor responsiveness is a build option),
  Power & idle (idle timeout, deep sleep, underglow auto-off), the layer colors from your
  sequences with a visual HSB picker, and the low-battery blink alert. Everything except the
  live trackball settings takes effect at the next firmware build + flash.
- **Firmware** — the wizard that makes sequences real: it bakes your current layout + staged
  sequences and every Advanced-tab setting into a custom firmware config, builds it on GitHub
  Actions via Cyboard's official template (needs `gh` CLI logged in), detects the ASSIMILATOR
  bootloader drive, flashes the right then the left half, and finishes automatically after the
  left flash (clears the old stored layout that would
  otherwise shadow the new keymap). By default KeyMapper firmware sets
  `CONFIG_ZMK_STUDIO_LOCKING=n`: the unlock combo is gone forever.
- **Backups** — list, create, and inspect backup bundles.
- **Help** — glossary: Transparent vs None, To vs Toggle layer, sticky keys, macros, and more.

## Things worth knowing

- Only the **left** half speaks the configuration protocol (it's the split "central").
- Reading or editing the keymap requires the keyboard to be unlocked; on stock firmware that's
  the physical A+F hold. There is no software bypass — it's a ZMK security design. KeyMapper-built
  firmware disables locking by default (re-enable it in the Advanced tab if you want the stock
  behavior back).
- Sequences/macros are compile-time in ZMK. Live editing covers everything else; macros go
  through the Firmware wizard once, then behave like normal behaviors in the Editor.
- Cyboard's Studio trackball config panel talks a private protocol extension that only exists in
  their stock firmware. KeyMapper ships its own equivalent: per-ball mode (cursor / vertical
  scroll / horizontal scroll / disabled), speed, and scroll direction are set in the Advanced tab
  (or by clicking the ball icons in the Editor) and — on firmware with KeyMapper's live config
  channel — apply instantly over Bluetooth and persist on the keyboard, no rebuild. Sensor
  responsiveness and the per-side Installed switches are build options. Defaults match the
  template: left ball scrolls, right ball moves the cursor.
- `settings_reset.uf2` from Cyboard **erases your stored layout** (their docs say otherwise —
  verified against the firmware source). KeyMapper never uses it; its reset flows back up first.

## Development

- Backend: Python 3.13, FastAPI. `cd backend && .venv\Scripts\python.exe -m pytest tests -q`
- Frontend: Flutter web. `cd frontend && flutter build web --pwa-strategy=none`
- Protocol bindings are generated from the pinned, MIT-licensed `zmk-studio-messages`
  protos in `backend/proto/` (see `PINNED_COMMIT.txt` and `THIRD_PARTY_NOTICES.md`).

## License

KeyMapper is released under the [MIT license](LICENSE). Vendored and build-time
third-party components are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
