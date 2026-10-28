import 'package:get/get.dart';

import '../../core/api/core_api_client.dart';
import '../../core/api/models.dart';
import '../../core/connection/connection_store.dart';

/// 日志页的数据来源：运行事件（system_events 表）和核心服务的日志文件。
class LogsRepository {
  LogsRepository({ConnectionStore? store}) : store = store ?? Get.find<ConnectionStore>();

  final ConnectionStore store;

  CoreApiClient get _api {
    final api = store.api;
    if (api == null || store.status.value != CoreStatus.running) throw CoreApiException('核心服务未运行');
    return api;
  }

  Rx<CoreStatus> get coreStatus => store.status;
  RxList<CoreEvent> get events => store.events;

  Future<List<SystemEventItem>> systemEvents(String? level) => _api.systemEvents(level: level);
  Future<({String file, List<String> lines})> logTail() => _api.logTail();
}
