import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../platform/app_paths.dart';
import '../../platform/data_dir_mover.dart';
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
        _ProxyCard(controller: controller),
        const SizedBox(height: 16),
        _DataDirCard(controller: controller),
        const SizedBox(height: 16),
        Card(
          child: ListTile(
            leading: const Icon(Icons.key_outlined),
            title: const Text('密钥'),
            subtitle: const Text('AppSecret 和模型的 API Key 在「配置」页填写和替换。密钥只存系统密钥库，不写数据库、日志和导出文件。'),
          ),
        ),
      ],
    );
  }
}

/// 网络代理：核心服务访问模型接口和 QQ 时用的代理。
class _ProxyCard extends StatelessWidget {
  const _ProxyCard({required this.controller});

  final SettingsController controller;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Obx(() {
          final enabled = controller.coreRunning && controller.proxyLoaded.value;
          final mode = controller.proxyMode.value;
          final busy = controller.proxySaving.value || controller.proxyTesting.value;
          final notice = controller.proxyNotice.value;
          final test = controller.proxyTest.value;
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('网络代理', style: theme.textTheme.titleMedium),
              const SizedBox(height: 4),
              Text(
                '核心服务访问模型接口和 QQ 时用的代理。Claude 在国内一般要走代理；DeepSeek 和 QQ 通常直连就行。',
                style: theme.textTheme.bodySmall,
              ),
              if (!controller.coreRunning)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text('核心服务未运行，启动后才能修改代理。', style: TextStyle(color: theme.colorScheme.error)),
                ),
              const SizedBox(height: 16),
              SegmentedButton<String>(
                segments: const [
                  ButtonSegment(value: 'none', label: Text('直连')),
                  ButtonSegment(value: 'system', label: Text('跟随系统代理')),
                  ButtonSegment(value: 'custom', label: Text('自定义')),
                ],
                selected: {mode},
                showSelectedIcon: false,
                onSelectionChanged: enabled ? (value) => controller.proxyMode.value = value.single : null,
              ),
              const SizedBox(height: 8),
              Text(
                switch (mode) {
                  'none' => '不走代理，也不读系统代理和 HTTP_PROXY 等环境变量。',
                  'system' => '读系统的代理设置和 HTTP_PROXY / HTTPS_PROXY 环境变量；系统没设代理时直连。',
                  _ => '用下面填写的代理地址。',
                },
                style: theme.textTheme.bodySmall,
              ),
              if (mode == 'custom') ...[
                const SizedBox(height: 12),
                TextField(
                  controller: controller.proxyUrl,
                  enabled: enabled,
                  decoration: const InputDecoration(
                    labelText: '代理地址',
                    hintText: 'http://127.0.0.1:7890 或 socks5://127.0.0.1:7891',
                    helperText: '支持 http、https、socks5、socks5h（由代理解析域名）；暂不支持带用户名和密码的代理',
                    border: OutlineInputBorder(),
                  ),
                ),
              ],
              const SizedBox(height: 8),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                controlAffinity: ListTileControlAffinity.leading,
                title: const Text('模型接口走代理'),
                subtitle: const Text('Claude、DeepSeek'),
                value: controller.proxyForProviders.value,
                onChanged: enabled && mode != 'none' ? (v) => controller.proxyForProviders.value = v ?? false : null,
              ),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                controlAffinity: ListTileControlAffinity.leading,
                title: const Text('QQ 走代理'),
                subtitle: const Text('连接 QQ 网关、发消息、下载用户发的图片。QQ 在国内，一般不用勾'),
                value: controller.proxyForQq.value,
                onChanged: enabled && mode != 'none' ? (v) => controller.proxyForQq.value = v ?? false : null,
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  FilledButton(
                    onPressed: enabled && !busy ? controller.saveProxy : null,
                    child: Text(controller.proxySaving.value ? '保存中…' : '保存'),
                  ),
                  const SizedBox(width: 12),
                  OutlinedButton(
                    onPressed: enabled && !busy ? controller.testProxy : null,
                    child: Text(controller.proxyTesting.value ? '测试中…' : '测试连接'),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text('测试用界面上的设置，不用先保存', style: theme.textTheme.bodySmall),
                  ),
                ],
              ),
              if (notice != null)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Text(notice.$1, style: TextStyle(color: notice.$2 ? theme.colorScheme.error : null)),
                ),
              if (test != null) ...[
                const SizedBox(height: 12),
                if (mode == 'system')
                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: Text(
                      test.systemProxy == null ? '系统没有设置代理，会直连。' : '读到的系统代理：${test.systemProxy}',
                      style: theme.textTheme.bodySmall,
                    ),
                  ),
                for (final r in test.results)
                  ListTile(
                    dense: true,
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(
                      r.ok ? Icons.check_circle_outline : Icons.error_outline,
                      color: r.ok ? Colors.green : theme.colorScheme.error,
                    ),
                    title: Text('${r.target}  ${r.url}'),
                    subtitle: Text(r.ok ? '连得通（HTTP ${r.status}，${r.elapsedMs} ms）' : '连不通：${r.error}'),
                  ),
              ],
            ],
          );
        }),
      ),
    );
  }
}

