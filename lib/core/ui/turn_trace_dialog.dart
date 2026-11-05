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
  'judging': '判断中',
  'skipped': '跳过',
  'expired': '已过期',
};

Color turnStatusColor(String status) => switch (status) {
      'succeeded' => Colors.green,
      'running' || 'queued' || 'judging' => Colors.orange,
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
  'recalled': '已撤回',
};

const _kindIcons = {
  'judge': Icons.rule_outlined,
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
        for (final s in steps) _StepTile(turnId: t.id, step: s, t0: t0!),
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
  const _StepTile({required this.turnId, required this.step, required this.t0});

  final int turnId;
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
        if (s.hasRequest)
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              onPressed: () => showDialog<void>(
                context: context,
                builder: (_) => _RequestDialog(turnId: turnId, stepId: s.id),
              ),
              icon: const Icon(Icons.article_outlined, size: 18),
              label: const Text('查看这次请求的完整上下文'),
            ),
          ),
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

const _roles = {'system': '系统', 'user': '用户', 'assistant': '模型', 'tool': '工具结果'};

String _json(Object? v) => const JsonEncoder.withIndent('  ').convert(v);

/// 请求模型时实际发出去的完整请求：参数、系统提示词、工具定义、每条消息，标出这次新加的消息。
class _RequestDialog extends StatefulWidget {
  const _RequestDialog({required this.turnId, required this.stepId});

  final int turnId;
  final int stepId;

  @override
  State<_RequestDialog> createState() => _RequestDialogState();
}

class _RequestDialogState extends State<_RequestDialog> {
  late final Future<RequestSnapshot> _snapshot = _load();

  Future<RequestSnapshot> _load() async {
    final store = Get.find<ConnectionStore>();
    final api = store.api;
    if (api == null || store.status.value != CoreStatus.running) throw CoreApiException('核心服务未运行');
    return api.stepRequest(widget.turnId, widget.stepId);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return FutureBuilder<RequestSnapshot>(
      future: _snapshot,
      builder: (context, snapshot) {
        final data = snapshot.data;
        return AlertDialog(
          title: Row(
            children: [
              Expanded(child: Text(data == null ? '请求的完整上下文' : '第 ${data.index} / ${data.count} 次请求的完整上下文')),
              if (data != null)
                IconButton(
                  tooltip: '复制完整请求（JSON）',
                  onPressed: () => Clipboard.setData(ClipboardData(text: _json(data.request))),
                  icon: const Icon(Icons.copy_all_outlined),
                ),
            ],
          ),
          content: SizedBox(
            width: 820,
            child: snapshot.hasError
                ? Text('取不到完整上下文：${snapshot.error}', style: TextStyle(color: theme.colorScheme.error))
                : data == null
                    ? const Padding(
                        padding: EdgeInsets.all(24),
                        child: Center(
                            child: SizedBox.square(dimension: 20, child: CircularProgressIndicator(strokeWidth: 2))),
                      )
                    : SingleChildScrollView(child: _RequestBody(snapshot: data)),
          ),
          actions: [TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('关闭'))],
        );
      },
    );
  }
}

class _RequestBody extends StatelessWidget {
  const _RequestBody({required this.snapshot});

  final RequestSnapshot snapshot;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final request = snapshot.request;
    final messages = request['messages'] as List<dynamic>? ?? const [];
    final tools = request['tools'] as List<dynamic>? ?? const [];
    final system = request['system'];
    final params = [
      for (final e in request.entries)
        if (!const {'messages', 'tools', 'system'}.contains(e.key))
          '${e.key}: ${e.value is String ? e.value : jsonEncode(e.value)}',
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text('图片数据、加密内容和签名换成了占位文字，其余是实际发出去的原文。', style: theme.textTheme.bodySmall),
        const SizedBox(height: 12),
        Text('参数', style: theme.textTheme.labelLarge),
        const SizedBox(height: 4),
        _Block(text: params.join('\n'), monospace: true),
        if (system != null) ...[
          const SizedBox(height: 12),
          Text('系统提示词', style: theme.textTheme.labelLarge),
          const SizedBox(height: 4),
          _Block(text: system is String ? system : _json(system)),
        ],
        if (tools.isNotEmpty) ...[
          const SizedBox(height: 12),
          Text('工具（${tools.length} 个）', style: theme.textTheme.labelLarge),
          for (final tool in tools)
            ExpansionTile(
              dense: true,
              shape: const Border(),
              collapsedShape: const Border(),
              tilePadding: const EdgeInsets.symmetric(horizontal: 4),
              expandedCrossAxisAlignment: CrossAxisAlignment.stretch,
              title: Text(_toolName(tool)),
              children: [_Block(text: _json(tool), monospace: true)],
            ),
        ],
        const SizedBox(height: 12),
        Text(
          '消息（${messages.length} 条'
          '${snapshot.newFrom < messages.length ? '，第 ${snapshot.newFrom + 1} 条起是这次新加的' : ''}）',
          style: theme.textTheme.labelLarge,
        ),
        for (final (i, m) in messages.indexed) _MessageTile(index: i, message: m, isNew: i >= snapshot.newFrom),
      ],
    );
  }

  static String _toolName(Object? tool) {
    if (tool is! Map) return '$tool';
    final fn = tool['function'];
    final name = fn is Map ? fn['name'] : tool['name'];
    final type = tool['type'];
    return type is String && type != 'function' && type != 'custom' ? '$name（$type）' : '$name';
  }
}

