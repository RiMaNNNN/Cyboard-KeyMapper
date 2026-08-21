/// Firmware wizard: sign in → generate → build → flash RIGHT → flash LEFT
/// (which finishes automatically), as a sequential stepper.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import '../app_state.dart';
import '../models.dart';

/// Guided pipeline turning the current layout + staged sequences into
/// flashable firmware (with Studio locking disabled unless re-enabled in
/// the Advanced tab).
class FirmwareScreen extends StatefulWidget {
  const FirmwareScreen({super.key, required this.model});

  final AppModel model;

  @override
  State<FirmwareScreen> createState() => _FirmwareScreenState();
}

class _FirmwareScreenState extends State<FirmwareScreen> {
  FirmwareStatus? _status;
  Map<String, dynamic>? _generated;
  Timer? _poll;
  int _finalizeElapsed = 0;
  Timer? _finalizeTicker;
  int _step = 0;
  bool _stepPositioned = false;
  bool _flashInFlight = false;

  AppModel get model => widget.model;

  /// Upper estimate for the finalize flash erase, shown as a countdown (the
  /// erase can take longer on a well-used settings partition; after the
  /// estimate the bar turns indeterminate and the server keeps waiting).
  static const int _finalizeEstimateS = 60;

  /// Display names of the staged sequences, for the already-flashed hint.
  List<String> _stagedNames = const [];

  @override
  void initState() {
    super.initState();
    _refresh();
    _loadStagedNames();
    _poll = Timer.periodic(const Duration(seconds: 3), (_) => _refresh());
  }

  Future<void> _loadStagedNames() async {
    try {
      final macros = await widget.model.api.stagedMacros();
      if (mounted) {
        setState(
            () => _stagedNames = [for (final m in macros) m.displayName]);
      }
    } on Object {
      // Purely informational; the hint simply stays off.
    }
  }

  /// Whether every staged sequence already exists (by display name) among
  /// the behaviors the connected keyboard reports — i.e. the staged set is
  /// fully baked into the running firmware.
  bool _stagedAlreadyFlashed(FirmwareStatus status) {
    if (_stagedNames.isEmpty || _stagedNames.length != status.stagedMacros) {
      return false;
    }
    final known = {for (final b in model.behaviors) b.displayName};
    if (known.isEmpty) return false;
    return _stagedNames.every(known.contains);
  }

  @override
  void dispose() {
    _poll?.cancel();
    _finalizeTicker?.cancel();
    super.dispose();
  }

  Future<void> _refresh() async {
    try {
      final status = await widget.model.api.firmwareStatus();
      if (mounted) setState(() => _status = status);
    } on Object {
      // Status polling failures are transient; the next tick retries.
    }
  }

