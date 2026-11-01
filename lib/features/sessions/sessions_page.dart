import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../core/api/models.dart';
import '../../core/ui/format.dart';
import '../../core/ui/placeholder_view.dart';
import '../../core/ui/scoped_settings_dialog.dart';
import '../../core/ui/turn_trace_dialog.dart';
import 'sessions_controller.dart';

const _recvSettingLabels = {
  'all': '接收全部消息',
  'only_mention': '仅 @机器人 的消息',
  'mention_and_context': '@机器人 的消息及上下文',
};

class SessionsPage extends GetView<SessionsController> {
  const SessionsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Obx(() {
      if (!controller.coreRunning) {
        return const PlaceholderView(
          title: '核心服务未运行',
          icon: Icons.power_off_outlined,
          description: '会话和聊天记录保存在核心服务里。请先在仪表盘启动核心服务。',
        );
      }
      if (controller.conversations.isEmpty) {
        return PlaceholderView(
          title: '还没有会话',
          icon: Icons.chat_bubble_outline,
          description: controller.error.value ?? '机器人在群里被 @ 或收到单聊消息后，会话会出现在这里。',
        );
      }
      return Row(
        children: [
          const SizedBox(width: 300, child: _ConversationList()),
          const VerticalDivider(width: 1),
          Expanded(child: controller.selected == null ? const SizedBox.shrink() : const _ConversationDetail()),
        ],
      );
    });
  }
}

class _ConversationList extends GetView<SessionsController> {
  const _ConversationList();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Obx(() => ListView(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 20, 8, 8),
              child: Row(
                children: [
                  Text('会话', style: theme.textTheme.titleLarge),
                  const Spacer(),
                  IconButton(tooltip: '刷新', onPressed: controller.loadConversations, icon: const Icon(Icons.refresh)),
                ],
              ),
            ),
            for (final c in controller.conversations)
              ListTile(
                selected: c.id == controller.selectedId.value,
                leading: Icon(c.isGroup ? Icons.groups_outlined : Icons.person_outline),
                title: Text(c.label, maxLines: 1, overflow: TextOverflow.ellipsis),
                subtitle: Text('${c.messageCount} 条消息 · ${formatTime(c.lastMessageAt)}'),
                onTap: () => controller.select(c.id),
              ),
          ],
        ));
  }
}

class _ConversationDetail extends GetView<SessionsController> {
  const _ConversationDetail();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Obx(() {
      final c = controller.selected!;
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(24, 20, 16, 8),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(c.label, style: theme.textTheme.titleLarge),
                      SelectableText(
                        '${c.isGroup ? '群' : '单聊'} · openid ${c.targetOpenid} · 当前历史第 ${c.historyEpoch + 1} 段',
                        style: theme.textTheme.bodySmall,
                      ),
                    ],
                  ),
                ),
                IconButton(tooltip: '备注名', icon: const Icon(Icons.edit_outlined), onPressed: () => _rename(context, c)),
                IconButton(
                  tooltip: '单独配置',
                  icon: const Icon(Icons.tune),
                  onPressed: () => showScopedSettingsDialog(
                    context,
                    scope: 'conversation',
                    scopeId: c.id,
                    accountId: c.accountId,
                    title: '「${c.label}」单独配置',
                    keys: conversationSettingKeys(isGroup: c.isGroup),
                  ),
                ),
                TextButton.icon(
                  onPressed: () => _confirmClear(context, c),
                  icon: const Icon(Icons.cleaning_services_outlined),
                  label: const Text('清空历史'),
                ),
              ],
            ),
          ),
          if (c.isGroup) const _BotStateBar(),
          const Divider(height: 1),
          Expanded(
            child: controller.loadingMessages.value
                ? const Center(child: CircularProgressIndicator())
                : const _MessageList(),
          ),
        ],
      );
    });
  }

  Future<void> _rename(BuildContext context, ConversationInfo c) async {
    final text = TextEditingController(text: c.displayName ?? '');
    final name = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('备注名'),
        content: TextField(
          controller: text,
          autofocus: true,
          decoration: const InputDecoration(helperText: '平台不提供群名和昵称，这里的名字只保存在本机'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(context, text.text), child: const Text('保存')),
        ],
      ),
    );
    text.dispose();
    if (name != null) _report(await controller.rename(name));
  }

  Future<void> _confirmClear(BuildContext context, ConversationInfo c) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('清空对话历史？'),
        content: const Text('之后的回复不再带上之前的内容。旧的聊天记录仍然保留，可以在这里查看。'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('清空')),
        ],
      ),
    );
    if (ok == true) _report(await controller.clearHistory());
  }
}

