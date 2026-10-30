import 'package:get/get.dart';

import '../../core/api/core_api_client.dart';
import '../../core/api/models.dart';
import '../../core/connection/connection_store.dart';
import '../../platform/autostart_service.dart';
import '../../platform/desktop_prefs.dart';
import '../../platform/tray_service.dart';

/// 设置页的数据来源：开机自启、关闭窗口时的行为、托盘可用性，以及核心服务的网络代理（走本机控制接口）。
class SettingsRepository {
  SettingsRepository({AutostartService? autostart, DesktopPrefs? prefs, TrayService? tray, ConnectionStore? store})
      : autostart = autostart ?? Get.find<AutostartService>(),
        prefs = prefs ?? Get.find<DesktopPrefs>(),
        tray = tray ?? Get.find<TrayService>(),
        store = store ?? Get.find<ConnectionStore>();

  final AutostartService autostart;
  final DesktopPrefs prefs;
  final TrayService tray;
  final ConnectionStore store;

  RxBool get autostartEnabled => autostart.enabled;
  bool get trayAvailable => tray.available;

  Future<void> setAutostart(bool value) => autostart.setEnabled(value);

  Rx<CloseAction> get closeAction => prefs.closeAction;

  Future<void> setCloseAction(CloseAction value) => prefs.setCloseAction(value);

  // ---- 网络代理 ----

  CoreApiClient get _api {
    final api = store.api;
    if (api == null || store.status.value != CoreStatus.running) {
      throw CoreApiException('核心服务未运行');
    }
    return api;
  }

  Rx<CoreStatus> get coreStatus => store.status;

  Future<Map<String, dynamic>> globalSettings() async {
    final s = await _api.getSettings();
    return {...s.defaults, ...s.values};
  }

  Future<void> putGlobalSetting(String key, Object? value) => _api.putSetting(key, value);

  Future<({List<ProxyTestResult> results, String? systemProxy})> testProxy({required String mode, String url = ''}) =>
      _api.testProxy(mode: mode, url: url);
}
