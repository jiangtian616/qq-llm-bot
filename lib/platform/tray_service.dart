import 'dart:io';

import 'package:get/get.dart';
import 'package:tray_manager/tray_manager.dart';

/// 托盘图标和菜单：打开界面、启动 / 停止核心服务、退出。
///
/// Linux 上 GNOME 默认不显示托盘（要装 AppIndicator 扩展）。检测不到托盘时 [available] 为 false，
/// 关闭窗口改为最小化。
class TrayService extends GetxService with TrayListener {
  TrayService({
    required this.onShowWindow,
    required this.onStartCore,
    required this.onStopCore,
    required this.onQuit,
  });

  final void Function() onShowWindow;
  final Future<void> Function() onStartCore;
  final Future<void> Function() onStopCore;
  final Future<void> Function() onQuit;

  bool available = false;

  Future<TrayService> init() async {
    if (Platform.isLinux && !await _linuxHasStatusNotifier()) {
      return this;
    }
    try {
      await trayManager.setIcon(Platform.isWindows ? 'assets/tray/tray_icon.ico' : 'assets/tray/tray_icon.png');
      if (!Platform.isLinux) await trayManager.setToolTip('qq-llm-bot');
      await trayManager.setContextMenu(Menu(items: [
        MenuItem(key: 'show', label: '打开界面'),
        MenuItem.separator(),
        MenuItem(key: 'start', label: '启动核心服务'),
        MenuItem(key: 'stop', label: '停止核心服务'),
        MenuItem.separator(),
        MenuItem(key: 'quit', label: '退出'),
      ]));
      trayManager.addListener(this);
      available = true;
    } on Object {
      available = false;
    }
    return this;
  }

  /// AppIndicator 依赖 D-Bus 上的 StatusNotifierWatcher；没有它，托盘图标不会显示。
  static Future<bool> _linuxHasStatusNotifier() async {
    try {
      final result = await Process.run('gdbus', [
        'call', '--session',
        '--dest', 'org.freedesktop.DBus',
        '--object-path', '/org/freedesktop/DBus',
        '--method', 'org.freedesktop.DBus.NameHasOwner',
        'org.kde.StatusNotifierWatcher',
      ]);
      return result.exitCode == 0 && result.stdout.toString().contains('true');
    } on Object {
      return false;
    }
  }

  @override
  void onTrayIconMouseDown() => onShowWindow();

  @override
  void onTrayIconRightMouseDown() => trayManager.popUpContextMenu();

  @override
  void onTrayMenuItemClick(MenuItem menuItem) {
    switch (menuItem.key) {
      case 'show':
        onShowWindow();
      case 'start':
        onStartCore();
      case 'stop':
        onStopCore();
      case 'quit':
        onQuit();
    }
  }

  @override
  void onClose() {
    if (available) {
      trayManager.removeListener(this);
      trayManager.destroy();
    }
    super.onClose();
  }
}