/// 群消息接收设置：只读，只能在 QQ 客户端的群设置里修改。
class _BotStateBar extends GetView<SessionsController> {
  const _BotStateBar();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Obx(() {
      final c = controller.selected!;
      final b = c.botState;
      final String text;
      if (b.recvMsgSetting != null) {
        text = _recvSettingLabels[b.recvMsgSetting] ?? b.recvMsgSetting!;
      } else if (c.receivedUnmentioned) {
        text = '接收全部消息（推断：收到过未 @ 的消息）';
      } else if (b.error != null || b.apiUnavailable) {
        text = '无法查询';
      } else {
        text = '还没查询';
      }
      return Padding(
        padding: const EdgeInsets.fromLTRB(24, 0, 16, 8),
        child: Row(
          children: [
            Text('消息接收：', style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.outline)),
            Flexible(child: Text(text, overflow: TextOverflow.ellipsis)),
            if (b.memberRole != null) Text('  ·  机器人角色：${b.memberRole}', style: theme.textTheme.bodySmall),
            if (b.checkedAt != null) Text('  ·  查询于 ${formatTime(b.checkedAt)}', style: theme.textTheme.bodySmall),
            const SizedBox(width: 4),
            controller.refreshingBotState.value
                ? const Padding(
                    padding: EdgeInsets.all(12),
                    child: SizedBox.square(dimension: 16, child: CircularProgressIndicator(strokeWidth: 2)),
                  )
                : IconButton(
                    tooltip: '刷新',
                    icon: const Icon(Icons.refresh, size: 20),
                    onPressed: () async => _report(await controller.refreshBotState()),
                  ),
            IconButton(
              tooltip: '说明',
              icon: const Icon(Icons.help_outline, size: 20),
              onPressed: () => _explain(context, b),
            ),
          ],
        ),
      );
    });
  }

  void _explain(BuildContext context, BotState b) {
    final noPermission = b.apiUnavailable || (b.error?.startsWith('11253') ?? false);
    showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('群消息接收设置'),
        content: Text(noPermission
            ? '这个机器人没有查询群状态的接口权限（错误码 11253，只对白名单机器人开放），需要向 QQ 开放平台申请。\n\n'
                '设置本身仍然可以在 QQ 客户端的群设置里修改。收到过未 @ 的群消息时，这里会按推断显示。'
            : '平台向机器人推送哪些群消息，由 QQ 客户端里的群设置决定，应用只能查询、不能修改。'
                '改完回来点刷新。\n\n'
                '推给机器人的群消息都会回复：设成「仅 @机器人」只回 @ 它的消息，设成「接收全部消息」每条都回。'
                '${b.error != null ? '\n\n最近一次查询失败：${b.error}' : ''}'),
        actions: [TextButton(onPressed: () => Navigator.pop(context), child: const Text('知道了'))],
      ),
    );
  }
}

class _MessageList extends GetView<SessionsController> {
  const _MessageList();

  @override
  Widget build(BuildContext context) {
    return Obx(() {
      final messages = controller.messages;
      if (messages.isEmpty) return const Center(child: Text('没有消息'));
      final items = <Widget>[];
      int? epoch;
      for (final m in _summaryFirst(messages)) {
        if (epoch != null && m.historyEpoch != epoch) {
          items.add(_EpochDivider(epoch: m.historyEpoch, summarized: m.isSummary));
        }
        epoch = m.historyEpoch;
        items.add(m.isSummary ? _SummaryNote(message: m) : _Bubble(message: m));
      }
      // 最新的消息在最下面，默认滚到底。
      return ListView(reverse: true, padding: const EdgeInsets.all(16), children: items.reversed.toList());
    });
  }

