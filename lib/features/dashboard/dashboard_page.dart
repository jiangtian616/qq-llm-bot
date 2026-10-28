import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../core/api/core_api_client.dart';
import '../../core/api/models.dart';
import '../../core/connection/connection_store.dart';
import 'dashboard_controller.dart';

class DashboardPage extends GetView<DashboardController> {
  const DashboardPage({super.key});

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(24),
      children: [
        Text('仪表盘', style: Theme.of(context).textTheme.headlineSmall),
        const SizedBox(height: 16),
        const _CoreStatusCard(),
        const SizedBox(height: 16),
        const _QQConnectionCard(),
        const SizedBox(height: 16),
        const _EventsCard(),
      ],
    );
  }
}

class _CoreStatusCard extends GetView<DashboardController> {
  const _CoreStatusCard();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Obx(() {
          final status = controller.status.value;
          final health = controller.health.value;
          final busy = controller.busy.value;
          final running = status == CoreStatus.running;
          final transitioning = busy || status == CoreStatus.starting || status == CoreStatus.stopping;
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  _StatusDot(status: status),
                  const SizedBox(width: 12),
                  Text('核心服务：${_statusLabel(status)}', style: theme.textTheme.titleMedium),
                  const Spacer(),
                  IconButton(
                    tooltip: '刷新',
                    onPressed: transitioning ? null : controller.refreshHealth,
                    icon: const Icon(Icons.refresh),
                  ),
                  const SizedBox(width: 8),
                  if (running)
                    OutlinedButton.icon(
                      onPressed: transitioning ? null : controller.stopCore,
                      icon: const Icon(Icons.stop_circle_outlined),
                      label: const Text('停止核心服务'),
                    )
                  else
                    FilledButton.icon(
                      onPressed: transitioning ? null : controller.startCore,
                      icon: transitioning
                          ? const SizedBox.square(dimension: 16, child: CircularProgressIndicator(strokeWidth: 2))
                          : const Icon(Icons.play_arrow),
                      label: const Text('启动核心服务'),
                    ),
                ],
              ),
              if (controller.apiVersionMismatch) ...[
                const SizedBox(height: 12),
                _Banner(
                  color: theme.colorScheme.errorContainer,
                  text: '核心服务的接口版本（${health?.apiVersion}）和桌面端（$supportedApiVersion）不一致，请同时升级两端。',
                ),
              ],
              if ((controller.actionError.value ?? controller.lastError.value) case final error?
                  when !running && !transitioning) ...[
                const SizedBox(height: 12),
                _Banner(color: theme.colorScheme.errorContainer, text: error),
              ],
              const SizedBox(height: 12),
              if (health != null)
                _HealthTable(health: health)
              else
                Text(
                  status == CoreStatus.notRunning ? '服务未运行。点「启动核心服务」拉起它；关掉界面不会影响已经在运行的服务。' : '正在检查……',
                  style: theme.textTheme.bodyMedium,
                ),
            ],
          );
        }),
      ),
    );
  }

  static String _statusLabel(CoreStatus s) => switch (s) {
        CoreStatus.unknown => '检查中',
        CoreStatus.notRunning => '未运行',
        CoreStatus.starting => '启动中',
        CoreStatus.running => '运行中',
        CoreStatus.stopping => '停止中',
      };
}

class _StatusDot extends StatelessWidget {
  const _StatusDot({required this.status});

  final CoreStatus status;

  @override
  Widget build(BuildContext context) {
    final color = switch (status) {
      CoreStatus.running => Colors.green,
      CoreStatus.starting || CoreStatus.stopping => Colors.orange,
      CoreStatus.notRunning => Colors.red,
      CoreStatus.unknown => Colors.grey,
    };
    return Container(width: 12, height: 12, decoration: BoxDecoration(color: color, shape: BoxShape.circle));
  }
}

class _HealthTable extends StatelessWidget {
  const _HealthTable({required this.health});

  final HealthInfo health;

  @override
  Widget build(BuildContext context) {
    final rows = <(String, String)>[
      ('/health', health.status),
      ('版本', '${health.version}（接口版本 ${health.apiVersion}）'),
      ('进程号', '${health.pid}'),
      ('已运行', _uptime(health.uptimeSeconds)),
      ('数据目录', health.dataDir),
      ('数据库版本', health.dbRevision ?? '—'),
      ('密钥存储', health.secretBackend == 'keyring' ? '系统密钥库' : '权限 0600 的文件（没有可用的系统密钥库）'),
      for (final e in health.plugins.entries) ('插件 · ${e.key}', e.value),
    ];
    final labelStyle = Theme.of(context).textTheme.bodyMedium?.copyWith(color: Theme.of(context).colorScheme.outline);
    return Table(
      columnWidths: const {0: IntrinsicColumnWidth(), 1: FlexColumnWidth()},
      children: [
        for (final (label, value) in rows)
          TableRow(children: [
            Padding(padding: const EdgeInsets.fromLTRB(0, 4, 24, 4), child: Text(label, style: labelStyle)),
            Padding(padding: const EdgeInsets.symmetric(vertical: 4), child: SelectableText(value)),
          ]),
      ],
    );
  }

