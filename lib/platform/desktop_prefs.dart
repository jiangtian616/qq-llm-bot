import 'dart:convert';
import 'dart:io';

import 'package:get/get.dart';

/// 点窗口关闭按钮时做什么。
enum CloseAction {
  /// 缩到托盘（没有托盘时最小化），核心服务继续运行。
  hide,

  /// 退出桌面端，核心服务一起停止。
  quit,
}

/// 只跟桌面端有关的偏好，存在数据目录的 desktop.json 里；核心服务不读它。
class DesktopPrefs extends GetxService {
  DesktopPrefs(this.file);

  final String file;

  final closeAction = CloseAction.hide.obs;

  DesktopPrefs load() {
    try {
      final json = jsonDecode(File(file).readAsStringSync()) as Map<String, dynamic>;
      closeAction.value = CloseAction.values.asNameMap()[json['close_action']] ?? CloseAction.hide;
    } on Object {
      // 文件不存在或内容不对，用默认值。
    }
    return this;
  }

  Future<void> setCloseAction(CloseAction value) async {
    await File(file).writeAsString(jsonEncode({'close_action': value.name}));
    closeAction.value = value;
  }
}
