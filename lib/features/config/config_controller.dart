import 'package:get/get.dart';

import '../../core/api/models.dart';
import '../../core/connection/connection_store.dart';
import 'config_repository.dart';

class ConfigController extends GetxController {
  ConfigController(this.repository);

  final ConfigRepository repository;

  final accounts = <AccountInfo>[].obs;
  final profiles = <ProfileInfo>[].obs;
  final settings = <String, dynamic>{}.obs;
  final loading = false.obs;
  final error = RxnString();

  bool get coreRunning => repository.coreStatus.value == CoreStatus.running;

  @override
  void onInit() {
    super.onInit();
    // 核心服务启动后、账号或配置在别处被改动后，重新拉取。
    ever(repository.coreStatus, (status) {
      if (status == CoreStatus.running) load();
    });
    ever(repository.events, (events) {
      if (events.isNotEmpty && const {'accounts.changed', 'profiles.changed', 'connection.state'}.contains(events.first.topic)) {
        load(quiet: true);
      }
    });
    if (coreRunning) load();
  }

  Future<void> load({bool quiet = false}) async {
    if (!quiet) loading.value = true;
    try {
      final results = await Future.wait([repository.accounts(), repository.profiles(), repository.globalSettings()]);
      accounts.assignAll(results[0] as List<AccountInfo>);
      profiles.assignAll(results[1] as List<ProfileInfo>);
      settings.assignAll(results[2] as Map<String, dynamic>);
      error.value = null;
    } on Object catch (e) {
      error.value = e.toString();
    } finally {
      loading.value = false;
    }
  }

  /// 执行一个修改，成功后刷新；失败时返回错误说明给对话框显示。
  Future<String?> run(Future<void> Function() action) async {
    try {
      await action();
      await load(quiet: true);
      return null;
    } on Object catch (e) {
      return e.toString();
    }
  }

  Future<String?> addAccount({
    required String appId,
    required String appSecret,
    required String displayName,
    required bool isSandbox,
    required bool autoConnect,
  }) =>
      run(() => repository.addAccount(
            appId: appId,
            appSecret: appSecret,
            displayName: displayName,
            isSandbox: isSandbox,
            autoConnect: autoConnect,
          ));

  Future<String?> updateAccount(int id, Map<String, dynamic> fields) => run(() => repository.updateAccount(id, fields));

  Future<String?> deleteAccount(int id) => run(() => repository.deleteAccount(id));

  Future<String?> updateProfile(int id, Map<String, dynamic> fields) => run(() => repository.updateProfile(id, fields));

  Future<String?> setSetting(String key, Object? value) => run(() => repository.putGlobalSetting(key, value));
}
