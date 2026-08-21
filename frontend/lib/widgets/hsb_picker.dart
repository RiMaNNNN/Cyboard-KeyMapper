/// Visual HSB color picker: a hue×saturation map plus a brightness slider.
library;

import 'package:flutter/material.dart';

/// Opens the picker; resolves to the chosen ``(hue, saturation, brightness)``
/// (ZMK ranges: 0–360, 0–100, 0–100) or null when cancelled.
Future<(int, int, int)?> showHsbPicker(
  BuildContext context, {
  int hue = 0,
  int saturation = 100,
  int brightness = 50,
  String title = 'Pick a color',
}) {
  return showDialog<(int, int, int)>(
    context: context,
    builder: (context) => _HsbPickerDialog(
      initialHue: hue,
      initialSaturation: saturation,
      initialBrightness: brightness,
      title: title,
    ),
  );
}

class _HsbPickerDialog extends StatefulWidget {
  const _HsbPickerDialog({
    required this.initialHue,
    required this.initialSaturation,
    required this.initialBrightness,
    required this.title,
  });

  final int initialHue;
  final int initialSaturation;
  final int initialBrightness;
  final String title;

  @override
  State<_HsbPickerDialog> createState() => _HsbPickerDialogState();
}

class _HsbPickerDialogState extends State<_HsbPickerDialog> {
  late double _hue = widget.initialHue.toDouble().clamp(0, 360);
  late double _sat = widget.initialSaturation.toDouble().clamp(0, 100);
  late double _bri = widget.initialBrightness.toDouble().clamp(0, 100);

  static const double _mapWidth = 340;
  static const double _mapHeight = 180;

  void _pickFromMap(Offset local) {
    setState(() {
      _hue = (local.dx / _mapWidth * 360).clamp(0, 360);
      _sat = (100 - local.dy / _mapHeight * 100).clamp(0, 100);
    });
  }

  @override
  Widget build(BuildContext context) {
    final preview =
        HSVColor.fromAHSV(1, _hue.clamp(0, 359.9), _sat / 100, _bri / 100)
            .toColor();
    return AlertDialog(
      title: Text(widget.title),
      content: SizedBox(
        width: _mapWidth,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Hue (x) × saturation (y) map: hue gradient overlaid by a
            // white-to-transparent vertical gradient (desaturation upward).
            GestureDetector(
              onTapDown: (d) => _pickFromMap(d.localPosition),
              onPanUpdate: (d) => _pickFromMap(d.localPosition),
              child: SizedBox(
                width: _mapWidth,
                height: _mapHeight,
                child: Stack(
                  children: [
                    Container(
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(8),
                        gradient: const LinearGradient(
                          colors: [
                            Color(0xFFFF0000),
                            Color(0xFFFFFF00),
                            Color(0xFF00FF00),
                            Color(0xFF00FFFF),
                            Color(0xFF0000FF),
                            Color(0xFFFF00FF),
                            Color(0xFFFF0000),
                          ],
                        ),
                      ),
                    ),
                    Container(
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(8),
                        gradient: const LinearGradient(
                          begin: Alignment.bottomCenter,
                          end: Alignment.topCenter,
                          colors: [Colors.transparent, Colors.white],
                        ),
                      ),
                    ),
                    Positioned(
                      left: (_hue / 360 * _mapWidth - 8).clamp(0, _mapWidth - 16),
                      top: ((100 - _sat) / 100 * _mapHeight - 8)
                          .clamp(0, _mapHeight - 16),
                      child: Container(
                        width: 16,
                        height: 16,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          border: Border.all(color: Colors.black, width: 2),
                          color: Colors.white.withValues(alpha: 0.6),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                const Text('Brightness'),
                Expanded(
                  child: Slider(
                    value: _bri,
                    min: 0,
                    max: 100,
                    divisions: 100,
                    label: '${_bri.round()}',
                    onChanged: (v) => setState(() => _bri = v),
                  ),
                ),
              ],
            ),
            Row(
              children: [
                Container(
                  width: 56,
                  height: 32,
                  decoration: BoxDecoration(
                    color: preview,
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(
                        color: Theme.of(context).colorScheme.outlineVariant),
                  ),
                ),
                const SizedBox(width: 12),
                Text('HSB(${_hue.round()}, ${_sat.round()}, ${_bri.round()})'
                    '${_bri > 50 ? "  (firmware caps brightness at 50)" : ""}'),
              ],
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(
              context, (_hue.round(), _sat.round(), _bri.round())),
          child: const Text('Use this color'),
        ),
      ],
    );
  }
}
