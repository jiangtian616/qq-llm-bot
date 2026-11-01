import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../core/api/models.dart';
import '../../core/ui/detail_dialog.dart';
import '../../core/ui/format.dart';
import '../../core/ui/placeholder_view.dart';
import '../../core/ui/turn_trace_dialog.dart';
import 'logs_controller.dart';

const _levels = {'info': '信息', 'warning': '警告', 'error': '错误'};

const _categories = {
  'connection': '连接',
  'resume': '断线恢复',
  'send': '发送',
  'provider': '模型',
  'account': '账号',
  'cleanup': '清理',
  'budget': '预算',
  'rate_limit': '限频',
  'history': '历史',
};

class LogsPage extends GetView<LogsController> {
  const LogsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Obx(() {
      if (!controller.coreRunning) {
        return const PlaceholderView(
          title: '核心服务未运行',
          icon: Icons.power_off_outlined,
          description: '运行事件和日志由核心服务记录。请先在仪表盘启动核心服务。',
        );
      }
      return DefaultTabController(
        length: 3,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 24, 16, 0),
              child: Row(
                children: [
                  Text('日志', style: Theme.of(context).textTheme.headlineSmall),
                  const Spacer(),
                  IconButton(tooltip: '刷新', onPressed: controller.load, icon: const Icon(Icons.refresh)),
                ],
              ),
            ),
            if (controller.error.value != null)
              Padding(
                padding: const EdgeInsets.fromLTRB(24, 8, 24, 0),
                child: Text(controller.error.value!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
              ),
            const TabBar(tabs: [Tab(text: '问答'), Tab(text: '运行事件'), Tab(text: '服务日志')]),
            const Expanded(child: TabBarView(children: [_TurnsTab(), _EventsTab(), _LogTab()])),
          ],
        ),
      );
    });
  }
}

class _TurnsTab extends GetView<LogsController> {
  const _TurnsTab();

  @override
  Widget build(BuildContext context) {
    return Obx(() => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
              child: Wrap(
                spacing: 8,
                children: [
                  ChoiceChip(
                    label: const Text('全部'),
                    selected: controller.turnStatus.value == null,
                    onSelected: (_) => controller.turnStatus.value = null,
                  ),
                  for (final e in turnStatuses.entries)
                    ChoiceChip(
                      label: Text('${e.value} ${controller.turnCounts[e.key] ?? 0}'),
                      selected: controller.turnStatus.value == e.key,
                      onSelected: (_) => controller.turnStatus.value = e.key,
                    ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 4),
              child: Text('每次调用模型的完整过程：组装上下文、请求模型、联网搜索、工具调用、回复编排。点开查看每一步。',
                  style: Theme.of(context).textTheme.bodySmall),
            ),
            Expanded(
              child: controller.turns.isEmpty
                  ? const Center(child: Text('没有问答'))
                  : ListView(
                      children: [
                        for (final t in controller.turns)
                          ListTile(
                            dense: true,
                            leading: _TurnStatusChip(status: t.status),
                            title: Text(t.preview.isEmpty ? '（空消息）' : t.preview, maxLines: 1, overflow: TextOverflow.ellipsis),
                            subtitle: Text(_turnMeta(t), maxLines: 2, overflow: TextOverflow.ellipsis),
                            onTap: () => showTurnTraceDialog(context, t.id),
                          ),
                      ],
                    ),
            ),
          ],
        ));
  }

  static String _turnMeta(TurnItem t) => [
        formatTime(t.startedAt),
        t.conversationLabel,
        t.model,
        '输入 ${t.inputTokens + t.cacheReadTokens} / 输出 ${t.outputTokens} token',
        formatUsd(t.costUsd),
        if (t.searchRequests > 0) '联网搜索 ${t.searchRequests} 次',
        if (t.toolRounds > 0) '工具 ${t.toolRounds} 轮',
        if (t.latencyMs != null) formatMs(t.latencyMs!),
        if (t.lastError != null && t.status == 'failed') t.lastError!,
      ].join(' · ');
}

