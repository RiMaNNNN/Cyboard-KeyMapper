/// Sequence composer: build macros (unlimited "Add behavior" steps) and stage
/// them for the next firmware build.
library;

import 'package:flutter/material.dart';

import '../app_state.dart';
import '../keycodes.dart';
import '../models.dart';

/// Staging area for key-to-sequence mappings (compiled into firmware).
class SequencesScreen extends StatefulWidget {
  const SequencesScreen({super.key, required this.model});

  final AppModel model;

  @override
  State<SequencesScreen> createState() => _SequencesScreenState();
}

class _SequencesScreenState extends State<SequencesScreen> {
  List<MacroModel> _staged = [];
  List<MacroModel> _presets = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final staged = await widget.model.api.stagedMacros();
      final presets = await widget.model.api.firmwarePresets();
      setState(() {
        _staged = staged;
        _presets = presets;
        _loading = false;
      });
    } on Object catch (e) {
      setState(() => _loading = false);
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Load failed: $e')));
      }
    }
  }

  Future<void> _push() async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.model.api.stageMacros(_staged);
      messenger.showSnackBar(SnackBar(
          content: Text('${_staged.length} sequence(s) staged for firmware')));
    } on Object catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('Staging failed: $e')));
    }
  }

  /// Builds a sequence that opens any program, file, folder, or URL: it taps
  /// Win+R (the Run dialog accepts anything double-clickable), types the path,
  /// and presses Enter. Typing assumes a US-QWERTY Windows layout.
  Future<void> _openLauncherDialog() async {
    final nameController = TextEditingController();
    final pathController = TextEditingController();
    final created = await showDialog<MacroModel>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Open program / file from a key'),
        content: SizedBox(
          width: 480,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text(
                'The sequence opens the Windows Run dialog (Win+R), types '
                'your path, and presses Enter — so it can start any program, '
                'open any file or folder (like double-clicking it), or open '
                'a URL. Requires a US-QWERTY Windows keyboard layout for the '
                'typed characters to land correctly.',
              ),
              const SizedBox(height: 8),
              TextField(
                controller: nameController,
                decoration: const InputDecoration(
                    labelText: 'Sequence name', helperText: 'e.g. open_notes'),
              ),
              TextField(
                controller: pathController,
                decoration: const InputDecoration(
                  labelText: 'Path / command / URL',
                  helperText:
                      r'e.g. notepad, C:\tools\app.exe, D:\notes.txt, '
                      'https://example.com',
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Cancel')),
          FilledButton(
            onPressed: () {
              final path = pathController.text.trim();
              final name = nameController.text.trim().toLowerCase();
              if (path.isEmpty || name.isEmpty) return;
              final typed = bindingsForText(path);
              if (typed == null) {
                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                    content: Text('The path contains a character that cannot '
                        'be typed with plain key taps — remove accents or '
                        'special symbols (quotes around paths with spaces '
                        'are fine).')));
                return;
              }
              final steps = <MacroStepModel>[
                MacroStepModel(kind: 'tap', binding: '&kp LG(R)'),
                // Give the Run dialog time to open and take focus.
                MacroStepModel(kind: 'wait_ms', value: 500),
                MacroStepModel(kind: 'tap', binding: typed.first),
                MacroStepModel(kind: 'wait_ms', value: 25),
                for (final b in typed.skip(1))
                  MacroStepModel(kind: 'tap', binding: b),
                MacroStepModel(kind: 'tap', binding: '&kp RET'),
              ];
              Navigator.pop(
                  context,
                  MacroModel(
                    nodeName: name,
                    displayName: 'Open: $path',
                    steps: steps,
                    waitMs: 25,
                    tapMs: 15,
                  ));
            },
            child: const Text('Create sequence'),
          ),
        ],
      ),
    );
    if (created == null) return;
    setState(() {
      _staged.removeWhere((m) => m.nodeName == created.nodeName);
      _staged.add(created);
    });
    await _push();
  }

  Future<void> _editMacro([MacroModel? existing]) async {
    final result = await showDialog<MacroModel>(
      context: context,
      builder: (context) => Dialog(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 620, maxHeight: 720),
          child: _MacroEditor(macro: existing),
        ),
      ),
    );
    if (result == null) return;
    setState(() {
      final index =
          _staged.indexWhere((m) => m.nodeName == result.nodeName);
      if (existing != null) {
        final oldIndex =
            _staged.indexWhere((m) => m.nodeName == existing.nodeName);
        if (oldIndex >= 0) _staged.removeAt(oldIndex);
      }
      if (index >= 0 && existing == null) {
        _staged[index] = result;
      } else {
        _staged.add(result);
      }
    });
    await _push();
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Material(
          elevation: 2,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(24, 12, 24, 12),
            child: Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                FilledButton.icon(
                  onPressed: () => _editMacro(),
                  icon: const Icon(Icons.add),
                  label: const Text('New sequence'),
                ),
                OutlinedButton.icon(
                  onPressed: _openLauncherDialog,
                  icon: const Icon(Icons.rocket_launch_outlined),
                  label: const Text('Open program / file…'),
                ),
                OutlinedButton.icon(
                  onPressed: () async {
                    setState(() {
                      for (final preset in _presets) {
                        if (!_staged.any((m) => m.nodeName == preset.nodeName)) {
                          _staged.add(preset);
                        }
                      }
                    });
                    await _push();
                  },
                  icon: const Icon(Icons.language),
                  label: const Text('Add all presets (€ è é à ì ù ò)'),
                ),
                for (final preset in _presets)
                  ActionChip(
                    label: Text(preset.displayName),
                    onPressed: () async {
                      if (!_staged.any((m) => m.nodeName == preset.nodeName)) {
                        setState(() => _staged.add(preset));
                        await _push();
                      }
                    },
                  ),
              ],
            ),
          ),
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.all(24),
            children: [
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Key → sequence mappings',
                          style: theme.textTheme.titleLarge),
                      const SizedBox(height: 8),
                      const Text(
                        'Sequences (macros) run several behaviors from one key '
                        'press: accented letters via Windows Alt-codes, "go to '
                        'layer AND set underglow color", or any chain you '
                        'compose — as many steps as you want. ZMK compiles '
                        'macros into the firmware, so after composing them here '
                        'you build & flash once in the Firmware tab; each '
                        'sequence then appears as a normal behavior you can put '
                        'on any key in the Editor.\n\n'
                        'Alt-code sequences manage Num Lock automatically: '
                        'the &nl_guard wrap switches it on when the PC has it '
                        'off and restores it afterwards.\n\n'
                        'Tip — hold-a-layer with color that restores itself: '
                        'press &rgb_mem, press &mo N, tap &rgb_ug '
                        'RGB_COLOR_HSB(...), pause until released, release '
                        '&mo N, release &rgb_mem. The &rgb_mem behavior '
                        'memorizes the color on press and restores it on '
                        'release, whatever it was.',
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 8),
              ..._stagedCards(),
            ],
          ),
        ),
      ],
    );
  }

  List<Widget> _stagedCards() {
    return [
      if (_staged.isEmpty)
        const Card(child: ListTile(title: Text('No sequences staged yet')))
      else
        for (final macro in _staged)
            Card(
              child: ListTile(
                leading: const Icon(Icons.playlist_play),
                title: Text(macro.displayName),
                subtitle: Text(
                    '${macro.nodeName} — ${macro.steps.length} step(s): '
                    '${macro.steps.map(_stepLabel).join(" → ")}'
                    '${macro.shiftedSteps.isEmpty ? "" : "\n⇧ SHIFT variant: "
                        "${macro.shiftedSteps.map(_stepLabel).join(" → ")}"}'),
                trailing: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    IconButton(
                        onPressed: () => _editMacro(macro),
                        icon: const Icon(Icons.edit)),
                    IconButton(
                      onPressed: () async {
                        setState(() => _staged
                            .removeWhere((m) => m.nodeName == macro.nodeName));
                        await _push();
                      },
                      icon: const Icon(Icons.delete_outline),
                    ),
                  ],
                ),
              ),
            ),
    ];
  }

  static String _stepLabel(MacroStepModel step) => switch (step.kind) {
        'tap' => 'tap ${step.binding}',
        'press' => 'hold ${step.binding}',
        'release' => 'release ${step.binding}',
        'wait_ms' => 'wait ${step.value}ms',
        'tap_ms' => 'tap-time ${step.value}ms',
        _ => 'pause-for-release',
      };
}

