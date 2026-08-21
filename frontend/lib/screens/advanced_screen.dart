/// Advanced: firmware-level tweaks — Studio locking, power & idle,
/// layer colors, and the low-battery blink.
library;

import 'package:flutter/material.dart';

import '../app_state.dart';
import '../models.dart';
import '../widgets/hsb_picker.dart';
import '../widgets/trackball_settings.dart';

/// Screen for the settings that are compiled into generated firmware.
///
/// Everything here takes effect on the keyboard only after a rebuild and
/// flash in the Firmware tab (that is a ZMK architecture rule, not an app
/// limitation): Studio locking, idle / deep-sleep power management, the
/// sequence layer colors, and the low-battery blink.
class AdvancedScreen extends StatelessWidget {
  const AdvancedScreen({super.key, required this.model});

  final AppModel model;

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(24),
      children: [
        _LockingCard(model: model),
        _TrackballsCard(model: model),
        _PowerCard(model: model),
        _LayerColorsCard(model: model),
        _BatteryAlertCard(model: model),
      ],
    );
  }
}

/// Studio locking on/off (compiled into the next firmware build), plus the
/// guided physical unlock whenever the connected keyboard is locked.
class _LockingCard extends StatefulWidget {
  const _LockingCard({required this.model});

  final AppModel model;

  @override
  State<_LockingCard> createState() => _LockingCardState();
}

class _LockingCardState extends State<_LockingCard> {
  LockingModel? _locking;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    widget.model.api.getLocking().then((l) {
      if (mounted) setState(() => _locking = l);
    }).catchError((Object _) {});
  }

  Future<void> _toggle(bool enabled) async {
    final locking = _locking;
    if (locking == null) return;
    setState(() {
      locking.studioLockingEnabled = enabled;
      _saving = true;
    });
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.model.api.putLocking(locking);
      messenger.showSnackBar(SnackBar(
          content: Text(enabled
              ? 'Locking will be ENABLED in the next firmware build — '
                  'KeyMapper will need the physical A+F unlock after every '
                  'keyboard restart'
              : 'Locking will be disabled in the next firmware build — '
                  'no unlock ritual ever again (advised)')));
    } on Object catch (e) {
      locking.studioLockingEnabled = !enabled;
      if (mounted) setState(() {});
      messenger.showSnackBar(SnackBar(content: Text('Save failed: $e')));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final snap = widget.model.snapshot;
    final locking = _locking;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              const Icon(Icons.lock_open_outlined),
              const SizedBox(width: 8),
              Text('Studio locking', style: theme.textTheme.titleMedium),
            ]),
            const SizedBox(height: 4),
            const Text(
                'Stock firmware boots "locked": no app (KeyMapper or Cyboard '
                'Studio) can read or edit the keymap until you hold the two '
                'physical keys at the factory A and F positions for about 3 '
                'seconds — after every restart. KeyMapper-built firmware '
                'disables that lock by default, which is the advised setting: '
                'the lock only guards configuration changes from this '
                'computer, never your typing. Keep it only if you want the '
                'stock behavior back. Compiled into the firmware — rebuild '
                'and flash to apply.'),
            const SizedBox(height: 4),
            if (locking == null)
              const Text('Locking setting unavailable (server not reachable).')
            else
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                title: const Text(
                    'Keep ZMK Studio locking (stock behavior, NOT advised)'),
                subtitle: const Text(
                    'When on, KeyMapper requires the physical A+F unlock '
                    'after every keyboard restart'),
                value: locking.studioLockingEnabled,
                onChanged: _saving ? null : _toggle,
              ),
            if (snap.connected && snap.locked) ...[
              const SizedBox(height: 8),
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: theme.colorScheme.tertiaryContainer,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      const Icon(Icons.lock_outline),
                      const SizedBox(width: 8),
                      Text('Unlock needed right now',
                          style: theme.textTheme.titleMedium),
                    ]),
                    const SizedBox(height: 8),
                    const Text(
                      'The connected keyboard is locked and the unlock is '
                      'physical (there is no software command — by design). '
                      'Hold the two LEFT-half home-row keys at the positions '
                      'where A and F sit in the factory layout for about 3 '
                      'seconds. This works no matter what you remapped those '
                      'keys to: the combo reads physical positions, not key '
                      'meanings. This banner disappears by itself the moment '
                      'the keyboard unlocks.',
                    ),
                  ],
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Both trackballs' behavior (mode, speed, direction — applied live over
/// Bluetooth when the firmware carries the config channel, and always staged
/// for future builds) plus the shared sensor responsiveness (build-time).
class _TrackballsCard extends StatefulWidget {
  const _TrackballsCard({required this.model});

