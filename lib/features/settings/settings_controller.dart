import 'package:get/get.dart';

import '../../platform/desktop_prefs.dart';
import 'settings_repository.dart';

class SettingsController extends GetxController {
  SettingsController(this.repository);

  final SettingsRepository repository;

  final error = RxnString();

  RxBool get autostartEnabled => repository.autostartEnabled;
  Rx<CloseAction> get closeAction => repository.closeAction;
  bool get trayAvailable => repository.trayAvailable;

  Future<void> setAutostart(bool value) async {
    error.value = null;
    try {
      await repository.setAutostart(value);
    } on Object catch (e) {
      error.value = '设置开机自启失败：$e';
    }
  }

  Future<void> setCloseAction(CloseAction value) async {
    error.value = null;
    try {
      await repository.setCloseAction(value);
    } on Object catch (e) {
      error.value = '保存关闭窗口时的设置失败：$e';
    }
  }
}
