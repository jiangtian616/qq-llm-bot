import 'package:get/get.dart';

import '../../core/api/core_api_client.dart';
import '../../core/api/models.dart';
import '../../core/connection/connection_store.dart';

/// 消息队列页的数据来源：收件箱和发件箱，直接读核心服务的两张表。
class QueueRepository {
  QueueRepository({ConnectionStore? store}) : store = store ?? Get.find<ConnectionStore>();

  final ConnectionStore store;

  CoreApiClient get _api {
    final api = store.api;
    if (api == null || store.status.value != CoreStatus.running) throw CoreApiException('核心服务未运行');
    return api;
  }

  Rx<CoreStatus> get coreStatus => store.status;
  RxList<CoreEvent> get events => store.events;

  Future<List<InboxItem>> inbox(String? status) => _api.listInbox(status: status);
  Future<List<OutboxItem>> outbox(String? status) => _api.listOutbox(status: status);
  Future<({Map<String, int> inbox, Map<String, int> outbox})> stats() => _api.stats();
  Future<void> retryInbox(int id) => _api.retryInbox(id);
  Future<void> retryOutbox(int id) => _api.retryOutbox(id);
  Future<void> cancelOutbox(int id) => _api.cancelOutbox(id);
}