  final AppModel model;

  @override
  State<_TrackballsCard> createState() => _TrackballsCardState();
}

class _TrackballsCardState extends State<_TrackballsCard> {
  TrackballModel? _trackballs;
  bool _saving = false;
  bool _dirty = false;

  @override
  void initState() {
    super.initState();
    widget.model.api.getTrackballs().then((t) {
      if (mounted) setState(() => _trackballs = t);
    }).catchError((Object _) {});
  }

  Future<void> _save() async {
    final trackballs = _trackballs;
    if (trackballs == null) return;
    setState(() => _saving = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      final (appliedLive, detail) =
          await widget.model.api.putTrackballs(trackballs);
      if (mounted) setState(() => _dirty = false);
      messenger.showSnackBar(SnackBar(
          content: Text(appliedLive
              ? 'Applied live — the trackballs behave this way right now '
                  '(and the settings are staged for future builds)'
              : 'Saved — ${detail ?? 'rebuild + flash in the Firmware tab '
                  'to apply on the keyboard'}')));
    } on Object catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('Save failed: $e')));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final trackballs = _trackballs;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              const Icon(Icons.trip_origin),
              const SizedBox(width: 8),
              Text('Trackballs', style: theme.textTheme.titleMedium),
              const Spacer(),
              if (trackballs != null)
                FilledButton.icon(
                  onPressed: _saving || !_dirty ? null : _save,
                  icon: const Icon(Icons.save_outlined, size: 18),
                  label: Text(_saving ? 'Saving…' : 'Save'),
                ),
            ]),
            const SizedBox(height: 4),
            const Text(
                'What each ball does when rolled: move the cursor, scroll '
                'vertically or horizontally, or nothing — with its own speed '
                'and scroll direction. Also reachable by clicking the ball '
                'icons in the Editor. On firmware with the live channel, '
                'mode, speed, and direction apply INSTANTLY over Bluetooth '
                'and persist on the keyboard; responsiveness and Installed '
                'changes still need a rebuild + flash. (Scrolling whatever '
                'window sits under the mouse pointer is a Windows setting, '
                'on by default — not a keyboard option.)'),
            const SizedBox(height: 8),
            if (trackballs == null)
              const Text(
                  'Trackball settings unavailable (server not reachable).')
            else ...[
              Wrap(
                spacing: 32,
                runSpacing: 12,
                children: [
                  SizedBox(
                    width: 320,
                    child: TrackballSideForm(
                      title: 'Left trackball',
                      side: trackballs.left,
                      onChanged: () => setState(() => _dirty = true),
                    ),
                  ),
                  SizedBox(
                    width: 320,
                    child: TrackballSideForm(
                      title: 'Right trackball',
                      side: trackballs.right,
                      onChanged: () => setState(() => _dirty = true),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 16,
                runSpacing: 8,
                children: [
                  SizedBox(
                    width: 260,
                    child: TextFormField(
                      key: const ValueKey('trackball-responsiveness'),
                      initialValue: '${trackballs.responsivenessMs}',
                      decoration: const InputDecoration(
                        labelText: 'Responsiveness ms (0–100)',
                        helperText: 'Shared by both balls; lower = snappier '
                            'but more battery and radio traffic. Stock: 8',
                      ),
                      keyboardType: TextInputType.number,
                      onChanged: (v) {
                        final parsed = int.tryParse(v.trim());
                        if (parsed != null && parsed >= 0 && parsed <= 100) {
                          setState(() {
                            trackballs.responsivenessMs = parsed;
                            _dirty = true;
                          });
                        }
                      },
                    ),
                  ),
                  if (!trackballs.forceAwake)
                  SizedBox(
                    width: 260,
                    child: TextFormField(
                      key: const ValueKey('trackball-wake-check'),
                      initialValue: trackballs.wakeCheckMs == null
                          ? ''
                          : '${trackballs.wakeCheckMs}',
                      decoration: const InputDecoration(
                        labelText: 'Wake check ms (10–2550)',
                        helperText: 'How often a RESTING ball looks for '
                            'motion — the wake-up lag. Lower = instant wake, '
                            'more battery. Empty = stock (up to 500 ms after '
                            'long idles)',
                      ),
                      keyboardType: TextInputType.number,
                      onChanged: (v) {
                        final text = v.trim();
                        final parsed = int.tryParse(text);
                        if (text.isEmpty ||
                            (parsed != null &&
                                parsed >= 10 &&
                                parsed <= 2550)) {
                          setState(() {
                            trackballs.wakeCheckMs =
                                text.isEmpty ? null : parsed;
                            _dirty = true;
                          });
                        }
                      },
                    ),
                  ),
                  if (!trackballs.forceAwake)
                  SizedBox(
                    width: 260,
                    child: TextFormField(
                      key: const ValueKey('trackball-awake-after'),
                      initialValue: trackballs.awakeAfterMotionMs == null
                          ? ''
                          : '${trackballs.awakeAfterMotionMs}',
                      decoration: const InputDecoration(
                        labelText: 'Stay awake after motion ms (32–8160)',
                        helperText: 'Full-speed time after the last movement '
                            'before the ball starts resting. Empty = stock '
                            '(128 ms)',
                      ),
                      keyboardType: TextInputType.number,
                      onChanged: (v) {
                        final text = v.trim();
                        final parsed = int.tryParse(text);
                        if (text.isEmpty ||
                            (parsed != null &&
                                parsed >= 32 &&
                                parsed <= 8160)) {
                          setState(() {
                            trackballs.awakeAfterMotionMs =
                                text.isEmpty ? null : parsed;
                            _dirty = true;
                          });
                        }
                      },
                    ),
                  ),
                ],
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                title: const Text('Force awake — never rest (infinite stay '
                    'awake, zero wake lag)'),
                subtitle: const Text('Highest sensor battery cost; the '
                    'keyboard-level idle and deep-sleep timeouts still '
                    'apply. Overrides the wake/stay-awake fields.'),
                value: trackballs.forceAwake,
                onChanged: (v) => setState(() {
                  trackballs.forceAwake = v;
                  _dirty = true;
                }),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Idle and deep-sleep power settings, compiled into the next firmware build.
///
/// These drive ZMK's built-in power management: a half with no USB power goes
/// idle after a period of inactivity (optionally switching its LEDs off) and
/// can later power down completely (deep sleep) until a key is pressed.
class _PowerCard extends StatefulWidget {
  const _PowerCard({required this.model});

  final AppModel model;

  @override
  State<_PowerCard> createState() => _PowerCardState();
}

class _PowerCardState extends State<_PowerCard> {
  PowerModel? _power;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    widget.model.api.getPower().then((p) {
      if (mounted) setState(() => _power = p);
    }).catchError((Object _) {});
  }

  Future<void> _save() async {
    final power = _power;
    if (power == null) return;
    setState(() => _saving = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.model.api.putPower(power);
      messenger.showSnackBar(const SnackBar(
          content: Text('Power settings saved — they apply after the next '
              'firmware build + flash (Firmware tab)')));
    } on Object catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('Save failed: $e')));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Widget _numberField(String label, int value, int min, int max,
      ValueChanged<int> onChanged) {
    return SizedBox(
      width: 210,
      child: TextFormField(
        // Keyed on the label only: including the value would remount the
        // field on every keystroke and throw the cursor out.
        key: ValueKey(label),
        initialValue: '$value',
        decoration: InputDecoration(labelText: '$label ($min–$max)'),
        keyboardType: TextInputType.number,
        onChanged: (v) {
          final parsed = int.tryParse(v.trim());
          if (parsed != null && parsed >= min && parsed <= max) {
            onChanged(parsed);
          }
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final power = _power;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              const Icon(Icons.power_settings_new),
              const SizedBox(width: 8),
              Text('Power & idle', style: theme.textTheme.titleMedium),
              const Spacer(),
              if (power != null)
                FilledButton.icon(
                  onPressed: _saving ? null : _save,
                  icon: const Icon(Icons.save_outlined, size: 18),
                  label: Text(_saving ? 'Saving…' : 'Save'),
                ),
            ]),
            const SizedBox(height: 4),
            const Text(
                'How each half saves battery when it runs unplugged: after the '
                'idle time it dims down (optionally LEDs off), and with deep '
                'sleep on it powers off completely until a key press wakes it. '
                'Compiled into the firmware — rebuild and flash in the '
                'Firmware tab to apply.'),
            const SizedBox(height: 8),
            if (power == null)
              const Text('Power settings unavailable (server not reachable).')
            else ...[
              Wrap(
                spacing: 16,
                runSpacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  _numberField('Idle after seconds', power.idleSeconds, 5,
                      7200, (v) => setState(() => power.idleSeconds = v)),
                  _numberField(
                      'Deep sleep after minutes',
                      power.deepSleepMinutes,
                      1,
                      1440,
                      (v) => setState(() => power.deepSleepMinutes = v)),
                ],
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                title: const Text('Deep sleep (half powers off when unused)'),
                subtitle: const Text(
                    'Waking takes a key press plus a moment to reconnect'),
                value: power.deepSleepEnabled,
                onChanged: (v) =>
                    setState(() => power.deepSleepEnabled = v),
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                title: const Text('Underglow off while idle'),
                value: power.rgbOffWhenIdle,
                onChanged: (v) => setState(() => power.rgbOffWhenIdle = v),
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                title: const Text('Underglow off whenever unplugged '
                    '(biggest battery saver)'),
                value: power.rgbOffWhenUnplugged,
                onChanged: (v) =>
                    setState(() => power.rgbOffWhenUnplugged = v),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Layer colors as defined by the staged sequences, with one brightness
/// control that rewrites all of them at once.
class _LayerColorsCard extends StatefulWidget {
  const _LayerColorsCard({required this.model});

  final AppModel model;

  @override
  State<_LayerColorsCard> createState() => _LayerColorsCardState();
}

class _LayerColorsCardState extends State<_LayerColorsCard> {
  List<(String, String, int, int, int, int)> _entries = [];
  int _brightness = 50;
  bool _loaded = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final macros = await widget.model.api.stagedMacros();
      final colorRe = RegExp(r'RGB_COLOR_HSB\((\d+),\s*(\d+),\s*(\d+)\)');
      final layerRe = RegExp(r'&(to|mo)\s+(\d+)');
      final entries = <(String, String, int, int, int, int)>[];
      for (final m in macros) {
        String? kind;
        int layer = -1;
        int? h, s, b;
        for (final step in [...m.steps, ...m.shiftedSteps]) {
          final lm = layerRe.firstMatch(step.binding);
          if (lm != null && kind == null) {
            kind = lm.group(1);
            layer = int.parse(lm.group(2)!);
          }
          final cm = colorRe.firstMatch(step.binding);
          if (cm != null && h == null) {
            h = int.parse(cm.group(1)!);
            s = int.parse(cm.group(2)!);
            b = int.parse(cm.group(3)!);
          }
        }
        if (kind != null && h != null) {
          entries.add((m.displayName, kind, layer, h, s!, b!));
        }
      }
      if (mounted) {
        setState(() {
          _entries = entries;
          if (entries.isNotEmpty) _brightness = entries.first.$6;
          _loaded = true;
        });
      }
    } on Object {
      if (mounted) setState(() => _loaded = true);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!_loaded || _entries.isEmpty) return const SizedBox.shrink();
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Layer colors (from your sequences)',
                style: theme.textTheme.titleMedium),
            const SizedBox(height: 4),
            const Text(
                'These HSB triples are what the underglow shows after each '
                'layer switch. Live tweaks made with the keyboard\'s own '
                'hue/sat/brightness keys cannot be read back over the '
                'protocol; to make a tweak permanent, put it here.'),
            const SizedBox(height: 8),
            Wrap(
              spacing: 12,
              runSpacing: 8,
              children: [
                for (final e in _entries)
                  ActionChip(
                    avatar: CircleAvatar(
                      backgroundColor: HSVColor.fromAHSV(
                              1, e.$4.toDouble().clamp(0, 359.9), e.$5 / 100,
                              e.$6 / 100)
                          .toColor(),
                    ),
                    label: Text(
                        '${e.$1} — HSB(${e.$4},${e.$5},${e.$6})'),
                    tooltip: 'Click to pick a new color for this layer',
                    onPressed: () async {
                      final picked = await showHsbPicker(context,
                          hue: e.$4,
                          saturation: e.$5,
                          brightness: e.$6,
                          title: 'Color for ${e.$1}');
                      if (picked == null || !context.mounted) return;
                      final messenger = ScaffoldMessenger.of(context);
                      try {
                        final n = await widget.model.api.setLayerColor(
                            e.$3, picked.$1, picked.$2, picked.$3);
                        messenger.showSnackBar(SnackBar(
                            content: Text('$n sequence color(s) updated — '
                                'rebuild + flash to apply on the keyboard')));
                        await _load();
                      } on Object catch (err) {
                        messenger.showSnackBar(
                            SnackBar(content: Text('Failed: $err')));
                      }
                    },
                  ),
              ],
            ),
            const SizedBox(height: 8),
            Row(children: [
              SizedBox(
                width: 190,
                child: TextFormField(
                  initialValue: '$_brightness',
                  decoration: const InputDecoration(
                      labelText: 'Brightness for ALL sequences',
                      helperText: '0-100; 0 = darkest still-lit (what the '
                          'brightness-down key bottoms out at), not off'),
                  keyboardType: TextInputType.number,
                  onChanged: (v) {
                    final parsed = int.tryParse(v);
                    if (parsed != null) _brightness = parsed;
                  },
                ),
              ),
              const SizedBox(width: 12),
              FilledButton.icon(
                onPressed: () async {
                  final messenger = ScaffoldMessenger.of(context);
                  final b = _brightness.clamp(0, 100);
                  try {
                    final n =
                        await widget.model.api.setSequenceBrightness(b);
                    messenger.showSnackBar(SnackBar(
                        content: Text('$n sequence color(s) set to '
                            'brightness $b. Rebuild + flash in the '
                            'Firmware tab to apply on the keyboard.')));
                    await _load();
                  } on Object catch (e) {
                    messenger
                        .showSnackBar(SnackBar(content: Text('Failed: $e')));
                  }
                },
                icon: const Icon(Icons.light_mode_outlined),
                label: const Text('Apply brightness'),
              ),
            ]),
          ],
        ),
      ),
    );
  }
}

