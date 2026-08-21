/// HTTP + WebSocket client for the KeyMapper backend (same-origin).
library;

import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';

import 'models.dart';

/// Error carrying the backend's HTTP status and detail message.
class ApiException implements Exception {
  ApiException(this.statusCode, this.detail);

  final int statusCode;
  final String detail;

  /// Whether the keyboard rejected the call because Studio access is locked.
  bool get isLocked => statusCode == 423;

  @override
  String toString() => 'API $statusCode: $detail';
}

/// Thin typed wrapper over the backend REST + WebSocket API.
class ApiClient {
  ApiClient({Uri? base}) : _base = base ?? Uri.base;

  final Uri _base;

  Uri _api(String path) => _base.replace(path: path, query: '');

  Future<dynamic> _decode(http.Response response) async {
    final body = response.body.isEmpty ? null : jsonDecode(response.body);
    if (response.statusCode >= 400) {
      final detail = body is Map<String, dynamic>
          ? (body['detail']?.toString() ?? response.body)
          : response.body;
      throw ApiException(response.statusCode, detail);
    }
    return body;
  }

  Future<dynamic> _get(String path) async =>
      _decode(await http.get(_api(path)));

  Future<dynamic> _send(String method, String path,
      [Map<String, dynamic>? body]) async {
    final request = http.Request(method, _api(path));
    if (body != null) {
      request.headers['content-type'] = 'application/json';
      request.body = jsonEncode(body);
    }
    final streamed = await request.send();
    return _decode(await http.Response.fromStream(streamed));
  }

  /// Opens the state WebSocket; emits the raw decoded message per push —
  /// full state snapshots plus slim `event` messages (capture presses).
  Stream<Map<String, dynamic>> stateStream() {
    final wsScheme = _base.scheme == 'https' ? 'wss' : 'ws';
    final channel = WebSocketChannel.connect(
        _base.replace(scheme: wsScheme, path: '/ws', query: ''));
    return channel.stream
        .map((raw) => jsonDecode(raw as String) as Map<String, dynamic>);
  }

  /// Starts or stops the live capture press stream (pushed over the state
  /// WebSocket as `capture_press` events). Returns whether the stream is
  /// live plus an explanation when it is not.
  Future<(bool, String?)> captureWatch(bool on) async {
    final body = await _send('POST', '/api/capture/watch', {'on': on})
        as Map<String, dynamic>;
    return (body['supported'] == true, body['detail'] as String?);
  }

  Future<StateSnapshot> getState() async => StateSnapshot.fromJson(
      await _get('/api/state') as Map<String, dynamic>);

  Future<StateSnapshot> connectNow() async => StateSnapshot.fromJson(
      await _send('POST', '/api/connect') as Map<String, dynamic>);

  Future<KeymapModel> getKeymap() async =>
      KeymapModel.fromJson(await _get('/api/keymap') as Map<String, dynamic>);

  Future<PhysicalLayoutsModel> getLayouts() async => PhysicalLayoutsModel
      .fromJson(await _get('/api/layouts') as Map<String, dynamic>);

  Future<List<BehaviorModel>> getBehaviors() async =>
      (await _get('/api/behaviors') as List<dynamic>)
          .map((b) => BehaviorModel.fromJson(b as Map<String, dynamic>))
          .toList();

  Future<void> setBinding(
          int layerId, int keyPosition, int behaviorId, int param1, int param2) =>
      _send('PUT', '/api/binding', {
        'layer_id': layerId,
        'key_position': keyPosition,
        'behavior_id': behaviorId,
        'param1': param1,
        'param2': param2,
      });

  Future<int> bulkSet(int layerId, int behaviorId, int param1, int param2) async {
    final result = await _send('POST', '/api/bulk_set', {
      'layer_id': layerId,
      'behavior_id': behaviorId,
      'param1': param1,
      'param2': param2,
    }) as Map<String, dynamic>;
    return result['positions'] as int;
  }

  Future<String> save() async =>
      ((await _send('POST', '/api/save')) as Map<String, dynamic>)['backup']
          as String;

  Future<void> discard() => _send('POST', '/api/discard');

  Future<String> backupNow() async =>
      ((await _send('POST', '/api/backup')) as Map<String, dynamic>)['backup']
          as String;

  Future<List<BackupEntry>> listBackups() async =>
      (await _get('/api/backups') as List<dynamic>)
          .map((b) => BackupEntry.fromJson(b as Map<String, dynamic>))
          .toList();

  Future<Map<String, dynamic>> backupContent(String name) async =>
      await _get('/api/backups/$name') as Map<String, dynamic>;

  Future<Map<String, dynamic>> addLayer() async =>
      await _send('POST', '/api/layer/add') as Map<String, dynamic>;

  Future<void> removeLayer(int layerIndex) =>
      _send('POST', '/api/layer/remove', {'layer_index': layerIndex});

  Future<void> restoreLayer(int layerId, int atIndex) => _send(
      'POST', '/api/layer/restore', {'layer_id': layerId, 'at_index': atIndex});

  Future<void> moveLayer(int startIndex, int destIndex) => _send('POST',
      '/api/layer/move', {'start_index': startIndex, 'dest_index': destIndex});

  Future<void> renameLayer(int layerId, String name) =>
      _send('PUT', '/api/layer/name', {'layer_id': layerId, 'name': name});

  Future<Map<String, dynamic>> resetSettings() async =>
      await _send('POST', '/api/reset_settings', {'confirm': true})
          as Map<String, dynamic>;

