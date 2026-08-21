/// Help: glossary of behaviors and concepts with use cases.
library;

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

/// One glossary entry.
class _Entry {
  const _Entry(this.term, this.definition);

  final String term;
  final String definition;
}

const List<_Entry> _glossary = [
  _Entry(
    'Layer',
    'A full set of key meanings. Layers stack: the highest active layer wins '
        'for each key, and Transparent keys fall through to the layer below. '
        'Layer 0 (Base) is always active.',
  ),
  _Entry(
    'Transparent (▽, &trans)',
    'The key does nothing ON THIS LAYER and passes the press down to the next '
        'active layer below. Use it on higher layers for keys that should keep '
        'their Base meaning. On Base itself it behaves like None (there is '
        'nothing below to fall through to).',
  ),
  _Entry(
    'None (∅, &none)',
    'The key swallows the press completely — nothing happens and lower layers '
        'do NOT see it. Use it to explicitly disable a key on a layer.',
  ),
  _Entry(
    'Key Press (&kp)',
    'Sends one ordinary key (letter, digit, modifier, F-key, media key). '
        'Modifiers can be embedded: LS(A) is Shift+A on one key.',
  ),
  _Entry(
    'Momentary Layer (MO, &mo)',
    'The layer is active only while the key is HELD — like Fn on a laptop. '
        'Release the key and you are back where you were.',
  ),
  _Entry(
    'To Layer (TO, &to)',
    'Switches to the layer and STAYS there (it also turns off all other '
        'layers except Base). Use it for mode switches, e.g. a gaming layer. '
        'Put a "To Layer 0" key on the target layer to get back!',
  ),
  _Entry(
    'Toggle Layer (TOG, &tog)',
    'Turns one layer on if it is off, and off if it is on — without touching '
        'other layers. Difference vs To Layer: Toggle stacks the layer on top '
        'of whatever is active; To Layer replaces the active set.',
  ),
  _Entry(
    'Sticky Layer (SL, &sl)',
    'Activates the layer for exactly the NEXT key press, then drops it '
        '(one-shot). Great for accents or symbols you type occasionally.',
  ),
  _Entry(
    'Sticky Key (&sk)',
    'One-shot modifier: press Shift-sticky, then A — you get a capital A '
        'without chording.',
  ),
  _Entry(
    'Mod-Tap (&mt)',
    'Hold = modifier, tap = normal key. Classic example: Ctrl when held, '
        'Escape when tapped.',
  ),
  _Entry(
    'Layer-Tap (&lt)',
    'Hold = momentary layer, tap = normal key. Example: hold Space for the '
        'navigation layer, tap for a space.',
  ),
  _Entry(
    'App Launch keys (AL)',
    'Standard HID codes that ask the operating system to open a program: '
        'calculator, internet browser, email, file manager, lock screen, and '
        'so on. Found in the Key Press picker under the "App Launch" '
        'category. What actually opens is decided by Windows — the keyboard '
        'only sends the standardized request.',
  ),
  _Entry(
    'App Control keys (AC)',
    'Standard HID codes that act inside a program: Copy, Paste, Undo, Find, '
        'browser Back/Forward/Refresh/Home, Zoom, Scroll and more. They go to '
        'the KEYBOARD-FOCUSED (active) window — the mouse position is '
        'irrelevant — and they are requests: the app handles the commands it '
        'understands (Copy/Paste/Undo and browser navigation are near-'
        'universal) and silently ignores the rest; some are picked up by '
        'Windows itself. Found in the Key Press picker under "App Control".',
  ),
  _Entry(
    'Open program / file (launcher sequence)',
    'A keyboard cannot carry a file path in a keycode, so KeyMapper builds '
        'launcher sequences instead: the key taps Win+R, types your path or '
        'URL, and presses Enter — Windows then opens it exactly as if you '
        'double-clicked it. Create one with "Open program / file…" in the '
        'Sequences tab (US-QWERTY Windows layout assumed for the typing).',
  ),
  _Entry(
    'Assign by pressing a key (capture)',
    'Super Fast Assign (Editor tab) and the "Press a key…" button in the '
        'binding dialog listen to TWO keyboards. A press on this computer\'s '
        'keyboard assigns that plain keycode. A press on the keyboard itself '
        'copies that key\'s FULL binding from the keyboard\'s ACTIVE layer '
        'at press time (exactly what the key does right now — so after '
        'switching to layer 1, the same key captures its layer-1 binding) — '
        'sequences, layer switches, underglow commands, anything a '
        'normal keyboard cannot express. The keyboard reports which physical '
        'key was pressed over the Bluetooth bond (KeyMapper-built firmware '
        'required — flash the latest build once; current builds PUSH every '
        'press instantly, so fast hover-press-hover workflows pair '
        'correctly). Note the pressed key still performs its action while '
        'you capture: a "To Layer" key really switches the keyboard\'s '
        'layer, and a sequence really types.',
  ),
  _Entry(
    'Sequence / Macro',
    'One key press runs several behaviors in order: type è via Alt+0232, '
        'switch layer AND set the underglow color, or any chain you compose in '
        'the Sequences tab. Sequences are compiled into the firmware (that is '
        'a ZMK architecture rule), so add them there once and flash — then '
        'assign them to keys like any behavior. Alt-code sequences manage '
        'Num Lock automatically (on when needed, restored afterwards).',
  ),
  _Entry(
    'RGB Save/Restore (&rgb_mem) and NumLock Guard (&nl_guard)',
    'Two KeyMapper-firmware behaviors, directly assignable from the '
        'Editor\'s picker on current builds. RGB Save/Restore: press '
        'memorizes the underglow color and on/off state, release restores '
        'them (the Hold-layer color presets wrap themselves in it). NumLock '
        'Guard: while held, forces the host\'s Num Lock ON and restores it '
        'on release — every Alt-code sequence is wrapped in it '
        'automatically, so you rarely need it on a key yourself.',
  ),
  _Entry(
    'Underglow (&rgb_ug)',
    'Controls the RGB LEDs: on/off, color, brightness, effects. Underglow '
        'commands are GLOBAL on a split keyboard — both halves follow. The '
        'chosen color persists across restarts.',
  ),
  _Entry(
    'Trackballs (mode, speed, responsiveness, wake)',
    'Each ball can move the cursor, scroll vertically, scroll horizontally, '
        'or be disabled — with its own speed and scroll direction, plus '
        'shared sensor timing: Responsiveness (minimum ms between motion '
        'reports; stock 8), Wake check (how often a resting ball looks for '
        'motion — the wake-up lag; stock rests in tiers down to one check '
        'per 500 ms), Stay awake after motion (full-speed time before '
        'resting; stock 128 ms), and Force awake (never rest at all — '
        'infinite stay awake with zero wake lag; the keyboard\'s idle and '
        'deep-sleep timeouts still bound overall power). Lower timing '
        'values mean more battery use. Configure everything in the Advanced '
        'tab or by clicking the round ball icons between the halves in the '
        'Editor. Mode, speed, and direction apply INSTANTLY over Bluetooth '
        '(live channel); the timing and Installed settings are build '
        'options (rebuild + flash). Scrolling whatever window is under the '
        'mouse pointer is a Windows setting (on by default), not a keyboard '
        'option.',
  ),
  _Entry(
    'BattCheck (battery level as color)',
    'A key behavior that flashes each half in its own battery color: 0% is '
        'red, sweeping through orange and yellow to green at 100%. One press '
        'triggers both halves, and each shows its OWN battery — so the two '
        'can differ. After the configured time (Advanced tab, default 2 s) '
        'your previous color, effect, and on/off state return; pressing '
        'again during the glow refreshes it. Assign it like any behavior in '
        'the Editor (it is also usable inside sequences). KeyMapper firmware '
        'only — it appears in the picker after you flash a build that '
        'includes it. Note: right after a restart a half may show red for '
        'the first minute until its first battery reading arrives.',
  ),
  _Entry(
    'Underglow brightness scale (why 0 is not off)',
    'Underglow brightness is stored on a 0-100 scale and the firmware '
        'remaps it onto its compiled output window (5%-50% on the Imprint). '
        'So brightness 0 is the DARKEST STILL-LIT setting — the same level '
        'the physical brightness-down key bottoms out at — not "off". Use '
        'the underglow off toggle to actually turn the LEDs off.',
  ),
  _Entry(
    'Power & idle (deep sleep)',
    'When a half runs on battery it goes IDLE after a period without key '
        'presses (optionally turning its LEDs off), and with deep sleep '
        'enabled it later powers OFF completely — that is why an unplugged, '
        'unused half seems dead until you press a key and give it a moment '
        'to reconnect. Both timeouts, the LED behavior, and deep sleep '
        'on/off are configurable in the Advanced tab under "Power & idle"; '
        'settings are compiled into the firmware, so rebuild and flash to '
        'apply them. On wake, KeyMapper firmware relights BOTH halves with '
        'the last color (stock ZMK only relights the half you pressed).',
  ),
  _Entry(
    'Studio Unlock (A+F hold)',
    'Stock firmware boots "locked": the keymap cannot be read or changed '
        'until you hold the two physical keys at the factory A and F '
        'positions (left home row) for ~3 s. It reads POSITIONS, not current '
        'meanings, so remapping does not move it. KeyMapper-built firmware '
        'removes locking by default (advised — the lock only guards '
        'configuration changes from this computer, never typing), but the '
        'Advanced tab can re-enable the stock behavior; then KeyMapper needs '
        'the A+F unlock after every keyboard restart.',
  ),
  _Entry(
    'Save vs pending changes',
    'Edits apply to the keyboard instantly but live in RAM ("pending"). Save '
        'writes them to the keyboard\'s settings flash so they survive power '
        'off. Discard reverts pending edits. KeyMapper always writes a file '
        'backup before saving.',
  ),
  _Entry(
    'Settings reset ("Restore stock settings")',
    'Erases the keyboard-stored layout (and Bluetooth pairings on a full '
        'reset), falling back to the keymap compiled into the firmware. '
        'DESTRUCTIVE for layouts made with Studio/KeyMapper on stock firmware — '
        'KeyMapper forces a backup first and never suggests the settings_reset '
        'UF2 casually.',
  ),
  _Entry(
    'Bootloader mode / ASSIMILATOR',
    'Double-tap the reset button next to the USB port: the half mounts as a '
        'USB drive named ASSIMILATOR. Dropping a .uf2 file on it flashes that '
        'half. The stored layout and pairings survive flashing (they live in '
        'a flash area the bootloader does not touch).',
  ),
  _Entry(
    'Supported platforms',
    'KeyMapper runs on Windows, macOS, and Linux — the UI is a browser page '
        'and the server is plain Python. Windows uses the native Bluetooth '
        'stack and is tested daily on real hardware; macOS and Linux use '
        'the cross-platform Bleak backend (code-complete, not yet validated '
        'on a physical keyboard). Sequences that TYPE at the computer — '
        'Alt-code accents and the Win+R program launcher — assume a Windows '
        'host, because they run on whatever machine the keyboard is plugged '
        'into. Everything else is host-independent.',
  ),
  _Entry(
    'Left half = the brain',
    'The left half is the split "central": it holds the keymap, talks to the '
        'PC, and runs the configuration link. KeyMapper always talks to the left '
        'half over USB; the right half only needs USB for firmware flashing.',
  ),
];

