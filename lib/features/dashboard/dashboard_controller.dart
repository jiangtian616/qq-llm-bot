import 'package:get/get.dart';

import '../../core/api/models.dart';
import '../../core/connection/connection_store.dart';
import 'dashboard_repository.dart';

class DashboardController extends GetxController {
  DashboardController(this._repo);

  final DashboardRepository _repo;

  final busy = false.obs;
  final actionError = RxnString();
  final accounts = <AccountInfo>[].obs;
  final accountBusy = <int>{}.obs;

  @override
  void onInit() {
    super.onInit();
    ever(_repo.status, (status) {
      if (status == CoreStatus.running) {
        loadAccounts();
      } else {
        accounts.clear();
      }
    });
    ever(_repo.events, (events) {
      if (events.isNotEmpty && const {'connection.state', 'accounts.changed'}.contains(events.first.topic)) {
        loadAccounts();
      }
    });
    if (_repo.status.value == CoreStatus.running) loadAccounts();
  }

  Future<void> loadAccounts() async {
    try {
      accounts.assignAll(await _repo.accounts());
    } on Object catch (e) {
      actionError.value = e.toString();
    }
  }

  Future<void> toggleConnection(AccountInfo account) async {
    accountBusy.add(account.id);
    actionError.value = null;
    try {
      if (account.connection.isActive) {
        await _repo.stopConnection(account.id);
      } else {
        await _repo.startConnection(account.id);
      }
      await loadAccounts();
    } on Object catch (e) {
      actionError.value = e.toString();
    } finally {
      accountBusy.remove(account.id);
    }
  }

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