typedef _Part = ({String? label, String text, bool mono});

class _MessageTile extends StatelessWidget {
  const _MessageTile({required this.index, required this.message, required this.isNew});

  final int index;
  final Object? message;
  final bool isNew;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final m = message is Map ? (message as Map).cast<String, dynamic>() : <String, dynamic>{'content': '$message'};
    final role = m['role'] as String? ?? '';
    final parts = _parts(m);
    final preview = parts.where((p) => p.label == null).map((p) => p.text).join(' ').replaceAll('\n', ' ');
    final labels = parts.map((p) => p.label).whereType<String>().toSet().join('、');
    return ExpansionTile(
      dense: true,
      shape: const Border(),
      collapsedShape: const Border(),
      tilePadding: const EdgeInsets.symmetric(horizontal: 4),
      childrenPadding: const EdgeInsets.fromLTRB(12, 0, 4, 8),
      expandedCrossAxisAlignment: CrossAxisAlignment.stretch,
      initiallyExpanded: isNew,
      title: Text.rich(
        TextSpan(children: [
          TextSpan(text: '#${index + 1} ${_roles[role] ?? role}', style: const TextStyle(fontWeight: FontWeight.w600)),
          if (isNew) TextSpan(text: '  新', style: TextStyle(color: theme.colorScheme.primary)),
          if (labels.isNotEmpty) TextSpan(text: '  [$labels]', style: TextStyle(color: theme.colorScheme.outline)),
          TextSpan(text: '  $preview'),
        ]),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      children: [
        for (final p in parts) ...[
          if (p.label != null) Text(p.label!, style: theme.textTheme.labelSmall),
          _Block(text: p.text.isEmpty ? '（空）' : p.text, monospace: p.mono),
        ],
      ],
    );
  }

  /// 一条消息拆成几段给人看：Claude 的各个内容块，DeepSeek 的 content、reasoning_content、tool_calls。
  static List<_Part> _parts(Map<String, dynamic> m) {
    final parts = <_Part>[];
    final reasoning = m['reasoning_content'];
    if (reasoning is String && reasoning.isNotEmpty) parts.add((label: '思考', text: reasoning, mono: false));
    final content = m['content'];
    if (content is String) {
      parts.add((label: null, text: content, mono: false));
    } else if (content is List) {
      parts.addAll(content.map(_block));
    }
    for (final call in m['tool_calls'] as List<dynamic>? ?? const []) {
      final fn = call is Map ? call['function'] : null;
      final args = fn is Map ? fn['arguments'] : null;
      parts.add((
        label: '调用工具 ${fn is Map ? fn['name'] : ''}',
        text: args is String ? _prettyJson(args) : _json(args),
        mono: true,
      ));
    }
    return parts;
  }

  static _Part _block(Object? b) {
    if (b is! Map) return (label: null, text: '$b', mono: false);
    final source = b['source'];
    final imageUrl = b['image_url'];
    final result = b['content'];
    return switch (b['type']) {
      'text' => (label: null, text: '${b['text'] ?? ''}', mono: false),
      'image' => (label: '图片', text: source is Map ? '${source['media_type']} ${source['data']}' : '', mono: false),
      'image_url' => (label: '图片', text: imageUrl is Map ? '${imageUrl['url']}' : '', mono: false),
      'thinking' => (label: '思考', text: '${b['thinking'] ?? ''}', mono: false),
      'tool_use' || 'server_tool_use' => (label: '调用工具 ${b['name']}', text: _json(b['input']), mono: true),
      'tool_result' => (
          label: b['is_error'] == true ? '工具结果（出错）' : '工具结果',
          text: result is List ? result.map((c) => _block(c).text).join('\n') : '${result ?? ''}',
          mono: false,
        ),
      final type => (label: '$type', text: _json(b), mono: true),
    };
  }

  static String _prettyJson(String raw) {
    try {
      return _json(jsonDecode(raw));
    } on FormatException {
      return raw;
    }
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
