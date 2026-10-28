import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../core/ui/placeholder_view.dart';
import 'config_controller.dart';

class ConfigPage extends GetView<ConfigController> {
  const ConfigPage({super.key});

  @override
  Widget build(BuildContext context) {
    return const PlaceholderView(
      title: '配置',
      icon: Icons.tune,
      description: '机器人账号、模型配置，以及全局 / 账号 / 会话三层的机器人行为配置。下一阶段实现。',
    );
  }
}
