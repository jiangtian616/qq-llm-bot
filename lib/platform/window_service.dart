import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:window_manager/window_manager.dart';

/// 窗口：关闭按钮默认最小化到托盘，核心服务继续运行；没有托盘时改为最小化。
class WindowService extends GetxService with WindowListener {
  WindowService({required this.trayAvailable});

  final bool Function() trayAvailable;

  Future<WindowService> init({required bool startHidden}) async {
    await windowManager.ensureInitialized();
    const options = WindowOptions(
      size: Size(1100, 720),
      minimumSize: Size(800, 560),
      center: true,
      title: 'qq-llm-bot',
    );
    await windowManager.waitUntilReadyToShow(options, () async {
      if (!startHidden) {
        await windowManager.show();
        await windowManager.focus();
      }
    });
    await windowManager.setPreventClose(true);
    windowManager.addListener(this);
    return this;
  }

  Future<void> showAndFocus() async {
    if (await windowManager.isMinimized()) await windowManager.restore();
    await windowManager.show();
    await windowManager.focus();
  }

  @override
  void onWindowClose() {
    if (trayAvailable()) {
      windowManager.hide();
    } else {
      windowManager.minimize();
    }
  }

  /// 真正退出桌面端（核心服务是否一起停止由调用方决定）。
  Future<void> quit() async {
    windowManager.removeListener(this);
    await windowManager.setPreventClose(false);
    await windowManager.destroy();
  }
}