/// Searchable glossary screen.
class HelpScreen extends StatefulWidget {
  const HelpScreen({super.key});

  @override
  State<HelpScreen> createState() => _HelpScreenState();
}

class _HelpScreenState extends State<HelpScreen> {
  String _filter = '';

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final entries = _glossary
        .where((e) =>
            _filter.isEmpty ||
            e.term.toLowerCase().contains(_filter) ||
            e.definition.toLowerCase().contains(_filter))
        .toList();
    return ListView(
      padding: const EdgeInsets.all(24),
      children: [
        Card(
          child: ListTile(
            leading: const Icon(Icons.menu_book_outlined),
            title: const Text('Full KeyMapper manual'),
            subtitle: const Text(
                'The searchable glossary below covers the essentials; the '
                'complete manual explains every tab, the firmware pipeline, '
                'sequences, backups, and troubleshooting in depth.'),
            trailing: FilledButton.icon(
              onPressed: () => launchUrl(
                  Uri.base.replace(path: '/manual', query: ''),
                  webOnlyWindowName: '_blank'),
              icon: const Icon(Icons.open_in_new),
              label: const Text('Open manual'),
            ),
          ),
        ),
        const SizedBox(height: 8),
        TextField(
          decoration: const InputDecoration(
            labelText: 'Search help',
            prefixIcon: Icon(Icons.search),
          ),
          onChanged: (v) => setState(() => _filter = v.trim().toLowerCase()),
        ),
        const SizedBox(height: 12),
        for (final entry in entries)
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(entry.term, style: theme.textTheme.titleMedium),
                  const SizedBox(height: 4),
                  Text(entry.definition),
                ],
              ),
            ),
          ),
      ],
    );
  }
}
