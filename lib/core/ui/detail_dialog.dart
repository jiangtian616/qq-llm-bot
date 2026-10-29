import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// 详情对话框里的一段：标题加一段可选中、可复制的文字。
class DetailSection {
  const DetailSection(this.label, this.text, {this.monospace = false});

  /// JSON 按缩进排版，等宽显示。字符串原样显示，空值不显示。
  static DetailSection? json(String label, Object? value) {
    if (value == null) return null;
    final text = value is String ? value : const JsonEncoder.withIndent('  ').convert(value);
    return DetailSection(label, text, monospace: true);
  }

  /// 多个「名称：值」拼成一段，值为空的跳过。
  static DetailSection fields(String label, Map<String, Object?> fields) => DetailSection(label, [
        for (final e in fields.entries)
          if (e.value != null && e.value.toString().isNotEmpty) '${e.key}：${e.value}',
      ].join('\n'));

  final String label;
  final String text;
  final bool monospace;
}

/// 查看列表里被截断的长文本。sections 立即显示；load 给出的部分（比如要另外请求的完整内容）拉到后接在后面。
Future<void> showDetailDialog(
  BuildContext context, {
  required String title,
  required List<DetailSection?> sections,
  Future<List<DetailSection?>> Function()? load,
}) =>
    showDialog<void>(
      context: context,
      builder: (_) => _DetailDialog(title: title, sections: sections.nonNulls.toList(), load: load),
    );

class _DetailDialog extends StatefulWidget {
  const _DetailDialog({required this.title, required this.sections, this.load});

  final String title;
  final List<DetailSection> sections;
  final Future<List<DetailSection?>> Function()? load;

  @override
  State<_DetailDialog> createState() => _DetailDialogState();
}

class _DetailDialogState extends State<_DetailDialog> {
  Future<List<DetailSection?>>? _loaded;

  @override
  void initState() {
    super.initState();
    _loaded = widget.load?.call();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return AlertDialog(
      title: Text(widget.title),
      content: SizedBox(
        width: 640,
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: [
              for (final s in widget.sections) _Section(section: s),
              if (_loaded != null)
                FutureBuilder<List<DetailSection?>>(
                  future: _loaded,
                  builder: (context, snapshot) {
                    if (snapshot.hasError) {
                      return Padding(
                        padding: const EdgeInsets.only(top: 8),
                        child: Text('取不到完整内容：${snapshot.error}', style: TextStyle(color: theme.colorScheme.error)),
                      );
                    }
                    if (!snapshot.hasData) {
                      return const Padding(
                        padding: EdgeInsets.all(16),
                        child: Center(child: SizedBox.square(dimension: 20, child: CircularProgressIndicator(strokeWidth: 2))),
                      );
                    }
                    return Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [for (final s in snapshot.data!.nonNulls) _Section(section: s)],
                    );
                  },
                ),
            ],
          ),
        ),
      ),
      actions: [TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('关闭'))],
    );
  }
}

class _Section extends StatelessWidget {
  const _Section({required this.section});

  final DetailSection section;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    if (section.text.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(child: Text(section.label, style: theme.textTheme.labelLarge)),
              IconButton(
                tooltip: '复制',
                iconSize: 16,
                visualDensity: VisualDensity.compact,
                onPressed: () => Clipboard.setData(ClipboardData(text: section.text)),
                icon: const Icon(Icons.copy),
              ),
            ],
          ),
          Container(
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: theme.colorScheme.surfaceContainerHighest,
              borderRadius: BorderRadius.circular(6),
            ),
            child: SelectableText(
              section.text,
              style: section.monospace
                  ? const TextStyle(fontFamily: 'Consolas', fontFamilyFallback: ['monospace'], fontSize: 12)
                  : theme.textTheme.bodyMedium,
            ),
          ),
        ],
      ),
    );
  }
}