class _MacroEditor extends StatefulWidget {
  const _MacroEditor({this.macro});

  final MacroModel? macro;

  @override
  State<_MacroEditor> createState() => _MacroEditorState();
}

class _MacroEditorState extends State<_MacroEditor> {
  late final TextEditingController _name;
  late final TextEditingController _label;
  late List<MacroStepModel> _steps;
  late List<MacroStepModel> _shiftedSteps;
  late int _waitMs;
  late int _tapMs;

  @override
  void initState() {
    super.initState();
    final m = widget.macro;
    _name = TextEditingController(text: m?.nodeName ?? '');
    _label = TextEditingController(text: m?.displayName ?? '');
    _steps = [
      for (final s in m?.steps ?? <MacroStepModel>[])
        MacroStepModel(kind: s.kind, binding: s.binding, value: s.value)
    ];
    _shiftedSteps = [
      for (final s in m?.shiftedSteps ?? <MacroStepModel>[])
        MacroStepModel(kind: s.kind, binding: s.binding, value: s.value)
    ];
    _waitMs = m?.waitMs ?? 30;
    _tapMs = m?.tapMs ?? 30;
  }

  Future<void> _addStep(List<MacroStepModel> target) async {
    final step = await _editStep(MacroStepModel(kind: 'tap', binding: '&kp A'));
    if (step != null) setState(() => target.add(step));
  }

