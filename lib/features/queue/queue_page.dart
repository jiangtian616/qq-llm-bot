import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../core/ui/placeholder_view.dart';
import 'queue_controller.dart';

class QueuePage extends GetView<QueueController> {
  const QueuePage({super.key});

  @override
  Widget build(BuildContext context) {
    return const PlaceholderView(
      title: '消息队列',
      icon: Icons.inbox_outlined,
      description: '收件箱和发件箱：积压、失败、过期的消息，以及手动重试。下一阶段实现。',
    );
  }
}
