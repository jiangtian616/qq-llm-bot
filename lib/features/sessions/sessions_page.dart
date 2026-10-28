import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../core/ui/placeholder_view.dart';
import 'sessions_controller.dart';

class SessionsPage extends GetView<SessionsController> {
  const SessionsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return const PlaceholderView(
      title: '会话',
      icon: Icons.chat_bubble_outline,
      description: '群聊和单聊会话列表、历史记录，以及群里的消息接收设置（只读）。下一阶段实现。',
    );
  }
}
