/// Trackball settings UI: per-side form, the Advanced-tab card, and the
/// editor's click-a-ball dialog.
library;

import 'package:flutter/material.dart';

import '../app_state.dart';
import '../models.dart';

/// Human labels for the trackball modes, in display order.
const List<(String, String, IconData)> trackballModes = [
  ('mouse', 'Cursor', Icons.mouse_outlined),
  ('scroll_vertical', 'Vertical scroll', Icons.swap_vert),
  ('scroll_horizontal', 'Horizontal scroll', Icons.swap_horiz),
  ('disabled', 'Disabled', Icons.do_not_disturb_alt),
];

/// Editable form for ONE trackball: installed switch, mode selector, speed,
/// and scroll direction. Mutates [side] in place and calls [onChanged] after
/// every edit so the host can enable its Save button.
class TrackballSideForm extends StatefulWidget {
  const TrackballSideForm({
    super.key,
    required this.title,
    required this.side,
    required this.onChanged,
  });

  final String title;
  final TrackballSideModel side;
  final VoidCallback onChanged;

  @override
  State<TrackballSideForm> createState() => _TrackballSideFormState();
}

class _TrackballSideFormState extends State<TrackballSideForm> {
  void _edit(VoidCallback apply) {
    setState(apply);
    widget.onChanged();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final side = widget.side;
    final scrolling =
        side.mode == 'scroll_vertical' || side.mode == 'scroll_horizontal';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Row(children: [
          const Icon(Icons.trip_origin, size: 18),
          const SizedBox(width: 6),
          Text(widget.title, style: theme.textTheme.titleSmall),
        ]),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          dense: true,
          title: const Text('Installed on this half'),
          subtitle: const Text(
              'The keyboard cannot report this, so tell KeyMapper — '
              'uninstalled balls disappear from the Editor AND are compiled '
              'off in the next firmware build'),
          value: side.installed,
          onChanged: (v) => _edit(() => side.installed = v),
        ),
        if (side.installed) ...[
          const SizedBox(height: 4),
          SegmentedButton<String>(
            segments: [
              for (final (value, label, icon) in trackballModes)
                ButtonSegment(
                  value: value,
                  icon: Icon(icon, size: 16),
                  tooltip: label,
                ),
            ],
            selected: {side.mode},
            onSelectionChanged: (s) => _edit(() => side.mode = s.first),
            showSelectedIcon: false,
          ),
          const SizedBox(height: 2),
          Text(
            trackballModes
                .firstWhere((m) => m.$1 == side.mode,
                    orElse: () => trackballModes.first)
                .$2,
            style: theme.textTheme.bodySmall,
          ),
          if (side.mode != 'disabled') ...[
            const SizedBox(height: 8),
            SizedBox(
              width: 220,
              child: TextFormField(
                key: ValueKey('${widget.title}-speed'),
                initialValue: '${side.speedPercent}',
                decoration: const InputDecoration(
                  labelText: 'Speed % (6–1600)',
                  helperText: '100 = sensor default; stock scroll uses 33',
                ),
                keyboardType: TextInputType.number,
                onChanged: (v) {
                  final parsed = int.tryParse(v.trim());
                  if (parsed != null && parsed >= 6 && parsed <= 1600) {
                    _edit(() => side.speedPercent = parsed);
                  }
                },
              ),
            ),
            if (scrolling)
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                title: const Text('Natural direction'),
                subtitle: const Text(
                    'View follows the top of the ball (stock scroll feel)'),
                value: side.naturalDirection,
                onChanged: (v) => _edit(() => side.naturalDirection = v),
              ),
          ],
        ],
      ],
    );
  }
}

