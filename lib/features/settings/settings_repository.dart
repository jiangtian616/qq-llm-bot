import 'package:get/get.dart';

import '../../platform/autostart_service.dart';
import '../../platform/desktop_prefs.dart';
import '../../platform/tray_service.dart';

/// 设置页的数据来源：开机自启、关闭窗口时的行为、托盘可用性。
class SettingsRepository {
  SettingsRepository({AutostartService? autostart, DesktopPrefs? prefs, TrayService? tray})
      : autostart = autostart ?? Get.find<AutostartService>(),
        prefs = prefs ?? Get.find<DesktopPrefs>(),
        tray = tray ?? Get.find<TrayService>();

  final AutostartService autostart;
  final DesktopPrefs prefs;
  final TrayService tray;

  RxBool get autostartEnabled => autostart.enabled;
  bool get trayAvailable => tray.available;

  Future<void> setAutostart(bool value) => autostart.setEnabled(value);

  Rx<CloseAction> get closeAction => prefs.closeAction;

  Future<void> setCloseAction(CloseAction value) => prefs.setCloseAction(value);
}
