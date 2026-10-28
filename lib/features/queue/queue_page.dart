import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../core/api/models.dart';
import '../../core/ui/format.dart';
import '../../core/ui/placeholder_view.dart';
import 'queue_controller.dart';

const _inboxStatuses = {
  'pending': '待处理',
  'processing': '处理中',
  'done': '已处理',
  'ignored': '已忽略',
  'expired': '已过期',
  'failed': '失败',
};

const _outboxStatuses = {
  'pending': '待发送',
  'sending': '发送中',
  'sent': '已发送',
  'failed': '失败',
  'expired': '已过期',
  'cancelled': '已取消',
};

const _eventTypes = {
  'GROUP_AT_MESSAGE_CREATE': '群聊 @',
  'GROUP_MESSAGE_CREATE': '群聊',
  'C2C_MESSAGE_CREATE': '单聊',
};

class QueuePage extends GetView<QueueController> {
  const QueuePage({super.key});

  @override
  Widget build(BuildContext context) {
    return Obx(() {
      if (!controller.coreRunning) {
        return const PlaceholderView(
          title: '核心服务未运行',
          icon: Icons.power_off_outlined,
          description: '收件箱和发件箱保存在核心服务里。请先在仪表盘启动核心服务。',
        );
      }
      return DefaultTabController(
        length: 2,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 24, 16, 0),
              child: Row(
                children: [
                  Text('消息队列', style: Theme.of(context).textTheme.headlineSmall),
                  const Spacer(),
                  IconButton(tooltip: '刷新', onPressed: controller.load, icon: const Icon(Icons.refresh)),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 4, 24, 0),
              child: Text(
                '收到的事件先写进收件箱再处理；生成的回复先写进发件箱再逐段发出。失败的可以在回复窗口内（群 5 分钟、单聊 60 分钟）重试。',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
            if (controller.error.value != null)
              Padding(
                padding: const EdgeInsets.fromLTRB(24, 8, 24, 0),
                child: Text(controller.error.value!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
              ),
            TabBar(tabs: [
              Tab(text: '收件箱（${_total(controller.inboxCounts)}）'),
              Tab(text: '发件箱（${_total(controller.outboxCounts)}）'),
            ]),
            const Expanded(child: TabBarView(children: [_InboxTab(), _OutboxTab()])),
          ],
        ),
      );
    });
  }

  static int _total(Map<String, int> counts) => counts.values.fold(0, (a, b) => a + b);
}

class _StatusFilter extends StatelessWidget {
  const _StatusFilter({required this.labels, required this.counts, required this.selected});

  final Map<String, String> labels;
  final Map<String, int> counts;
  final RxnString selected;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
      child: Wrap(
        spacing: 8,
        children: [
          ChoiceChip(label: const Text('全部'), selected: selected.value == null, onSelected: (_) => selected.value = null),
          for (final e in labels.entries)
            ChoiceChip(
              label: Text('${e.value} ${counts[e.key] ?? 0}'),
              selected: selected.value == e.key,
              onSelected: (_) => selected.value = e.key,
            ),
        ],
      ),
    );
  }
}

class _InboxTab extends GetView<QueueController> {
  const _InboxTab();

  @override
  Widget build(BuildContext context) {
    return Obx(() => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _StatusFilter(labels: _inboxStatuses, counts: controller.inboxCounts, selected: controller.inboxStatus),
            Expanded(
              child: controller.inbox.isEmpty
                  ? const Center(child: Text('没有事件'))
                  : ListView(
                      children: [
                        for (final e in controller.inbox)
                          ListTile(
                            leading: _StatusChip(status: e.status, label: _inboxStatuses[e.status] ?? e.status),
                            title: Text(e.preview.isEmpty ? '（无内容）' : e.preview, maxLines: 1, overflow: TextOverflow.ellipsis),
                            subtitle: Text([
                              _eventTypes[e.eventType] ?? e.eventType,
                              '收到 ${formatTime(e.receivedAt)}',
                              if (e.replyDeadline != null) '回复截止 ${formatTime(e.replyDeadline)}',
                              if (e.attempts > 1) '处理 ${e.attempts} 次',
                              if (e.lastError != null) e.lastError!,
                            ].join(' · '), maxLines: 2, overflow: TextOverflow.ellipsis),
                            trailing: e.status == 'failed'
                                ? TextButton(onPressed: () async => _report(await controller.retryInbox(e.id)), child: const Text('重试'))
                                : null,
                          ),
                      ],
                    ),
            ),
          ],
        ));
  }
}

class _OutboxTab extends GetView<QueueController> {
  const _OutboxTab();

  @override
  Widget build(BuildContext context) {
    return Obx(() => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _StatusFilter(labels: _outboxStatuses, counts: controller.outboxCounts, selected: controller.outboxStatus),
            Expanded(
              child: controller.outbox.isEmpty
                  ? const Center(child: Text('没有回复'))
                  : ListView(
                      children: [
                        for (final o in controller.outbox)
                          ListTile(
                            leading: _StatusChip(status: o.status, label: _outboxStatuses[o.status] ?? o.status),
                            title: Text(o.preview, maxLines: 1, overflow: TextOverflow.ellipsis),
                            subtitle: Text(_outboxMeta(o), maxLines: 2, overflow: TextOverflow.ellipsis),
                            trailing: switch (o.status) {
                              'failed' => TextButton(
                                  onPressed: () async => _report(await controller.retryOutbox(o.id)),
                                  child: const Text('重试'),
                                ),
                              'pending' => TextButton(
                                  onPressed: () async => _report(await controller.cancelOutbox(o.id)),
                                  child: const Text('取消'),
                                ),
                              _ => null,
                            },
                          ),
                      ],
                    ),
            ),
          ],
        ));
  }

  static String _outboxMeta(OutboxItem o) => [
        o.conversationLabel,
        if (o.chunkCount > 1) '第 ${o.chunkIndex + 1}/${o.chunkCount} 段',
        'msg_seq ${o.msgSeq}',
        o.sentAt != null ? '发送于 ${formatTime(o.sentAt)}' : '生成于 ${formatTime(o.createdAt)}',
        if (o.deadline != null && o.sentAt == null) '截止 ${formatTime(o.deadline)}',
        if (o.attempts > 1) '尝试 ${o.attempts} 次',
        if (o.status == 'pending' && o.nextAttemptAt != null && o.attempts > 0) '下次重试 ${formatTime(o.nextAttemptAt)}',
        if (o.lastError != null && o.status != 'sent') '${o.lastErrorCode != null ? '[${o.lastErrorCode}] ' : ''}${o.lastError}',
      ].join(' · ');
}

class _StatusChip extends StatelessWidget {
  const _StatusChip({required this.status, required this.label});

  final String status;
  final String label;

  @override
  Widget build(BuildContext context) {
    final color = switch (status) {
      'done' || 'sent' => Colors.green,
      'pending' || 'processing' || 'sending' => Colors.orange,
      'failed' => Colors.red,
      _ => Colors.grey,
    };
    return Container(
      width: 64,
      padding: const EdgeInsets.symmetric(vertical: 4),
      decoration: BoxDecoration(color: color.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(6)),
      child: Text(label, textAlign: TextAlign.center, style: TextStyle(color: color, fontSize: 12)),
    );
  }
}

void _report(String? error) {
  if (error != null) Get.snackbar('操作失败', error, snackPosition: SnackPosition.BOTTOM, maxWidth: 480);
}
