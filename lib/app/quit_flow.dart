import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../core/connection/connection_store.dart';
import '../platform/core_process_manager.dart';
import '../platform/single_instance.dart';
import '../platform/window_service.dart';

enum _QuitChoice { uiOnly, stopCore }

/// 托盘菜单「退出」：询问是否同时停止核心服务。
Future<void> quitApp(SingleInstance instance) async {
  final window = Get.find<WindowService>();
  final store = Get.find<ConnectionStore>();
  var choice = _QuitChoice.uiOnly;
  if (store.status.value == CoreStatus.running) {
    await window.showAndFocus();
    final picked = await Get.dialog<_QuitChoice>(
      AlertDialog(
        title: const Text('退出 qq-llm-bot'),
        content: const Text('核心服务仍在运行。关闭界面后，它会继续收发 QQ 消息。'),
        actions: [
          TextButton(onPressed: () => Get.back<_QuitChoice>(), child: const Text('取消')),
          TextButton(onPressed: () => Get.back(result: _QuitChoice.uiOnly), child: const Text('只退出界面')),
          FilledButton(onPressed: () => Get.back(result: _QuitChoice.stopCore), child: const Text('同时停止核心服务')),
        ],
      ),
    );
    if (picked == null) return;
    choice = picked;
  }
  if (choice == _QuitChoice.stopCore) {
    await Get.find<CoreProcessManager>().stop();
  }
  await instance.release();
  await window.quit();
}
