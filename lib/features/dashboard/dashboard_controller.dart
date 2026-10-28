import 'package:get/get.dart';

import '../../core/api/models.dart';
import '../../core/connection/connection_store.dart';
import 'dashboard_repository.dart';

class DashboardController extends GetxController {
  DashboardController(this._repo);

  final DashboardRepository _repo;

  final busy = false.obs;
  final actionError = RxnString();

  Rx<CoreStatus> get status => _repo.status;
  Rxn<HealthInfo> get health => _repo.health;
  RxnString get lastError => _repo.lastError;
  RxList<CoreEvent> get events => _repo.events;
  bool get apiVersionMismatch => _repo.apiVersionMismatch;

  Future<void> refreshHealth() => _repo.refresh();

  Future<void> startCore() => _run(_repo.startCore);

  Future<void> stopCore() => _run(_repo.stopCore);

  Future<void> _run(Future<void> Function() action) async {
    if (busy.value) return;
    busy.value = true;
    actionError.value = null;
    try {
      await action();
    } on Object catch (e) {
      actionError.value = e.toString();
    } finally {
      busy.value = false;
    }
  }
}