  Future<List<MacroModel>> firmwarePresets() async =>
      (await _get('/api/firmware/presets') as List<dynamic>)
          .map((m) => MacroModel.fromJson(m as Map<String, dynamic>))
          .toList();

  Future<List<MacroModel>> stagedMacros() async =>
      (await _get('/api/firmware/macros') as List<dynamic>)
          .map((m) => MacroModel.fromJson(m as Map<String, dynamic>))
          .toList();

  Future<void> stageMacros(List<MacroModel> macros) =>
      _send('POST', '/api/firmware/macros',
          {'macros': macros.map((m) => m.toJson()).toList()});

  Future<FirmwareStatus> firmwareStatus() async => FirmwareStatus.fromJson(
      await _get('/api/firmware/status') as Map<String, dynamic>);

  Future<void> githubLogin() => _send('POST', '/api/firmware/github_login');

  Future<Map<String, dynamic>> firmwareGenerate() async =>
      await _send('POST', '/api/firmware/generate', {'confirm': true})
          as Map<String, dynamic>;

  Future<void> firmwareBuild() =>
      _send('POST', '/api/firmware/build', {'confirm': true});

  Future<void> firmwareFlash(String fileName) => _send(
      'POST', '/api/firmware/flash', {'file': fileName, 'confirm': true});

  Future<Map<String, dynamic>> firmwareFinalize() async =>
      await _send('POST', '/api/firmware/finalize', {'confirm': true})
          as Map<String, dynamic>;

  Future<(List<(String, int)>, String?)> getBattery() async {
    final body = await _get('/api/battery') as Map<String, dynamic>;
    final halves = [
      for (final h in (body['halves'] as List<dynamic>))
        (
          (h as Map<String, dynamic>)['label'] as String,
          h['percent'] as int,
        ),
    ];
    return (halves, body['detail'] as String?);
  }

  Future<int> setLayerColor(int layer, int hue, int saturation, int brightness) async {
    final result = await _send('POST', '/api/firmware/layer_color', {
      'layer': layer,
      'hue': hue,
      'saturation': saturation,
      'brightness': brightness,
    }) as Map<String, dynamic>;
    return result['updated'] as int;
  }

  Future<int> backupsDelete(List<String> names) async {
    final result = await _send('POST', '/api/backups/delete',
        {'names': names, 'confirm': true}) as Map<String, dynamic>;
    return result['deleted'] as int;
  }

  Future<Map<String, dynamic>> backupDiff(String name) async =>
      await _get('/api/backups/$name/diff') as Map<String, dynamic>;

  Future<Map<String, dynamic>> backupRestore(String name) async =>
      await _send('POST', '/api/backups/restore',
          {'name': name, 'confirm': true}) as Map<String, dynamic>;

  /// Same-origin URL to download one backup file.
  Uri backupDownloadUrl(String name) =>
      _api('/api/backups/$name/download');

  /// Same-origin URL of the full manual.
  Uri manualUrl() => _api('/manual');

  Future<int> setSequenceBrightness(int brightness) async {
    final result = await _send('POST', '/api/firmware/sequence_brightness',
        {'brightness': brightness}) as Map<String, dynamic>;
    return result['updated'] as int;
  }

  /// Last key pressed on the keyboard itself as (press counter, position,
  /// active-layer bitmask — null on firmware predating layer reporting), or
  /// null when the flashed firmware lacks the capture service.
  Future<(int, int, int?)?> getCapturePress() async {
    final body = await _get('/api/capture/press') as Map<String, dynamic>;
    if (body['supported'] != true) return null;
    return (
      body['counter'] as int,
      body['position'] as int,
      body['layers'] as int?,
    );
  }

  Future<BatteryAlertModel> getBatteryAlert() async => BatteryAlertModel
      .fromJson(await _get('/api/firmware/battery_alert') as Map<String, dynamic>);

  Future<void> putBatteryAlert(BatteryAlertModel alert) =>
      _send('PUT', '/api/firmware/battery_alert', alert.toJson());

  /// Idle / deep-sleep settings compiled into the next firmware build.
  Future<PowerModel> getPower() async => PowerModel.fromJson(
      await _get('/api/firmware/power') as Map<String, dynamic>);

  /// Updates and persists the idle / deep-sleep settings.
  Future<void> putPower(PowerModel power) =>
      _send('PUT', '/api/firmware/power', power.toJson());

  /// Studio-locking setting compiled into the next firmware build.
  Future<LockingModel> getLocking() async => LockingModel.fromJson(
      await _get('/api/firmware/locking') as Map<String, dynamic>);

  /// Updates and persists the Studio-locking setting.
  Future<void> putLocking(LockingModel locking) =>
      _send('PUT', '/api/firmware/locking', locking.toJson());

  /// Trackball settings compiled into the next firmware build.
  Future<TrackballModel> getTrackballs() async => TrackballModel.fromJson(
      await _get('/api/firmware/trackballs') as Map<String, dynamic>);

  /// Updates and persists the trackball settings. Returns whether the
  /// keyboard applied them live over Bluetooth, plus an explanation when it
  /// could not (older firmware, keyboard unreachable).
  Future<(bool, String?)> putTrackballs(TrackballModel trackballs) async {
    final body = await _send(
        'PUT', '/api/firmware/trackballs', trackballs.toJson())
        as Map<String, dynamic>;
    return (body['applied_live'] == true, body['detail'] as String?);
  }
}
