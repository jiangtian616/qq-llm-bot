import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../core/ui/placeholder_view.dart';
import 'logs_controller.dart';

class LogsPage extends GetView<LogsController> {
  const LogsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return const PlaceholderView(
      title: '日志',
      icon: Icons.receipt_long_outlined,
      description: '运行事件（连接变化、Resume 失败、清理记录）和核心服务日志。下一阶段实现。',
    );
  }
}
