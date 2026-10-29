import 'package:get/get.dart';

import '../platform/core_process_manager.dart';
import '../platform/single_instance.dart';
import '../platform/window_service.dart';

bool _quitting = false;

/// 退出桌面端：先藏起窗口，再平稳停止核心服务，最后关掉进程。
///
/// 托盘菜单「退出」和设置成「退出程序」的关闭按钮都走这里。万一桌面端没走到这一步就没了（被强制结束、
/// 停止调试），核心服务也会自己发现桌面端退出并停止（见 agent-core 的 --parent-pid）。
Future<void> quitApp(SingleInstance instance) async {
  if (_quitting) return;
  _quitting = true;
  final window = Get.find<WindowService>();
  await window.hide();
  try {
    await Get.find<CoreProcessManager>().stop();
  } on Object {
    // 停不下来也照样退出，核心服务会跟着桌面端退出。
  }
  await instance.release();
  await window.quit();
}