/// Configurable low-battery underglow blink (compiled into generated firmware).
class _BatteryAlertCard extends StatefulWidget {
  const _BatteryAlertCard({required this.model});

  final AppModel model;

  @override
  State<_BatteryAlertCard> createState() => _BatteryAlertCardState();
}

class _BatteryAlertCardState extends State<_BatteryAlertCard> {
  BatteryAlertModel? _alert;
  bool _dirty = false;

  @override
  void initState() {
    super.initState();
    widget.model.api
        .getBatteryAlert()
        .then((a) {
          if (mounted) setState(() => _alert = a);
        })
        .catchError((_) {});
  }

  Widget _numField(
    String label,
    String help,
    int value,
    int min,
    int max,
    ValueChanged<int> onChanged,
  ) {
    return SizedBox(
      width: 170,
      child: TextFormField(
        initialValue: '$value',
        decoration: InputDecoration(labelText: label, helperText: help),
        keyboardType: TextInputType.number,
        onChanged: (v) {
          final parsed = int.tryParse(v);
          if (parsed != null && parsed >= min && parsed <= max) {
            onChanged(parsed);
            setState(() => _dirty = true);
          }
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final alert = _alert;
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: alert == null
            ? const SizedBox(
                height: 48,
                child: Center(child: CircularProgressIndicator()),
              )
            : Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const Icon(Icons.battery_alert_outlined),
                      const SizedBox(width: 8),
                      Text(
                        'Low-battery blink',
                        style: theme.textTheme.titleMedium,
                      ),
                      const Spacer(),
                      Switch(
                        value: alert.enabled,
                        onChanged: (v) => setState(() {
                          alert.enabled = v;
                          _dirty = true;
                        }),
                      ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  const Text(
                    'The half whose battery drops below the threshold blinks '
                    'its underglow in the alert color, then restores your '
                    'color. Compiled into the firmware: change these, then '
                    'Generate + Build + Flash for them to take effect. Color '
                    'is HSB — hue 0-360 (0 red, 120 green, 240 blue), '
                    'saturation and brightness 0-100.',
                  ),
                  const SizedBox(height: 8),
                  Wrap(
                    spacing: 12,
                    runSpacing: 8,
                    children: [
                      _numField(
                        'Threshold %',
                        '1-99',
                        alert.thresholdPercent,
                        1,
                        99,
                        (v) => alert.thresholdPercent = v,
                      ),
                      _numField(
                        'Blinks',
                        '1-20',
                        alert.blinkCount,
                        1,
                        20,
                        (v) => alert.blinkCount = v,
                      ),
                      _numField(
                        'Hue',
                        '0-360',
                        alert.hue,
                        0,
                        360,
                        (v) => alert.hue = v,
                      ),
                      _numField(
                        'Saturation',
                        '0-100',
                        alert.saturation,
                        0,
                        100,
                        (v) => alert.saturation = v,
                      ),
                      _numField(
                        'Brightness',
                        '0-100 (cap 50)',
                        alert.brightness,
                        0,
                        100,
                        (v) => alert.brightness = v,
                      ),
                      _numField(
                        'Every (min)',
                        '1-1440',
                        alert.intervalMinutes,
                        1,
                        1440,
                        (v) => alert.intervalMinutes = v,
                      ),
                    ],
                  ),
                  const Divider(height: 24),
                  Text('BattCheck', style: theme.textTheme.titleSmall),
                  const SizedBox(height: 4),
                  const Text(
                    'The BattCheck behavior (assign it to any key in the '
                    'Editor) flashes each half in its own battery color: '
                    '0% red, through orange and yellow, 100% green — then '
                    'your color returns. Duration is compiled into the '
                    'firmware.',
                  ),
                  const SizedBox(height: 8),
                  _numField(
                    'BattCheck ms',
                    '250-60000; capped just below the idle timeout at '
                        'build time',
                    alert.battcheckMs,
                    250,
                    60000,
                    (v) => alert.battcheckMs = v,
                  ),
                  const SizedBox(height: 8),
                  FilledButton.icon(
                    onPressed: !_dirty
                        ? null
                        : () async {
                            final messenger = ScaffoldMessenger.of(context);
                            try {
                              await widget.model.api.putBatteryAlert(alert);
                              if (mounted) setState(() => _dirty = false);
                              messenger.showSnackBar(
                                const SnackBar(
                                  content: Text(
                                    'Saved - included in the next firmware build',
                                  ),
                                ),
                              );
                            } on Object catch (e) {
                              messenger.showSnackBar(
                                SnackBar(content: Text('Save failed: $e')),
                              );
                            }
                          },
                    icon: const Icon(Icons.save),
                    label: const Text('Save settings'),
                  ),
                ],
              ),
      ),
    );
  }
}
