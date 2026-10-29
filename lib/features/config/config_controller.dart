import 'package:get/get.dart';

import '../../core/api/models.dart';
import '../../core/connection/connection_store.dart';
import 'config_repository.dart';

class ConfigController extends GetxController {
  ConfigController(this.repository);

  final ConfigRepository repository;

  final accounts = <AccountInfo>[].obs;
  final profiles = <ProfileInfo>[].obs;
  final providers = <String, ProviderSpec>{}.obs;
  final settings = <String, dynamic>{}.obs;
  final loading = false.obs;
  final error = RxnString();

  /// 「限频、预算与数据保留」卡片上的提示：(内容, 是否错误)。
  final limitsNotice = Rxn<(String, bool)>();

  /// 「上下文与历史」卡片上的提示：(内容, 是否错误)。
  final contextNotice = Rxn<(String, bool)>();

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
      final results = await Future.wait(
          [repository.accounts(), repository.profiles(), repository.globalSettings(), repository.providers()]);
      accounts.assignAll(results[0] as List<AccountInfo>);
      profiles.assignAll(results[1] as List<ProfileInfo>);
      settings.assignAll(results[2] as Map<String, dynamic>);
      providers.assignAll({for (final p in results[3] as List<ProviderSpec>) p.name: p});
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
    required bool autoConnect,
  }) =>
      run(() => repository.addAccount(
            appId: appId,
            appSecret: appSecret,
            displayName: displayName,
            autoConnect: autoConnect,
          ));

  Future<String?> updateAccount(int id, Map<String, dynamic> fields) => run(() => repository.updateAccount(id, fields));

  Future<String?> deleteAccount(int id) => run(() => repository.deleteAccount(id));

  Future<String?> updateProfile(int id, Map<String, dynamic> fields) => run(() => repository.updateProfile(id, fields));

  Future<String?> setSetting(String key, Object? value) => run(() => repository.putGlobalSetting(key, value));

  /// 一次保存多个配置项；全部成功才返回 null。
  Future<String?> setSettings(Map<String, Object?> values) => run(() async {
        for (final e in values.entries) {
          await repository.putGlobalSetting(e.key, e.value);
        }
      });

  Future<({int deleted, String? backup})> runMaintenance() => repository.runMaintenance();
}
