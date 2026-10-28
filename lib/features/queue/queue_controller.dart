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

  static const _topics = {'inbox.new', 'turn.done', 'turn.failed', 'queue.changed'};

  @override
  void onInit() {
    super.onInit();
    ever(repository.coreStatus, (status) {
      if (status == CoreStatus.running) load();
    });
    debounce(repository.events, (_) {
      final events = repository.events;
      if (events.isNotEmpty && (_topics.contains(events.first.topic) || events.first.topic.startsWith('outbox.'))) {
        load();
      }
    }, time: const Duration(milliseconds: 300));
    ever(inboxStatus, (_) => load());
    ever(outboxStatus, (_) => load());
    if (coreRunning) load();
  }

  Future<void> load() async {
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
    }
  }

  Future<String?> retryInbox(int id) => _run(() => repository.retryInbox(id));
  Future<String?> retryOutbox(int id) => _run(() => repository.retryOutbox(id));
  Future<String?> cancelOutbox(int id) => _run(() => repository.cancelOutbox(id));

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