  Future<void> _run(Future<void> Function() action) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await action();
      await _refresh();
    } on Object catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  /// Whether the given step's goal is met (enables its "Next step" button).
  bool _stepReady(int step, FirmwareStatus status) => switch (step) {
    0 => status.githubAvailable && status.githubAuthenticated,
    1 => _generated != null,
    2 => status.jobPhase == 'done',
    3 => model.wizardRightFlashed,
    _ => model.wizardFinished,
  };

  @override
  Widget build(BuildContext context) {
    final status = _status;
    final theme = Theme.of(context);
    if (status == null) {
      return const Center(child: CircularProgressIndicator());
    }
    if (!_stepPositioned) {
      _step = _stepReady(0, status) ? 1 : 0;
      _stepPositioned = true;
    }

    StepState stepState(int index) => _stepReady(index, status)
        ? StepState.complete
        : (_step == index ? StepState.editing : StepState.indexed);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(24, 12, 24, 0),
          child: Card(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Text(
                'Follow the five steps IN ORDER, top to bottom. The last '
                'step flashes the LEFT half and then finishes everything by '
                'itself (it clears the old stored layout that would shadow '
                'the new keymap, and verifies the result). Why firmware at '
                'all? Sequences (macros) cannot be created over the wire: '
                'ZMK compiles them in. This wizard bakes your CURRENT layout '
                'plus the staged sequences and every Advanced-tab setting '
                '(locking, trackballs, power, colors, battery alert) into a '
                'custom firmware — the A+F unlock is removed unless you kept '
                'Studio locking on in the Advanced tab.',
                style: theme.textTheme.bodySmall,
              ),
            ),
          ),
        ),
        Expanded(
          child: Stepper(
            currentStep: _step,
            onStepTapped: (i) => setState(() => _step = i),
            controlsBuilder: (context, details) {
              final ready = _stepReady(_step, status);
              return Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Row(
                  children: [
                    if (_step < 4)
                      FilledButton.icon(
                        onPressed: ready
                            ? () => setState(() => _step += 1)
                            : null,
                        icon: const Icon(Icons.arrow_downward),
                        label: Text(
                          ready
                              ? 'Next step'
                              : 'Complete this step to continue',
                        ),
                      ),
                    if (_step > 0) ...[
                      const SizedBox(width: 8),
                      TextButton(
                        onPressed: () => setState(() => _step -= 1),
                        child: const Text('Back'),
                      ),
                    ],
                  ],
                ),
              );
            },
            steps: [
              Step(
                title: const Text('Sign in to GitHub'),
                state: stepState(0),
                isActive: _step >= 0,
                content: _githubContent(status),
              ),
              Step(
                title: const Text('Generate the firmware configuration'),
                state: stepState(1),
                isActive: _step >= 1,
                content: _generateContent(status, theme),
              ),
              Step(
                title: const Text('Build on GitHub Actions'),
                state: stepState(2),
                isActive: _step >= 2,
                content: _buildContent(status, theme),
              ),
              Step(
                title: const Text('Flash the RIGHT half'),
                state: stepState(3),
                isActive: _step >= 3,
                content: _flashSideContent(status, theme, left: false),
              ),
              Step(
                title: const Text('Flash the LEFT half — finishes by itself'),
                state:
                    model.wizardFinished ? StepState.complete : stepState(4),
                isActive: _step >= 4,
                content: _flashSideContent(status, theme, left: true),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _githubContent(FirmwareStatus status) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          status.githubAvailable
              ? (status.githubAuthenticated
                    ? 'Signed in as ${status.githubLogin}. Your private firmware '
                          'repository ("${status.githubLogin}/…") is created '
                          'automatically from Cyboard\'s official template on the '
                          'first build. The sign-in is remembered across runs.'
                    : 'GitHub CLI found, but no account is signed in. Click below: '
                          'a console window opens and your browser asks you to '
                          'approve. KeyMapper never sees your password or token — the '
                          'sign-in is stored by GitHub\'s own CLI and remembered '
                          'for future runs.')
              : 'GitHub CLI (gh) not found. Install it from cli.github.com, '
                    'then come back here.',
        ),
        const SizedBox(height: 8),
        if (status.githubAvailable && !status.githubAuthenticated)
          FilledButton.icon(
            onPressed: () => _run(() => widget.model.api.githubLogin()),
            icon: const Icon(Icons.login),
            label: const Text('Sign in with GitHub'),
          ),
      ],
    );
  }

  Widget _generateContent(FirmwareStatus status, ThemeData theme) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          '${status.stagedMacros} sequence(s) staged (Sequences tab)'
          '${_stagedAlreadyFlashed(status) ? ' — all of them are already in '
              'the connected firmware, so no rebuild is needed unless you '
              'changed something' : ''}. '
          'Generation snapshots the keyboard layout (a fresh backup is '
          'taken automatically when connected and unlocked).',
        ),
        const SizedBox(height: 8),
        FilledButton.icon(
          onPressed: () => _run(() async {
            final result = await widget.model.api.firmwareGenerate();
            setState(() => _generated = result);
          }),
          icon: const Icon(Icons.build_circle_outlined),
          label: const Text('Generate'),
        ),
        if (_generated != null) ...[
          const SizedBox(height: 8),
          Text('Files:', style: theme.textTheme.labelLarge),
          for (final f in (_generated!['files'] as List<dynamic>))
            Text('• $f', style: theme.textTheme.bodySmall),
          if ((_generated!['warnings'] as List<dynamic>).isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
              'Warnings — review before flashing:',
              style: theme.textTheme.labelLarge?.copyWith(
                color: theme.colorScheme.error,
              ),
            ),
            for (final w in (_generated!['warnings'] as List<dynamic>))
              Text(
                '• $w',
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.error,
                ),
              ),
          ],
        ],
      ],
    );
  }

  Widget _buildContent(FirmwareStatus status, ThemeData theme) {
    final jobRunning =
        status.jobPhase != null &&
        status.jobPhase != 'done' &&
        status.jobPhase != 'error';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            FilledButton.icon(
              onPressed: jobRunning
                  ? null
                  : () => _run(() => widget.model.api.firmwareBuild()),
              icon: const Icon(Icons.cloud_upload_outlined),
              label: const Text('Push & build'),
            ),
            const SizedBox(width: 12),
            if (jobRunning) ...[
              const SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
              const SizedBox(width: 8),
              Text('${status.jobPhase}: ${status.jobDetail ?? ""}'),
            ],
            if (status.jobPhase == 'error')
              Expanded(
                child: Text(
                  'Failed: ${status.jobError}',
                  style: TextStyle(color: theme.colorScheme.error),
                ),
              ),
          ],
        ),
        if (status.uf2Files.isNotEmpty) ...[
          const SizedBox(height: 8),
          const Text('Firmware ready:'),
          for (final f in status.uf2Files)
            Text(
              '• ${f.split(RegExp(r"[\\/]")).last}',
              style: theme.textTheme.bodySmall,
            ),
        ],
      ],
    );
  }

  /// One guided flash step: instructions for the given half, the drive
  /// indicator, and that half's flash button. Flashing the left half (the
  /// last step) automatically waits for the keyboard to reconnect and then
  /// finishes the installation (clears the old stored layout and verifies).
  Widget _flashSideContent(
    FirmwareStatus status,
    ThemeData theme, {
    required bool left,
  }) {
    final side = left ? 'LEFT' : 'RIGHT';
    final file = status.uf2Files
        .where((f) => f
            .split(RegExp(r"[\\/]"))
            .last
            .toLowerCase()
            .contains(left ? 'left' : 'right'))
        .toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Plug the $side half into this PC with the USB-C data cable and '
          'double-tap the reset button next to its USB port. A drive named '
          'ASSIMILATOR appears - KeyMapper detects it below. Then press the '
          'flash button.'
          '${left ? '\n\nAfter this flash, KeyMapper finishes everything '
              'automatically: it waits for the keyboard to reconnect, clears '
              'the old stored layout (a fresh backup is written first), and '
              'verifies the new keymap. Keep the left half on the cable.' : ''}',
        ),
        const SizedBox(height: 8),
        Row(
          children: [
            Icon(
              status.bootloaderDrive != null ? Icons.usb : Icons.usb_off,
              color: status.bootloaderDrive != null
                  ? Colors.green
                  : theme.colorScheme.outline,
            ),
            const SizedBox(width: 8),
            Text(
              status.bootloaderDrive != null
                  ? 'Bootloader drive detected at ${status.bootloaderDrive}'
                  : 'No bootloader drive detected - double-tap reset on the '
                      '$side half',
            ),
          ],
        ),
        const SizedBox(height: 8),
        if (file.isEmpty)
          const Text('No firmware file for this half - run the Build step.')
        else
          _flashButton(file.first, status.bootloaderDrive != null, left: left),
        if (model.wizardRightFlashed && !left)
          const Padding(
            padding: EdgeInsets.only(top: 8),
            child: Row(children: [
              Icon(Icons.check_circle, color: Colors.green),
              SizedBox(width: 8),
              Text('Right half flashed - continue with the LEFT half.'),
            ]),
          ),
        if (model.wizardLeftFlashed && left && !model.wizardFinished)
          const Padding(
            padding: EdgeInsets.only(top: 8),
            child: Row(children: [
              Icon(Icons.check_circle, color: Colors.green),
              SizedBox(width: 8),
              Text('Left half flashed.'),
            ]),
          ),
        if (!left && !model.wizardRightFlashed)
          TextButton(
            onPressed: () {
              model.updateWizard(() => model.wizardRightFlashed = true);
              setState(() => _step = 4);
            },
            child: const Text(
                'I already flashed the right half earlier - skip'),
          ),
        if (left &&
            !model.wizardLeftFlashed &&
            !model.wizardFinished &&
            !model.wizardFinalizing &&
            !model.wizardWaitingReconnect &&
            model.wizardFinishError == null)
          TextButton(
            onPressed: _autoFinish,
            child: const Text(
                'Already flashed both halves? Finish setup now'),
          ),
        if (left) ..._finishProgress(theme),
      ],
    );
  }

  /// The automatic finish, shown under the left-half flash button:
  /// reconnect wait, finalize countdown, done row, or a retry on failure.
  List<Widget> _finishProgress(ThemeData theme) {
    if (model.wizardWaitingReconnect) {
      return const [
        SizedBox(height: 10),
        Row(children: [
          SizedBox(
              width: 18,
              height: 18,
              child: CircularProgressIndicator(strokeWidth: 2)),
          SizedBox(width: 10),
          Expanded(
              child: Text('Left half flashed - waiting for the keyboard to '
                  'reconnect over USB...')),
        ]),
      ];
    }
    if (model.wizardFinalizing) {
      return [
        const SizedBox(height: 10),
        Row(
          children: [
            const SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(strokeWidth: 2)),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                _finalizeElapsed < _finalizeEstimateS
                    ? 'Finishing - clearing the old stored layout (slow flash '
                        'work), about '
                        '${_finalizeEstimateS - _finalizeElapsed} s left...'
                    : 'Finishing - almost done, still working '
                        '($_finalizeElapsed s)...',
              ),
            ),
          ],
        ),
        const SizedBox(height: 6),
        LinearProgressIndicator(
          value: _finalizeElapsed < _finalizeEstimateS
              ? _finalizeElapsed / _finalizeEstimateS
              : null,
        ),
      ];
    }
    if (model.wizardFinished) {
      return const [
        SizedBox(height: 10),
        Row(children: [
          Icon(Icons.check_circle, color: Colors.green),
          SizedBox(width: 8),
          Text('All done - new firmware active and verified. Enjoy!'),
        ]),
      ];
    }
    if (model.wizardFinishError != null) {
      return [
        const SizedBox(height: 10),
        Text('Automatic finish failed: ${model.wizardFinishError}',
            style: TextStyle(color: theme.colorScheme.error)),
        const SizedBox(height: 6),
        FilledButton.icon(
          onPressed: _autoFinish,
          icon: const Icon(Icons.task_alt),
          label: const Text('Finish setup (retry)'),
        ),
      ];
    }
    return const [];
  }

  /// One flash button with a hand icon for its half (Material's hand glyph,
  /// mirrored for the left side). A successful right flash advances to the
  /// left step; a successful left flash starts the automatic finish.
  Widget _flashButton(String path, bool driveReady, {required bool left}) {
    final fileName = path.split(RegExp(r"[\\/]")).last;
    final side = left ? 'Left' : 'Right';
    final Widget handIcon = left
        ? Transform.flip(
            flipX: true,
            child: const Icon(Icons.back_hand_outlined, size: 26),
          )
        : const Icon(Icons.back_hand_outlined, size: 26);
    return Tooltip(
      message: fileName,
      child: FilledButton.tonalIcon(
        onPressed: driveReady &&
                !_flashInFlight &&
                !model.wizardFinalizing &&
                !model.wizardWaitingReconnect
            ? () async {
                final messenger = ScaffoldMessenger.of(context);
                setState(() => _flashInFlight = true);
                try {
                  await widget.model.api.firmwareFlash(fileName);
                } on Object catch (e) {
                  messenger.showSnackBar(SnackBar(
                      content: Text('Flashing the $side half failed: $e — '
                          'the bootloader survives a failed flash; '
                          'double-tap reset and try again.')));
                  return;
                } finally {
                  if (mounted) setState(() => _flashInFlight = false);
                }
                if (left) {
                  model.updateWizard(() => model.wizardLeftFlashed = true);
                  await _autoFinish();
                } else {
                  model.updateWizard(() => model.wizardRightFlashed = true);
                  if (mounted) setState(() => _step = 4);
                  messenger.showSnackBar(const SnackBar(
                      content: Text('Right half flashed - now the LEFT '
                          'half: double-tap its reset and flash')));
                }
                await _refresh();
              }
            : null,
        icon: handIcon,
        label: Padding(
          padding: const EdgeInsets.symmetric(vertical: 10),
          child: Text('Push $side side Firmware'),
        ),
      ),
    );
  }

  /// Waits for the freshly flashed keyboard to reconnect, then clears the
  /// old stored layout and verifies - the user never runs Finalize by hand.
  /// Every failure names its phase and states what already succeeded.
  Future<void> _autoFinish() async {
    if (model.wizardWaitingReconnect || model.wizardFinalizing) return;
    model.updateWizard(() {
      model.wizardFinishError = null;
      model.wizardWaitingReconnect = true;
    });
    var connected = false;
    try {
      // The keyboard needs a moment to leave bootloader mode and
      // re-enumerate; a stale pre-flash "connected" would otherwise let
      // finalize race the reboot.
      await Future<void>.delayed(const Duration(seconds: 3));
      for (var i = 0; i < 30; i++) {
        await model.refreshState();
        if (model.snapshot.connected) {
          // Settle check: confirm the connection is stable, not a snapshot
          // from just before the reboot.
          await Future<void>.delayed(const Duration(seconds: 2));
          await model.refreshState();
          if (model.snapshot.connected) {
            connected = true;
            break;
          }
        }
        await Future<void>.delayed(const Duration(seconds: 2));
      }
    } on Object catch (e) {
      model.updateWizard(() {
        model.wizardWaitingReconnect = false;
        model.wizardFinishError =
            'The left half WAS flashed successfully, but checking the '
            'connection failed unexpectedly: $e. Retry below.';
      });
      return;
    }
    model.updateWizard(() => model.wizardWaitingReconnect = false);
    if (!connected) {
      model.updateWizard(() => model.wizardFinishError =
          'The left half WAS flashed successfully, but the keyboard did not '
          'reconnect over USB within 60 s. Check the LEFT half is on the '
          'USB-C DATA cable (and out of bootloader mode), then retry.');
      return;
    }
    await _runFinalize();
  }

  /// Clears the keyboard-stored layout (fresh backup first) and verifies the
  /// baked-in keymap, with the slow-flash countdown.
  Future<void> _runFinalize() async {
    if (model.wizardFinalizing) return;
    final messenger = ScaffoldMessenger.of(context);
    model.updateWizard(() {
      model.wizardFinalizing = true;
      model.wizardFinishError = null;
    });
    _finalizeElapsed = 0;
    _finalizeTicker?.cancel();
    _finalizeTicker = Timer.periodic(
      const Duration(seconds: 1),
      (_) {
        if (mounted) setState(() => _finalizeElapsed++);
      },
    );
    final Map<String, dynamic> result;
    try {
      result = await widget.model.api.firmwareFinalize();
    } on Object catch (e) {
      _finalizeTicker?.cancel();
      model.updateWizard(() {
        model.wizardFinalizing = false;
        model.wizardFinishError =
            'The flash succeeded and the keyboard reconnected, but clearing '
            'the old stored layout failed: $e. Nothing is lost — a backup '
            'was written first. Retry below.';
      });
      return;
    }
    _finalizeTicker?.cancel();
    // The keyboard-side work is complete: record success on the model so it
    // survives tab switches and mid-flight disposal.
    model.updateWizard(() {
      model.wizardFinalizing = false;
      model.wizardFinished = true;
    });
    try {
      await widget.model.loadEverything();
      messenger.showSnackBar(
        SnackBar(
          content: Text(
            'Done! Backup ${result["backup"]} written; '
            '${(result["keymap"]["layers"] as List).length} layers active.',
          ),
        ),
      );
    } on Object catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text('Finished successfully on the keyboard, but '
              'refreshing the app view failed: $e — press F5.')));
    }
  }
}
