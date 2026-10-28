import 'dart:io';

import 'package:get/get.dart';
import 'package:launch_at_startup/launch_at_startup.dart';

/// 开机自启：只用当前用户级别的方式（Windows 启动项、macOS 登录项、Linux ~/.config/autostart），
/// 不需要管理员权限。自启时带 --minimized，启动后直接最小化到托盘。
class AutostartService extends GetxService {
  static const minimizedArg = '--minimized';

  final enabled = false.obs;

  Future<AutostartService> init() async {
    launchAtStartup.setup(
      appName: 'qq-llm-bot',
      appPath: Platform.resolvedExecutable,
      args: const [minimizedArg],
    );
    enabled.value = await launchAtStartup.isEnabled();
    return this;
  }

  Future<void> setEnabled(bool value) async {
    if (value) {
      await launchAtStartup.enable();
    } else {
      await launchAtStartup.disable();
    }
    enabled.value = await launchAtStartup.isEnabled();
  }
}
