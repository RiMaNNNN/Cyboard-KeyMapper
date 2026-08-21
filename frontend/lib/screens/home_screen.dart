/// Home: connection status and battery gauges.
library;

import 'package:flutter/material.dart';

import '../app_state.dart';

/// Landing screen: the connection card with the battery card below it.
/// Firmware-level tweaks (locking, power, colors, alerts) live in the
/// Advanced tab.
class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key, required this.model});

  final AppModel model;

  @override
  Widget build(BuildContext context) {
    final snap = model.snapshot;
    final theme = Theme.of(context);
    return ListView(
      padding: const EdgeInsets.all(24),
      children: [
        Card(
          child: ListTile(
            leading: Icon(
              snap.connected ? Icons.keyboard : Icons.keyboard_hide,
              color: snap.connected ? Colors.green : theme.colorScheme.error,
              size: 40,
            ),
            title: Text(snap.connected
                ? '${snap.deviceName ?? "Keyboard"} connected on ${snap.port}'
                : 'No keyboard connected'),
            subtitle: Text(snap.connected
                ? (snap.fake
                    ? 'Demo mode: simulated keyboard, no hardware involved.'
                    : (snap.locked
                        ? 'Live device link over USB — LOCKED: unlock it in '
                            'the Advanced tab (physical A+F hold).'
                        : 'Live device link over USB.'))
                : 'Plug the LEFT half into this PC with a USB-C DATA cable. '
                    'KeyMapper connects automatically — no port picking needed.'),
            trailing: snap.connected
                ? null
                : FilledButton(
                    onPressed: () => model.api
                        .connectNow()
                        .then((_) => model.refreshState()),
                    child: const Text('Scan now'),
                  ),
          ),
        ),
        _BatteryCard(model: model),
      ],
    );
  }
}

/// Battery status of both halves, read over the Bluetooth bond.
class _BatteryCard extends StatefulWidget {
  const _BatteryCard({required this.model});

  final AppModel model;

  @override
  State<_BatteryCard> createState() => _BatteryCardState();
}

class _BatteryCardState extends State<_BatteryCard> {
  List<(String, int)> _halves = [];
  String? _detail;
  bool _busy = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _busy = true);
    try {
      final (halves, detail) = await widget.model.api.getBattery();
      if (mounted) {
        setState(() {
          _halves = halves;
          _detail = detail;
          _busy = false;
        });
      }
    } on Object catch (e) {
      if (mounted) {
        setState(() {
          _halves = [];
          _detail = 'battery read failed: $e';
          _busy = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              Text('Battery', style: Theme.of(context).textTheme.titleMedium),
              const Spacer(),
              if (_busy)
                const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2))
              else
                IconButton(
                  tooltip: 'Refresh (reads over Bluetooth)',
                  onPressed: _load,
                  icon: const Icon(Icons.refresh),
                ),
            ]),
            if (_halves.isEmpty && !_busy)
              Text(_detail ?? 'unavailable')
            else
              Wrap(spacing: 32, runSpacing: 12, children: [
                for (final (label, percent) in _halves)
                  _BatteryGauge(label: label, percent: percent),
              ]),
            if (_halves.isNotEmpty && _detail != null) ...[
              const SizedBox(height: 6),
              Text(_detail!, style: Theme.of(context).textTheme.bodySmall),
            ],
            const SizedBox(height: 4),
            Text(
                'A low or sagging battery dims and color-shifts the underglow '
                '(mixed colors like yellow show it first) and can cause '
                'flicker.',
                style: Theme.of(context).textTheme.bodySmall),
          ],
        ),
      ),
    );
  }
}

/// A battery drawn as a filled gauge: green above 40%, orange above 20%,
/// red below, with the percentage written inside.
class _BatteryGauge extends StatelessWidget {
  const _BatteryGauge({required this.label, required this.percent});

  final String label;
  final int percent;

  Color get _color => percent > 40
      ? Colors.green
      : percent > 20
          ? Colors.orange
          : Colors.red;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final outline = theme.colorScheme.onSurfaceVariant;
    final fraction = (percent.clamp(0, 100)) / 100.0;
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Row(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            Container(
              width: 132,
              height: 46,
              padding: const EdgeInsets.all(3),
              decoration: BoxDecoration(
                border: Border.all(color: outline, width: 2.5),
                borderRadius: BorderRadius.circular(10),
              ),
              child: Stack(
                children: [
                  FractionallySizedBox(
                    widthFactor: fraction,
                    heightFactor: 1,
                    child: Container(
                      decoration: BoxDecoration(
                        color: _color,
                        borderRadius: BorderRadius.circular(6),
                      ),
                    ),
                  ),
                  Center(
                    child: Text(
                      '$percent%',
                      style: theme.textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w700,
                        color: theme.colorScheme.onSurface,
                        shadows: [
                          Shadow(
                              color: theme.colorScheme.surface,
                              blurRadius: 3),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            ),
            // Battery terminal nub.
            Container(
              width: 6,
              height: 18,
              margin: const EdgeInsets.only(left: 2),
              decoration: BoxDecoration(
                color: outline,
                borderRadius: const BorderRadius.horizontal(
                    right: Radius.circular(3)),
              ),
            ),
          ],
        ),
        const SizedBox(height: 4),
        Text(label, style: theme.textTheme.titleSmall),
      ],
    );
  }
}
