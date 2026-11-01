import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:get/get.dart';

import '../api/core_api_client.dart';
import '../api/models.dart';
import '../connection/connection_store.dart';
import 'format.dart';

const turnStatuses = {
  'succeeded': '成功',
  'failed': '失败',
  'refused': '拒答',
  'running': '处理中',
  'queued': '排队中',
  'expired': '已过期',
};

Color turnStatusColor(String status) => switch (status) {
      'succeeded' => Colors.green,
      'running' || 'queued' => Colors.orange,
      'failed' => Colors.red,
      _ => Colors.grey,
    };

const _outboxStatuses = {
  'pending': '待发送',
  'sending': '发送中',
  'sent': '已发送',
  'failed': '失败',
  'expired': '已过期',
  'cancelled': '已取消',
};

const _kindIcons = {
  'context': Icons.input,
  'request': Icons.smart_toy_outlined,
  'search': Icons.travel_explore,
  'tool': Icons.build_outlined,
  'reply': Icons.reply,
  'compose': Icons.view_agenda_outlined,
  'compaction': Icons.compress,
  'note': Icons.info_outline,
  'error': Icons.error_outline,
};

/// 一次问答的处理过程：组装上下文、每次请求模型、联网搜索、工具调用、回复编排、历史压缩，按时间排列。
Future<void> showTurnTraceDialog(BuildContext context, int turnId) =>
    showDialog<void>(context: context, builder: (_) => _TurnTraceDialog(turnId: turnId));

String formatMs(int ms) => ms < 1000 ? '$ms 毫秒' : '${(ms / 1000).toStringAsFixed(1)} 秒';

class _TurnTraceDialog extends StatefulWidget {
  const _TurnTraceDialog({required this.turnId});

  final int turnId;

  @override
  State<_TurnTraceDialog> createState() => _TurnTraceDialogState();
}

class _TurnTraceDialogState extends State<_TurnTraceDialog> {
  late Future<TurnDetail> _detail = _load();

  Future<TurnDetail> _load() async {
    final store = Get.find<ConnectionStore>();
    final api = store.api;
    if (api == null || store.status.value != CoreStatus.running) throw CoreApiException('核心服务未运行');
    return api.turnDetail(widget.turnId);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return AlertDialog(
      title: Row(
        children: [
          Expanded(child: Text('问答 #${widget.turnId} 的处理过程')),
          IconButton(
            tooltip: '刷新',
            onPressed: () => setState(() => _detail = _load()),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      content: SizedBox(
        width: 820,
        child: FutureBuilder<TurnDetail>(
          future: _detail,
          builder: (context, snapshot) {
            if (snapshot.hasError) {
              return Text('取不到处理过程：${snapshot.error}', style: TextStyle(color: theme.colorScheme.error));
            }
            if (!snapshot.hasData) {
              return const Padding(
                padding: EdgeInsets.all(24),
                child: Center(child: SizedBox.square(dimension: 20, child: CircularProgressIndicator(strokeWidth: 2))),
              );
            }
            return SingleChildScrollView(child: _Body(detail: snapshot.data!));
          },
        ),
      ),
      actions: [TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('关闭'))],
    );
  }
}

class _Body extends StatelessWidget {
  const _Body({required this.detail});

  final TurnDetail detail;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final t = detail.turn;
    final steps = detail.steps;
    final t0 = steps.isEmpty ? null : steps.first.startedAt;
    final summary = <String, String>{
      '会话': t.conversationLabel,
      '状态': turnStatuses[t.status] ?? t.status,
      '模型': '${t.provider} / ${t.model}',
      if (t.stopReason != null) '结束原因': t.stopReason!,
      '用量': '输入 ${t.inputTokens}（缓存读 ${t.cacheReadTokens}，写 ${t.cacheWriteTokens}）/ 输出 ${t.outputTokens} token',
      '费用': formatUsd(t.costUsd),
      if (t.searchRequests > 0) '联网搜索': '${t.searchRequests} 次',
      if (t.toolRounds > 0) '工具调用': '${t.toolRounds} 轮',
      if (t.latencyMs != null) '模型耗时': formatMs(t.latencyMs!),
      '开始': formatTime(t.startedAt),
    };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        Wrap(
          spacing: 20,
          runSpacing: 4,
          children: [
            for (final e in summary.entries)
              Text.rich(TextSpan(children: [
                TextSpan(text: '${e.key}：', style: TextStyle(color: theme.colorScheme.outline)),
                TextSpan(text: e.value),
              ])),
          ],
        ),
        if (t.lastError != null && t.status == 'failed') ...[
          const SizedBox(height: 8),
          _Block(text: t.lastError!, color: theme.colorScheme.error),
        ],
        const SizedBox(height: 12),
        Text('提问${detail.sender != null ? '（${detail.sender}）' : ''}', style: theme.textTheme.labelLarge),
        const SizedBox(height: 4),
        _Block(text: detail.question.isEmpty ? '（空消息）' : detail.question),
        const SizedBox(height: 12),
        Text('处理过程（${steps.length} 步，点开看详情）', style: theme.textTheme.labelLarge),
        if (steps.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Text('没有记录。升级之前的问答没有处理过程。', style: theme.textTheme.bodySmall),
          ),
        for (final s in steps) _StepTile(step: s, t0: t0!),
        if (detail.outbox.isNotEmpty) ...[
          const SizedBox(height: 12),
          Text('发出的回复（${detail.outbox.length} 段）', style: theme.textTheme.labelLarge),
          const SizedBox(height: 4),
          for (final (i, o) in detail.outbox.indexed)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 2),
              child: Text(
                '${i + 1}. [${_outboxStatuses[o.status] ?? o.status}${o.attempts > 1 ? ' · 尝试 ${o.attempts} 次' : ''}] ${o.preview}',
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
            ),
        ],
      ],
    );
  }
}

