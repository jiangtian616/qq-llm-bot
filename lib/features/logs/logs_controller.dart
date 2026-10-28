import 'dart:async';

import 'package:get/get.dart';

import '../../core/api/models.dart';
import '../../core/connection/connection_store.dart';
import 'logs_repository.dart';

class LogsController extends GetxController {
  LogsController(this.repository);

  final LogsRepository repository;

  final systemEvents = <SystemEventItem>[].obs;
  final level = RxnString();
  final logLines = <String>[].obs;
  final logFile = ''.obs;
  final autoRefresh = true.obs;
  final error = RxnString();

  Timer? _timer;

  bool get coreRunning => repository.coreStatus.value == CoreStatus.running;

  @override
  void onInit() {
    super.onInit();
    ever(repository.coreStatus, (status) {
      if (status == CoreStatus.running) load();
    });
    ever(level, (_) => loadEvents());
    // 运行事件由核心服务写库时没有单独推送，跟着连接状态等事件一起刷新。
    debounce(repository.events, (_) => loadEvents(), time: const Duration(milliseconds: 500));
    _timer = Timer.periodic(const Duration(seconds: 3), (_) {
      if (autoRefresh.value && coreRunning) loadLog();
    });
    if (coreRunning) load();
  }

  @override
  void onClose() {
    _timer?.cancel();
    super.onClose();
  }

  Future<void> load() async {
    await Future.wait([loadEvents(), loadLog()]);
  }

  Future<void> loadEvents() async {
    if (!coreRunning) return;
    try {
      systemEvents.assignAll(await repository.systemEvents(level.value));
      error.value = null;
    } on Object catch (e) {
      error.value = e.toString();
    }
  }

  Future<void> loadLog() async {
    if (!coreRunning) return;
    try {
      final tail = await repository.logTail();
      logFile.value = tail.file;
      logLines.assignAll(tail.lines);
    } on Object catch (e) {
      error.value = e.toString();
    }
  }
}