/// Opens the trackball settings dialog for one side (from the editor's ball
/// buttons). Loads the current settings, edits that side plus the shared
/// responsiveness, and saves the whole configuration on Apply.
Future<void> showTrackballDialog(
  BuildContext context,
  AppModel model,
  String sideName,
) async {
  final messenger = ScaffoldMessenger.of(context);
  final TrackballModel config;
  try {
    config = await model.api.getTrackballs();
  } on Object catch (e) {
    messenger.showSnackBar(
        SnackBar(content: Text('Trackball settings unavailable: $e')));
    return;
  }
  if (!context.mounted) return;
  // Live outside the builder so a route rebuild cannot reset them mid-save.
  bool saving = false;
  bool appliedLive = false;
  String? applyDetail;
  final saved = await showDialog<bool>(
    context: context,
    builder: (context) {
      return StatefulBuilder(
        builder: (context, setState) => AlertDialog(
          title: Text(
              '${sideName == 'left' ? 'Left' : 'Right'} trackball settings'),
          content: SizedBox(
            width: 420,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  TrackballSideForm(
                    title: sideName == 'left'
                        ? 'Left trackball'
                        : 'Right trackball',
                    side: config.side(sideName),
                    onChanged: () {},
                  ),
                  const Divider(height: 24),
                  Wrap(
                    spacing: 12,
                    runSpacing: 8,
                    children: [
                      SizedBox(
                        width: 220,
                        child: TextFormField(
                          initialValue: '${config.responsivenessMs}',
                          decoration: const InputDecoration(
                            labelText: 'Responsiveness ms (0–100)',
                            helperText: 'Both trackballs share this; lower = '
                                'snappier, more battery. Stock: 8',
                          ),
                          keyboardType: TextInputType.number,
                          onChanged: (v) {
                            final parsed = int.tryParse(v.trim());
                            if (parsed != null &&
                                parsed >= 0 &&
                                parsed <= 100) {
                              config.responsivenessMs = parsed;
                            }
                          },
                        ),
                      ),
                      if (!config.forceAwake)
                        SizedBox(
                          width: 220,
                          child: TextFormField(
                            initialValue: config.wakeCheckMs == null
                                ? ''
                                : '${config.wakeCheckMs}',
                            decoration: const InputDecoration(
                              labelText: 'Wake check ms (10–2550)',
                              helperText: 'How often a resting ball looks '
                                  'for motion (the wake lag; 10 = the '
                                  'sensor\'s hardware floor, near-instant). '
                                  'Empty = stock tiers, up to 500 ms',
                            ),
                            keyboardType: TextInputType.number,
                            onChanged: (v) {
                              final text = v.trim();
                              final parsed = int.tryParse(text);
                              if (text.isEmpty ||
                                  (parsed != null &&
                                      parsed >= 10 &&
                                      parsed <= 2550)) {
                                config.wakeCheckMs =
                                    text.isEmpty ? null : parsed;
                              }
                            },
                          ),
                        ),
                      if (!config.forceAwake)
                        SizedBox(
                          width: 220,
                          child: TextFormField(
                            initialValue: config.awakeAfterMotionMs == null
                                ? ''
                                : '${config.awakeAfterMotionMs}',
                            decoration: const InputDecoration(
                              labelText: 'Stay awake ms (32–8160)',
                              helperText: 'Full speed after the last '
                                  'movement before resting. Empty = stock '
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
                                config.awakeAfterMotionMs =
                                    text.isEmpty ? null : parsed;
                              }
                            },
                          ),
                        ),
                    ],
                  ),
                  SwitchListTile(
                    contentPadding: EdgeInsets.zero,
                    dense: true,
                    title: const Text('Force awake — never rest '
                        '(infinite stay awake, zero wake lag)'),
                    subtitle: const Text('Highest sensor battery cost; '
                        'keyboard idle/deep sleep still applies. Overrides '
                        'the wake/stay-awake fields (hidden while on).'),
                    value: config.forceAwake,
                    onChanged: (v) =>
                        setState(() => config.forceAwake = v),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Mode, speed, and direction apply INSTANTLY on firmware '
                    'with the live channel (and are staged for future '
                    'builds); responsiveness, wake timing, and Installed '
                    'changes need a rebuild + flash. Scrolling any window '
                    'under the mouse pointer is a Windows setting (on by '
                    'default), not a keyboard option.',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: saving
                  ? null
                  : () async {
                      setState(() => saving = true);
                      try {
                        (appliedLive, applyDetail) =
                            await model.api.putTrackballs(config);
                        if (context.mounted) Navigator.pop(context, true);
                      } on Object catch (e) {
                        saving = false;
                        if (context.mounted) setState(() {});
                        messenger.showSnackBar(
                            SnackBar(content: Text('Save failed: $e')));
                      }
                    },
              child: Text(saving ? 'Saving…' : 'Apply'),
            ),
          ],
        ),
      );
    },
  );
  if (saved == true) {
    messenger.showSnackBar(SnackBar(
        content: Text(appliedLive
            ? 'Applied live — the trackball behaves this way right now '
                '(and the setting is staged for future builds)'
            : 'Saved — ${applyDetail ?? 'rebuild + flash in the Firmware '
                'tab to apply on the keyboard'}')));
  }
}
