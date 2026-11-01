import 'dart:io';

import 'package:get/get.dart';

import '../platform/core_process_manager.dart';
import '../platform/single_instance.dart';
import '../platform/tray_service.dart';
import '../platform/window_service.dart';

bool _quitting = false;

/// 退出桌面端：先藏起窗口、移除托盘图标，再平稳停止核心服务，最后关掉进程。
///
/// 托盘菜单「退出」和设置成「退出程序」的关闭按钮都走这里。万一桌面端没走到这一步就没了（被强制结束、
/// 停止调试），核心服务也会自己发现桌面端退出并停止（见 agent-core 的 --parent-pid）。
Future<void> quitApp(SingleInstance instance) async {
  if (_quitting) return;
  _quitting = true;
  final window = Get.find<WindowService>();
  await window.hide();
  // 托盘图标跟窗口一起去掉，不用等核心服务停完。
  try {
    await Get.find<TrayService>().remove();
  } on Object {
    // 图标移不掉也照样退出。
  }
  try {
    await Get.find<CoreProcessManager>().stop();
  } on Object {
    // 停不下来也照样退出，核心服务会跟着桌面端退出。
  }
  await instance.release();
  await window.quit();
}

/// 重新启动桌面端：先拉起新实例再退出当前的。换了数据目录后用，新实例的锁文件在新目录里，不会和当前实例冲突。
Future<void> restartApp(SingleInstance instance) async {
  // AppImage 运行时可执行文件在临时挂载的目录里，当前实例退出后就没了，要从 AppImage 文件本身启动。
  final appImage = Platform.environment['APPIMAGE'];
  final executable = Platform.isLinux && appImage != null && appImage.isNotEmpty ? appImage : Platform.resolvedExecutable;
  await Process.start(executable, const [], mode: ProcessStartMode.detached);
  await quitApp(instance);
}