class _StepTile extends StatelessWidget {
  const _StepTile({required this.step, required this.t0});

  final TurnStep step;
  final DateTime t0;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final s = step;
    final failed = s.error != null;
    final offset = s.startedAt.difference(t0).inMilliseconds;
    final meta = [
      '+${(offset / 1000).toStringAsFixed(2)} 秒',
      if (s.durationMs != null) '耗时 ${formatMs(s.durationMs!)}',
      if (failed) s.error!.split('\n').first,
    ];
    return ExpansionTile(
      dense: true,
      shape: const Border(),
      collapsedShape: const Border(),
      tilePadding: const EdgeInsets.symmetric(horizontal: 4),
      childrenPadding: const EdgeInsets.fromLTRB(44, 0, 4, 12),
      expandedCrossAxisAlignment: CrossAxisAlignment.stretch,
      initiallyExpanded: s.kind == 'error',
      leading: Icon(_kindIcons[s.kind] ?? Icons.circle_outlined,
          size: 20, color: failed ? theme.colorScheme.error : theme.colorScheme.primary),
      title: Text(s.title, maxLines: 2, overflow: TextOverflow.ellipsis),
      subtitle: Text(meta.join(' · '),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(color: failed ? theme.colorScheme.error : theme.colorScheme.outline)),
      children: [
        if (s.detail.isNotEmpty) _Block(text: s.detail),
        if (failed) _Block(text: s.error!, color: theme.colorScheme.error),
        if (s.data != null)
          ExpansionTile(
            dense: true,
            shape: const Border(),
            collapsedShape: const Border(),
            tilePadding: EdgeInsets.zero,
            expandedCrossAxisAlignment: CrossAxisAlignment.stretch,
            title: Text('原始数据（JSON，已去掉图片数据和加密内容）', style: theme.textTheme.bodySmall),
            children: [_Block(text: const JsonEncoder.withIndent('  ').convert(s.data), monospace: true)],
          ),
      ],
    );
  }
}

/// 一段可选中、可复制的文字。
class _Block extends StatelessWidget {
  const _Block({required this.text, this.color, this.monospace = false});

  final String text;
  final Color? color;
  final bool monospace;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      margin: const EdgeInsets.only(bottom: 6),
      padding: const EdgeInsets.fromLTRB(10, 8, 4, 8),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: SelectableText(
              text,
              style: monospace
                  ? TextStyle(fontFamily: 'Consolas', fontFamilyFallback: const ['monospace'], fontSize: 12, color: color)
                  : theme.textTheme.bodySmall?.copyWith(color: color),
            ),
          ),
          IconButton(
            tooltip: '复制',
            iconSize: 14,
            visualDensity: VisualDensity.compact,
            onPressed: () => Clipboard.setData(ClipboardData(text: text)),
            icon: const Icon(Icons.copy),
          ),
        ],
      ),
    );
  }
}