/// 数据目录：数据库、日志、备份放在哪。换目录要重新启动程序。
class _DataDirCard extends StatelessWidget {
  const _DataDirCard({required this.controller});

  final SettingsController controller;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final paths = controller.paths;
    final changeable = paths.source == DataDirSource.custom || paths.source == DataDirSource.standard;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Obx(() {
          final busy = controller.dataDirBusy.value;
          final notice = controller.dataDirNotice.value;
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('数据目录', style: theme.textTheme.titleMedium),
              const SizedBox(height: 4),
              Text('数据库、日志和备份都放在这里。', style: theme.textTheme.bodySmall),
              const SizedBox(height: 12),
              SelectableText(paths.dataDir),
              const SizedBox(height: 4),
              Text(
                switch (paths.source) {
                  DataDirSource.environment => '由环境变量 ${AppPaths.envVar} 指定，不能在这里修改。',
                  DataDirSource.portable => '便携模式：程序旁边有 ${AppPaths.portableDirName} 文件夹。升级时如果删掉整个程序文件夹，数据会一起删掉。'
                      '要换位置，先退出程序，把这个文件夹移到程序目录以外，再到这里选它。',
                  DataDirSource.custom => '自定义目录。升级时删掉程序文件夹不影响这里的数据。',
                  DataDirSource.standard => '系统默认目录。升级时删掉程序文件夹不影响这里的数据。',
                },
                style: theme.textTheme.bodySmall,
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  OutlinedButton(onPressed: controller.openDataDir, child: const Text('打开文件夹')),
                  if (changeable) ...[
                    const SizedBox(width: 12),
                    OutlinedButton(
                      onPressed: busy ? null : () => _change(context),
                      child: Text(busy ? '正在更换…' : '更换…'),
                    ),
                  ],
                  if (paths.source == DataDirSource.custom) ...[
                    const SizedBox(width: 12),
                    TextButton(
                      onPressed: busy ? null : () => _change(context, target: AppPaths.standardDataDir),
                      child: const Text('改回默认目录'),
                    ),
                  ],
                ],
              ),
              if (notice != null)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Text(notice.$1, style: TextStyle(color: notice.$2 ? theme.colorScheme.error : null)),
                ),
            ],
          );
        }),
      ),
    );
  }

  Future<void> _change(BuildContext context, {String? target}) async {
    final dir = target == null ? await controller.pickDataDir() : (controller.checkDataDir(target) ? target : null);
    if (dir == null || !context.mounted) return;
    final action = await showDialog<DataDirAction>(context: context, builder: (_) => _DataDirDialog(target: dir));
    if (action != null) await controller.changeDataDir(dir, action);
  }
}

/// 确认怎么处理新目录：没有数据就复制过去；已有数据时用它，或者用当前数据替换。
class _DataDirDialog extends StatelessWidget {
  const _DataDirDialog({required this.target});

  final String target;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final hasData = DataDirMover.hasData(target);
    return AlertDialog(
      title: const Text('更换数据目录'),
      content: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 480),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SelectableText(target),
            const SizedBox(height: 12),
            Text(hasData
                ? '这个文件夹里已经有数据（可能是以前用过的）。可以直接用它，也可以用当前的数据替换，'
                    '替换时那里原有的数据库挪到它的 backups 文件夹里留底。'
                : '会先停止核心服务，把当前的数据复制过去。'),
            const SizedBox(height: 8),
            const Text('完成后程序会重新启动。原目录的数据保留不动，确认没问题后可以手动删除。'),
            if (DataDirMover.insideProgramDir(target)) ...[
              const SizedBox(height: 8),
              Text(
                '这个文件夹在程序目录里，升级时如果删掉整个程序文件夹，数据会一起删掉。建议选程序目录以外的位置。',
                style: TextStyle(color: theme.colorScheme.error),
              ),
            ],
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('取消')),
        if (hasData) ...[
          TextButton(
            onPressed: () => Navigator.pop(context, DataDirAction.replace),
            child: const Text('用当前数据替换'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, DataDirAction.useExisting),
            child: const Text('使用这里的数据'),
          ),
        ] else
          FilledButton(
            onPressed: () => Navigator.pop(context, DataDirAction.copy),
            child: const Text('复制并重启'),
          ),
      ],
    );
  }
}
