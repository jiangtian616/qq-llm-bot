import 'dart:io';

import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:window_manager/window_manager.dart';

import 'desktop_prefs.dart';

/// 窗口：关闭按钮按设置缩到托盘（没有托盘时最小化），或者直接退出桌面端。
class WindowService extends GetxService with WindowListener {
  WindowService({required this.trayAvailable, required this.closeAction, required this.onQuitRequested});

  final bool Function() trayAvailable;
  final CloseAction Function() closeAction;
  final VoidCallback onQuitRequested;

  /// Windows 上隐藏系统标题栏，由界面自己画（见 AppTitleBar）；macOS / Linux 先保留系统标题栏。
  static bool get customTitleBar => Platform.isWindows;

  Future<WindowService> init({required bool startHidden}) async {
    await windowManager.ensureInitialized();
    final options = WindowOptions(
      size: Size(1100, 720),
      minimumSize: Size(800, 560),
      center: true,
      title: 'qq-llm-bot',
      titleBarStyle: customTitleBar ? TitleBarStyle.hidden : TitleBarStyle.normal,
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
    if (closeAction() == CloseAction.quit) {
      onQuitRequested();
    } else if (trayAvailable()) {
      windowManager.hide();
    } else {
      windowManager.minimize();
    }
  }

  Future<void> hide() => windowManager.hide();

  /// 真正退出桌面端。
  Future<void> quit() async {
    windowManager.removeListener(this);
    await windowManager.setPreventClose(false);
    await windowManager.destroy();
  }
}