  static String _uptime(double seconds) {
    final d = Duration(seconds: seconds.round());
    if (d.inHours > 0) return '${d.inHours} 小时 ${d.inMinutes % 60} 分';
    if (d.inMinutes > 0) return '${d.inMinutes} 分 ${d.inSeconds % 60} 秒';
    return '${d.inSeconds} 秒';
  }
}

class _QQConnectionCard extends GetView<DashboardController> {
  const _QQConnectionCard();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Obx(() {
          final running = controller.status.value == CoreStatus.running;
          final accounts = controller.accounts;
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('QQ 连接', style: theme.textTheme.titleMedium),
              const SizedBox(height: 8),
              if (running && controller.actionError.value != null)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Text(controller.actionError.value!, style: TextStyle(color: theme.colorScheme.error)),
                ),
              if (!running)
                Text('核心服务运行后才能连接 QQ。', style: theme.textTheme.bodyMedium)
              else if (accounts.isEmpty)
                Row(
                  children: [
                    Expanded(child: Text('还没有添加机器人账号。', style: theme.textTheme.bodyMedium)),
                    // 只按路由名跳转，不引用配置页的代码。
                    FilledButton.tonal(onPressed: () => Get.offNamed<void>('/config'), child: const Text('去配置页添加')),
                  ],
                ),
              for (final a in accounts)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: _ConnectionDot(state: a.connection.state),
                  title: Text('${a.displayName}${a.isSandbox ? '（沙箱）' : ''}'),
                  subtitle: Text(_connectionText(a)),
                  trailing: controller.accountBusy.contains(a.id)
                      ? const SizedBox.square(dimension: 20, child: CircularProgressIndicator(strokeWidth: 2))
                      : a.connection.isActive
                          ? OutlinedButton(onPressed: () => controller.toggleConnection(a), child: const Text('停止连接'))
                          : FilledButton(
                              onPressed: a.secretSet && a.enabled ? () => controller.toggleConnection(a) : null,
                              child: const Text('启动连接'),
                            ),
                ),
            ],
          );
        }),
      ),
    );
  }

  static String _connectionText(AccountInfo a) {
    final c = a.connection;
    final state = switch (c.state) {
      'online' => '在线',
      'connecting' => '连接中',
      'reconnecting' => '断线重连中',
      'error' => '出错',
      _ => '未连接',
    };
    return [
      state,
      if (!a.secretSet) '没有 AppSecret',
      if (c.lastEventAt != null) '最近收到事件 ${_DashboardTime.format(c.lastEventAt!)}',
      if (c.state == 'error' && c.lastError != null) c.lastError!,
    ].join(' · ');
  }
}

class _ConnectionDot extends StatelessWidget {
  const _ConnectionDot({required this.state});

  final String state;

  @override
  Widget build(BuildContext context) {
    final color = switch (state) {
      'online' => Colors.green,
      'connecting' || 'reconnecting' => Colors.orange,
      'error' => Colors.red,
      _ => Colors.grey,
    };
    return Container(width: 12, height: 12, decoration: BoxDecoration(color: color, shape: BoxShape.circle));
  }
}

class _DashboardTime {
  static String format(DateTime t) =>
      '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}:${t.second.toString().padLeft(2, '0')}';
}

class _EventsCard extends GetView<DashboardController> {
  const _EventsCard();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('实时事件', style: theme.textTheme.titleMedium),
            const SizedBox(height: 8),
            Obx(() {
              final events = controller.events;
              if (events.isEmpty) return Text('暂无事件', style: theme.textTheme.bodyMedium);
              return Column(
                children: [
                  for (final e in events.take(20))
                    ListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      leading: Text(_time(e.ts), style: theme.textTheme.bodySmall),
                      title: Text(e.topic),
                      subtitle: e.data.isEmpty ? null : Text(e.data.toString()),
                    ),
                ],
              );
            }),
          ],
        ),
      ),
    );
  }

  static String _time(DateTime t) => _DashboardTime.format(t);
}

class _Banner extends StatelessWidget {
  const _Banner({required this.color, required this.text});

  final Color color;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(8)),
      child: SelectableText(text),
    );
  }
}
