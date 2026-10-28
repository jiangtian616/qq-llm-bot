import 'package:get/get.dart';

import '../../core/api/core_api_client.dart';
import '../../core/api/models.dart';
import '../../core/connection/connection_store.dart';

/// 配置页的数据来源：机器人账号、模型配置和全局配置，都走本机控制接口。
class ConfigRepository {
  ConfigRepository({ConnectionStore? store}) : store = store ?? Get.find<ConnectionStore>();

  final ConnectionStore store;

  CoreApiClient get _api {
    final api = store.api;
    if (api == null || store.status.value != CoreStatus.running) {
      throw CoreApiException('核心服务未运行');
    }
    return api;
  }

  Rx<CoreStatus> get coreStatus => store.status;
  RxList<CoreEvent> get events => store.events;

  Future<List<AccountInfo>> accounts() => _api.listAccounts();

  Future<void> addAccount({
    required String appId,
    required String appSecret,
    required String displayName,
    required bool isSandbox,
    required bool autoConnect,
  }) =>
      _api.createAccount(
        appId: appId,
        appSecret: appSecret,
        displayName: displayName,
        isSandbox: isSandbox,
        autoConnect: autoConnect,
      );

  Future<void> updateAccount(int id, Map<String, dynamic> fields) => _api.updateAccount(id, fields);

  Future<void> deleteAccount(int id) => _api.deleteAccount(id);

  Future<List<ProfileInfo>> profiles() => _api.listProfiles();

  Future<void> updateProfile(int id, Map<String, dynamic> fields) => _api.updateProfile(id, fields);

  Future<Map<String, dynamic>> globalSettings() async {
    final s = await _api.getSettings();
    return {...s.defaults, ...s.values};
  }

  Future<void> putGlobalSetting(String key, Object? value) => _api.putSetting(key, value);
}