  Widget _stepsSection(String title, List<MacroStepModel> target) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Expanded(
              child: Text('$title (${target.length})',
                  style: Theme.of(context).textTheme.titleSmall),
            ),
            TextButton.icon(
                onPressed: () => _addStep(target),
                icon: const Icon(Icons.add),
                label: const Text('Add behavior')),
          ],
        ),
        for (int i = 0; i < target.length; i++)
          ListTile(
            dense: true,
            leading: Text('${i + 1}'),
            title: Text(_SequencesScreenState._stepLabel(target[i])),
            trailing: Row(mainAxisSize: MainAxisSize.min, children: [
              IconButton(
                icon: const Icon(Icons.arrow_upward, size: 18),
                onPressed: i > 0
                    ? () => setState(
                        () => target.insert(i - 1, target.removeAt(i)))
                    : null,
              ),
              IconButton(
                icon: const Icon(Icons.edit, size: 18),
                onPressed: () async {
                  final edited = await _editStep(target[i]);
                  if (edited != null) setState(() => target[i] = edited);
                },
              ),
              IconButton(
                icon: const Icon(Icons.delete_outline, size: 18),
                onPressed: () => setState(() => target.removeAt(i)),
              ),
            ]),
          ),
        const Divider(),
      ],
    );
  }

  Future<MacroStepModel?> _editStep(MacroStepModel step) {
    return showDialog<MacroStepModel>(
      context: context,
      builder: (context) {
        String kind = step.kind;
        String binding = step.binding;
        int value = step.value;
        return StatefulBuilder(builder: (context, setLocal) {
          final needsBinding =
              kind == 'tap' || kind == 'press' || kind == 'release';
          final needsValue = kind == 'wait_ms' || kind == 'tap_ms';
          return AlertDialog(
            title: const Text('Behavior step'),
            content: SizedBox(
              width: 420,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  DropdownButtonFormField<String>(
                    initialValue: kind,
                    decoration: const InputDecoration(labelText: 'Step type'),
                    items: const [
                      DropdownMenuItem(
                          value: 'tap', child: Text('Tap (press + release)')),
                      DropdownMenuItem(
                          value: 'press', child: Text('Press and hold')),
                      DropdownMenuItem(value: 'release', child: Text('Release')),
                      DropdownMenuItem(
                          value: 'wait_ms', child: Text('Change wait time')),
                      DropdownMenuItem(
                          value: 'tap_ms', child: Text('Change tap time')),
                      DropdownMenuItem(
                          value: 'pause_for_release',
                          child: Text('Pause until key released')),
                    ],
                    onChanged: (v) => setLocal(() => kind = v ?? kind),
                  ),
                  if (needsBinding) ...[
                    TextFormField(
                      initialValue: binding,
                      decoration: const InputDecoration(
                        labelText: 'Behavior (devicetree form)',
                        helperText:
                            'e.g. &kp LS(A), &to 1, &rgb_ug RGB_COLOR_HSB(60,100,100)',
                      ),
                      onChanged: (v) => binding = v,
                    ),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: Wrap(spacing: 4, children: [
                        for (final quick in [
                          '&kp ', '&to ', '&mo ', '&rgb_ug ', '&rgb_mem'
                        ])
                          ActionChip(
                            label: Text(quick.trim()),
                            onPressed: () => setLocal(() => binding = quick),
                          ),
                        PopupMenuButton<Keycode>(
                          child: const Chip(label: Text('key…')),
                          itemBuilder: (context) => [
                            for (final k in keycodeCatalog.take(60))
                              PopupMenuItem(value: k, child: Text(k.name)),
                          ],
                          onSelected: (k) =>
                              setLocal(() => binding = '&kp ${k.name}'),
                        ),
                      ]),
                    ),
                  ],
                  if (needsValue)
                    TextFormField(
                      initialValue: '$value',
                      decoration:
                          const InputDecoration(labelText: 'Milliseconds'),
                      keyboardType: TextInputType.number,
                      onChanged: (v) => value = int.tryParse(v) ?? value,
                    ),
                ],
              ),
            ),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(context),
                  child: const Text('Cancel')),
              FilledButton(
                onPressed: () => Navigator.pop(context,
                    MacroStepModel(kind: kind, binding: binding.trim(), value: value)),
                child: const Text('OK'),
              ),
            ],
          );
        });
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(widget.macro == null ? 'New sequence' : 'Edit sequence',
              style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 8),
          Row(children: [
            Expanded(
              child: TextField(
                controller: _name,
                decoration: const InputDecoration(
                    labelText: 'Identifier (a-z, 0-9, _)',
                    helperText: 'e.g. my_email'),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: TextField(
                controller: _label,
                decoration: const InputDecoration(
                    labelText: 'Display name', helperText: 'shown in pickers'),
              ),
            ),
          ]),
          Row(children: [
            Expanded(
              child: TextFormField(
                initialValue: '$_waitMs',
                decoration: const InputDecoration(
                    labelText: 'Wait between steps (ms)',
                    helperText: '30 for typed output; 0 for instant'),
                keyboardType: TextInputType.number,
                onChanged: (v) => _waitMs = int.tryParse(v) ?? _waitMs,
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: TextFormField(
                initialValue: '$_tapMs',
                decoration: const InputDecoration(
                    labelText: 'Tap duration (ms)',
                    helperText: '30 for typed output; 0 for instant'),
                keyboardType: TextInputType.number,
                onChanged: (v) => _tapMs = int.tryParse(v) ?? _tapMs,
              ),
            ),
          ]),
          const SizedBox(height: 8),
          Expanded(
            child: ListView(
              children: [
                _stepsSection('Steps', _steps),
                _stepsSection(
                  '⇧ SHIFT variant (runs instead when Shift is held; the held '
                  'Shift is masked while it types)',
                  _shiftedSteps,
                ),
              ],
            ),
          ),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              TextButton(
                  onPressed: () => Navigator.pop(context),
                  child: const Text('Cancel')),
              const SizedBox(width: 8),
              FilledButton(
                onPressed: () {
                  final name = _name.text.trim().toLowerCase();
                  if (name.isEmpty || _steps.isEmpty) return;
                  Navigator.pop(
                    context,
                    MacroModel(
                      nodeName: name,
                      displayName: _label.text.trim().isEmpty
                          ? name
                          : _label.text.trim(),
                      steps: _steps,
                      waitMs: _waitMs,
                      tapMs: _tapMs,
                      shiftedSteps: _shiftedSteps,
                    ),
                  );
                },
                child: const Text('Save sequence'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
