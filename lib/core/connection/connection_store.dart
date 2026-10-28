import 'dart:async';

import 'package:get/get.dart';

import '../api/core_api_client.dart';
import '../api/models.dart';
import '../events/event_stream_client.dart';
import 'control_endpoint.dart';

enum CoreStatus { unknown, notRunning, starting, running, stopping }

/// 核心服务的连接状态。定时读 control.json 并调 /health；服务重启后自动重连，状态从服务重新拉取。
class ConnectionStore extends GetxService {
  ConnectionStore({required this.controlFile, this.pollInterval = const Duration(seconds: 3)});

  final String controlFile;
  final Duration pollInterval;

  final status = CoreStatus.unknown.obs;
  final health = Rxn<HealthInfo>();
  final lastError = RxnString();
  final events = <CoreEvent>[].obs;

  ControlEndpoint? _endpoint;
  CoreApiClient? _api;
  EventStreamClient? _eventClient;
  StreamSubscription<CoreEvent>? _eventSub;
  Timer? _timer;
  bool _refreshing = false;

  static const _maxEvents = 200;

  ControlEndpoint? get endpoint => _endpoint;
  CoreApiClient? get api => _api;

  bool get apiVersionMismatch {
    final h = health.value;
    return h != null && h.apiVersion != supportedApiVersion;
  }

  @override
  void onInit() {
    super.onInit();
    _timer = Timer.periodic(pollInterval, (_) => refresh());
    refresh();
  }

  @override
  void onClose() {
    _timer?.cancel();
    _disconnect();
    super.onClose();
  }

  /// CoreProcessManager 在启动、停止期间设置过渡状态，轮询不会把它覆盖成「未运行」。
  void setTransition(CoreStatus value) => status.value = value;

  Future<void> refresh() async {
    if (_refreshing) return;
    _refreshing = true;
    try {
      final ep = await ControlEndpoint.read(controlFile);
      if (ep == null) {
        _disconnect();
        health.value = null;
        if (status.value != CoreStatus.starting) status.value = CoreStatus.notRunning;
        return;
      }
      if (_endpoint == null || _endpoint!.port != ep.port || _endpoint!.token != ep.token) {
        _disconnect();
        _endpoint = ep;
        _api = CoreApiClient(ep);
      }
      try {
        health.value = await _api!.health();
        lastError.value = null;
        if (status.value != CoreStatus.stopping) status.value = CoreStatus.running;
        if (_eventSub == null) _subscribeEvents(ep);
      } on Object catch (e) {
        // control.json 还在但接口不通：服务刚崩溃，或者正在启动 / 停止。
        health.value = null;
        lastError.value = e.toString();
        if (status.value == CoreStatus.running || status.value == CoreStatus.unknown) {
          status.value = CoreStatus.notRunning;
        }
      }
    } finally {
      _refreshing = false;
    }
  }

  void _subscribeEvents(ControlEndpoint ep) {
    final client = EventStreamClient(ep);
    _eventClient = client;
    _eventSub = client.connect().listen(
      (event) {
        events.insert(0, event);
        if (events.length > _maxEvents) events.removeRange(_maxEvents, events.length);
      },
      onError: (_) => _dropEvents(),
      onDone: _dropEvents,
      cancelOnError: true,
    );
  }

  void _dropEvents() {
    _eventSub = null;
    _eventClient = null;
  }

  void _disconnect() {
    _eventSub?.cancel();
    _eventClient?.close();
    _dropEvents();
    _api?.close();
    _api = null;
    _endpoint = null;
  }
}