class _TurnStatusChip extends StatelessWidget {
  const _TurnStatusChip({required this.status});

  final String status;

  @override
  Widget build(BuildContext context) {
    final color = turnStatusColor(status);
    return Container(
      width: 56,
      padding: const EdgeInsets.symmetric(vertical: 4),
      decoration: BoxDecoration(color: color.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(6)),
      child: Text(turnStatuses[status] ?? status, textAlign: TextAlign.center, style: TextStyle(color: color, fontSize: 12)),
    );
  }
}

class _EventsTab extends GetView<LogsController> {
  const _EventsTab();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Obx(() => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
              child: Wrap(
                spacing: 8,
                children: [
                  ChoiceChip(
                    label: const Text('全部'),
                    selected: controller.level.value == null,
                    onSelected: (_) => controller.level.value = null,
                  ),
                  for (final e in _levels.entries)
                    ChoiceChip(
                      label: Text(e.value),
                      selected: controller.level.value == e.key,
                      onSelected: (_) => controller.level.value = e.key,
                    ),
                ],
              ),
            ),
            Expanded(
              child: controller.systemEvents.isEmpty
                  ? const Center(child: Text('没有事件'))
                  : ListView(
                      children: [
                        for (final e in controller.systemEvents)
                          ListTile(
                            dense: true,
                            leading: Icon(
                              switch (e.level) {
                                'error' => Icons.error_outline,
                                'warning' => Icons.warning_amber_outlined,
                                _ => Icons.info_outline,
                              },
                              color: switch (e.level) {
                                'error' => theme.colorScheme.error,
                                'warning' => Colors.orange,
                                _ => theme.colorScheme.outline,
                              },
                            ),
                            // 长的只显示开头，点开看完整内容。
                            title: Text(e.message, maxLines: 2, overflow: TextOverflow.ellipsis),
                            subtitle: Text([
                              formatTime(e.ts),
                              _categories[e.category] ?? e.category,
                              if (e.accountId != null) '账号 ${e.accountId}',
                              if (e.data != null) e.data.toString(),
                            ].join(' · '), maxLines: 1, overflow: TextOverflow.ellipsis),
                            onTap: () => showDetailDialog(
                              context,
                              title: '运行事件 #${e.id}',
                              sections: [
                                DetailSection('内容', e.message),
                                DetailSection.fields('概要', {
                                  '时间': formatTime(e.ts),
                                  '级别': _levels[e.level] ?? e.level,
                                  '分类': _categories[e.category] ?? e.category,
                                  if (e.accountId != null) '账号': e.accountId,
                                }),
                                DetailSection.json('附加数据', e.data),
                              ],
                            ),
                          ),
                      ],
                    ),
            ),
          ],
        ));
  }
}

class _LogTab extends GetView<LogsController> {
  const _LogTab();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Obx(() => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
              child: Row(
                children: [
                  Expanded(
                    child: SelectableText(controller.logFile.value, style: theme.textTheme.bodySmall),
                  ),
                  const Text('自动刷新'),
                  Switch(value: controller.autoRefresh.value, onChanged: (v) => controller.autoRefresh.value = v),
                ],
              ),
            ),
            Expanded(
              child: Container(
                margin: const EdgeInsets.fromLTRB(16, 4, 16, 16),
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: theme.colorScheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(8),
                ),
                // 最新的日志在最下面，默认停在底部。
                child: ListView.builder(
                  reverse: true,
                  itemCount: controller.logLines.length,
                  itemBuilder: (context, i) {
                    final line = controller.logLines[controller.logLines.length - 1 - i];
                    final color = line.contains(' ERROR ')
                        ? theme.colorScheme.error
                        : line.contains(' WARNING ')
                            ? Colors.orange
                            : null;
                    return SelectableText(
                      line,
                      style: TextStyle(fontFamily: 'Consolas', fontFamilyFallback: const ['monospace'], fontSize: 12, color: color),
                    );
                  },
                ),
              ),
            ),
          ],
        ));
  }
}
