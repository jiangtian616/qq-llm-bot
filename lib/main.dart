import 'dart:io';

import 'package:flutter/material.dart';
import 'package:get/get.dart';

import 'app/app.dart';
import 'app/quit_flow.dart';
import 'app/startup_error_app.dart';
import 'core/connection/connection_store.dart';
import 'platform/app_paths.dart';
import 'platform/autostart_service.dart';
import 'platform/core_process_manager.dart';
import 'platform/desktop_prefs.dart';
import 'platform/single_instance.dart';
import 'platform/tray_service.dart';
import 'platform/window_service.dart';

Future<void> main(List<String> args) async {
  WidgetsFlutterBinding.ensureInitialized();
  final AppPaths paths;
  try {
    paths = AppPaths.resolve();
  } on DataDirException catch (e) {
    runApp(StartupErrorApp(message: e.message));
    return;
  }

  WindowService? window;
  final instance = await SingleInstance.acquire(
    lockFile: paths.desktopLockFile,
    portFile: paths.desktopPortFile,
    onShowRequested: () => window?.showAndFocus(),
  );
  if (instance == null) {
    // 已有实例在运行，已经通知它把窗口唤到前台。
    exit(0);
  }
  Get.put(paths, permanent: true);
  Get.put(instance, permanent: true);

  final prefs = Get.put(DesktopPrefs(paths.desktopPrefsFile).load(), permanent: true);
  final store = Get.put(ConnectionStore(controlFile: paths.controlFile), permanent: true);
  final processes = Get.put(CoreProcessManager(dataDir: paths.dataDir, store: store), permanent: true);

  final tray = await Get.put(
    TrayService(
      onShowWindow: () => window?.showAndFocus(),
      onStartCore: processes.start,
      onStopCore: processes.stop,
      onQuit: () => quitApp(instance),
    ),
    permanent: true,
  ).init();
  await Get.put(AutostartService(), permanent: true).init();

  // 开机自启时最小化到托盘；没有托盘时照常显示窗口，免得用户找不到。
  final startHidden = args.contains(AutostartService.minimizedArg) && tray.available;
  window = await Get.put(
    WindowService(
      trayAvailable: () => tray.available,
      closeAction: () => prefs.closeAction.value,
      onQuitRequested: () => quitApp(instance),
    ),
    permanent: true,
  ).init(startHidden: startHidden);

  // 打开桌面端时，如果核心服务没在运行就拉起它。
  processes.start().catchError((Object e) => store.lastError.value = e.toString());

  runApp(const QQLlmBotApp());
}
