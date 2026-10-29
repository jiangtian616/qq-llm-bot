import 'package:get/get.dart';

import '../../platform/autostart_service.dart';
import '../../platform/tray_service.dart';

/// 设置页的数据来源：开机自启、托盘可用性。
class SettingsRepository {
  SettingsRepository({AutostartService? autostart, TrayService? tray})
      : autostart = autostart ?? Get.find<AutostartService>(),
        tray = tray ?? Get.find<TrayService>();

  final AutostartService autostart;
  final TrayService tray;

  RxBool get autostartEnabled => autostart.enabled;
  bool get trayAvailable => tray.available;

  Future<void> setAutostart(bool value) => autostart.setEnabled(value);
}
