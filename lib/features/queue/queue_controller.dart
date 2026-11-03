import 'dart:async';

import 'package:get/get.dart';

import '../../core/api/models.dart';
import '../../core/connection/connection_store.dart';
import 'queue_repository.dart';

class QueueController extends GetxController {
  QueueController(this.repository);

  final QueueRepository repository;

  final inbox = <InboxItem>[].obs;
  final outbox = <OutboxItem>[].obs;
  final inboxStatus = RxnString();
  final outboxStatus = RxnString();
  final inboxCounts = <String, int>{}.obs;
  final outboxCounts = <String, int>{}.obs;
  final error = RxnString();

  bool get coreRunning => repository.coreStatus.value == CoreStatus.running;

  static const _topics = {'inbox.new', 'queue.changed', 'maintenance.done'};

  Timer? _reloadTimer;
  bool _loading = false;
  bool _reloadPending = false;

  @override
  void onInit() {
    super.onInit();
    ever(repository.coreStatus, (status) {
      if (status == CoreStatus.running) load();
    });
    // 每来一条相关事件都记下，300 毫秒内的合并成一次刷新；不用 debounce，消息不断时它会一直等下去。
    ever(repository.events, (events) {
      if (events.isNotEmpty && (_topics.contains(events.first.topic) || events.first.topic.startsWith('outbox.'))) {
        _reloadTimer ??= Timer(const Duration(milliseconds: 300), () {
          _reloadTimer = null;
          load();
        });
      }
    });
    ever(inboxStatus, (_) => load());
    ever(outboxStatus, (_) => load());
    if (coreRunning) load();
  }

  @override
  void onClose() {
    _reloadTimer?.cancel();
    super.onClose();
  }

  /// 同一时间只拉一次；拉取中又要刷新的，等这次完成后再拉一次，免得旧结果盖掉新结果。
  Future<void> load() async {
    if (_loading) {
      _reloadPending = true;
      return;
    }
    _loading = true;
    try {
      final results = await Future.wait([
        repository.inbox(inboxStatus.value),
        repository.outbox(outboxStatus.value),
        repository.stats(),
      ]);
      inbox.assignAll(results[0] as List<InboxItem>);
      outbox.assignAll(results[1] as List<OutboxItem>);
      final stats = results[2] as ({Map<String, int> inbox, Map<String, int> outbox});
      inboxCounts.assignAll(stats.inbox);
      outboxCounts.assignAll(stats.outbox);
      error.value = null;
    } on Object catch (e) {
      error.value = e.toString();
    } finally {
      _loading = false;
      if (_reloadPending) {
        _reloadPending = false;
        unawaited(load());
      }
    }
  }

  Future<String?> retryInbox(int id) => _run(() => repository.retryInbox(id));
  Future<String?> retryOutbox(int id) => _run(() => repository.retryOutbox(id));
  Future<String?> cancelOutbox(int id) => _run(() => repository.cancelOutbox(id));
  Future<String?> recallOutbox(int id) => _run(() => repository.recallOutbox(id));

  Future<String?> _run(Future<void> Function() action) async {
    try {
      await action();
      await load();
      return null;
    } on Object catch (e) {
      return e.toString();
    }
  }
}