  /// 摘要是一段的开头，但写入得比压缩时挪过来的最近几轮晚，放回这一段最前面。
  static List<ChatMessage> _summaryFirst(List<ChatMessage> messages) {
    final ordered = <ChatMessage>[];
    var start = 0;
    for (var i = 1; i <= messages.length; i++) {
      if (i == messages.length || messages[i].historyEpoch != messages[start].historyEpoch) {
        final run = messages.sublist(start, i);
        ordered
          ..addAll(run.where((m) => m.isSummary))
          ..addAll(run.where((m) => !m.isSummary));
        start = i;
      }
    }
    return ordered;
  }
}

class _EpochDivider extends StatelessWidget {
  const _EpochDivider({required this.epoch, this.summarized = false});

  final int epoch;

  /// 这一段是历史压缩开始的：之前的内容换成了摘要和最近几轮原文。
  final bool summarized;

  @override
  Widget build(BuildContext context) {
    final style = Theme.of(context).textTheme.bodySmall?.copyWith(color: Theme.of(context).colorScheme.outline);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Row(
        children: [
          const Expanded(child: Divider()),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8),
            child: Text(
              summarized ? '历史第 ${epoch + 1} 段：较早的内容已压缩成摘要，最近几轮原文保留' : '历史第 ${epoch + 1} 段：之前的内容不再带入上下文',
              style: style,
            ),
          ),
          const Expanded(child: Divider()),
        ],
      ),
    );
  }
}

/// 历史压缩生成的摘要：模型在新一段里看到的是它，加上最近几轮原文。
class _SummaryNote extends StatelessWidget {
  const _SummaryNote({required this.message});

  final ChatMessage message;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: cs.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: cs.outlineVariant),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.summarize_outlined, size: 16, color: cs.onSurfaceVariant),
              const SizedBox(width: 6),
              Text('之前对话的摘要 · ${formatTime(message.createdAt)}',
                  style: theme.textTheme.labelMedium?.copyWith(color: cs.onSurfaceVariant)),
            ],
          ),
          const SizedBox(height: 6),
          SelectableText(message.content, style: theme.textTheme.bodyMedium),
        ],
      ),
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({required this.message});

  final ChatMessage message;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final m = message;
    final out = m.isOutgoing;
    final meta = <String>[
      formatTime(m.createdAt),
      if (!out && m.senderLabel != null) m.senderLabel!,
      if (!out && !m.triggered) '未触发回复',
      if (m.attachmentCount > 0) '${m.attachmentCount} 个附件',
      if (m.turn != null) ..._turnMeta(m.turn!),
    ];
    return Align(
      alignment: out ? Alignment.centerRight : Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 560),
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 4),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          decoration: BoxDecoration(
            color: out ? theme.colorScheme.primaryContainer : theme.colorScheme.surfaceContainerHighest,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Opacity(
            opacity: !out && !m.triggered ? 0.7 : 1,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SelectableText(m.content.isEmpty ? '（空消息）' : m.content),
                const SizedBox(height: 4),
                Text.rich(
                  TextSpan(children: [
                    TextSpan(text: meta.join(' · ')),
                    if (m.turn != null) ...[
                      const TextSpan(text: ' · '),
                      WidgetSpan(
                        alignment: PlaceholderAlignment.middle,
                        child: InkWell(
                          onTap: () => showTurnTraceDialog(context, m.turn!.id),
                          child: Text('处理过程',
                              style: theme.textTheme.labelSmall?.copyWith(
                                  color: theme.colorScheme.primary, decoration: TextDecoration.underline)),
                        ),
                      ),
                    ],
                  ]),
                  style: theme.textTheme.labelSmall?.copyWith(color: theme.colorScheme.outline),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  static List<String> _turnMeta(TurnSummary t) => [
        if (t.status == 'refused') '模型拒答',
        if (t.status == 'failed') '模型调用失败',
        t.model,
        '输入 ${t.inputTokens}${t.cacheReadTokens > 0 ? '（缓存 ${t.cacheReadTokens}）' : ''} / 输出 ${t.outputTokens} token',
        if (t.searchRequests > 0) '联网搜索 ${t.searchRequests} 次',
        formatUsd(t.costUsd),
        if (t.latencyMs != null) '${(t.latencyMs! / 1000).toStringAsFixed(1)} 秒',
      ];
}

void _report(String? error) {
  if (error != null) Get.snackbar('操作失败', error, snackPosition: SnackPosition.BOTTOM, maxWidth: 480);
}
