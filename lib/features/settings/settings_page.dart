import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../platform/desktop_prefs.dart';
import 'settings_controller.dart';

class SettingsPage extends GetView<SettingsController> {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ListView(
      padding: const EdgeInsets.all(24),
      children: [
        Text('设置', style: theme.textTheme.headlineSmall),
        const SizedBox(height: 16),
        Card(
          child: Column(
            children: [
              Obx(() => SwitchListTile(
                    title: const Text('开机自启'),
                    subtitle: const Text('登录系统时启动桌面端并最小化到托盘，由它拉起核心服务。不需要管理员权限。'),
                    value: controller.autostartEnabled.value,
                    onChanged: controller.setAutostart,
                  )),
              Obx(() => ListTile(
                    title: const Text('关闭窗口时'),
                    subtitle: Text(switch (controller.closeAction.value) {
                      CloseAction.hide when controller.trayAvailable => '缩到托盘，核心服务继续收发 QQ 消息',
                      CloseAction.hide => '检测不到系统托盘，改为最小化窗口；核心服务继续收发 QQ 消息',
                      CloseAction.quit => '退出桌面端，核心服务一起停止，不再收发 QQ 消息',
                    }),
                    trailing: SegmentedButton<CloseAction>(
                      segments: [
                        ButtonSegment(
                          value: CloseAction.hide,
                          label: Text(controller.trayAvailable ? '缩到托盘' : '最小化'),
                        ),
                        const ButtonSegment(value: CloseAction.quit, label: Text('退出程序')),
                      ],
                      selected: {controller.closeAction.value},
                      showSelectedIcon: false,
                      onSelectionChanged: (value) => controller.setCloseAction(value.single),
                    ),
                  )),
            ],
          ),
        ),
        Obx(() => controller.error.value == null
            ? const SizedBox.shrink()
            : Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(controller.error.value!, style: TextStyle(color: theme.colorScheme.error)),
              )),
        const SizedBox(height: 16),
        Card(
          child: ListTile(
            leading: const Icon(Icons.key_outlined),
            title: const Text('密钥'),
            subtitle: const Text('AppSecret 和 Claude API Key 在「配置」页填写和替换。密钥只存系统密钥库，不写数据库、日志和导出文件。'),
          ),
        ),
      ],
    );
  }
}
