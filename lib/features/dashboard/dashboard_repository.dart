import 'package:get/get.dart';

import '../../core/api/models.dart';
import '../../core/connection/connection_store.dart';
import '../../platform/core_process_manager.dart';

/// 仪表盘的数据来源：核心服务的连接状态和进程管理。
class DashboardRepository {
  DashboardRepository({ConnectionStore? store, CoreProcessManager? processes})
      : store = store ?? Get.find<ConnectionStore>(),
        processes = processes ?? Get.find<CoreProcessManager>();

  final ConnectionStore store;
  final CoreProcessManager processes;

  Rx<CoreStatus> get status => store.status;
  Rxn<HealthInfo> get health => store.health;
  RxnString get lastError => store.lastError;
  RxList<CoreEvent> get events => store.events;
  bool get apiVersionMismatch => store.apiVersionMismatch;

  Future<void> refresh() => store.refresh();
  Future<void> startCore() => processes.start();
  Future<void> stopCore() => processes.stop();
}
