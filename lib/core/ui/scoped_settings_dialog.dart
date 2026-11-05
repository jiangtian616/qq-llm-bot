import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../api/core_api_client.dart';
import '../api/models.dart';
import '../connection/connection_store.dart';

/// group.reply_mode 的选项：值 → 界面上的说法。配置页也用它。
const groupReplyModes = {
  'mention': '只回复 @ 它的',
  'all': '每条都回复',
  'auto': '由模型判断',
};

/// 某个配置项在界面上怎么编辑。longText 是多行文字，编辑框放在这一行下面。
enum _Kind { toggle, integer, decimal, text, longText, profile, choice }

class _Spec {
  const _Spec(this.label, this.kind, {this.helper, this.min = 0, this.nullLabel, this.choices = const {}});

  final String label;
  final _Kind kind;
  final String? helper;
  final num min;

  /// 值为空时显示什么（比如「不限」）。
  final String? nullLabel;

  /// choice 的选项：值 → 说法。
  final Map<String, String> choices;
}

const _specs = <String, _Spec>{
  'group.reply_mode': _Spec('群聊消息回复哪些', _Kind.choice,
      helper: 'QQ 群设置成「接收全部消息」时才有区别；引用机器人的消息也算 @ 它', choices: groupReplyModes),
  'group.judge.profile_id': _Spec('判断要不要回复用的模型配置', _Kind.profile, nullLabel: '和回复用同一个'),
  'group.judge.prompt': _Spec('判断要不要回复的标准', _Kind.longText, helper: '「由模型判断」时用；最近的聊天和输出格式由程序补上'),
  'group.context_max_messages': _Spec('回复时最多带上多少条期间的群消息', _Kind.integer,
      helper: '只带最新的，更早的只告诉模型省略了几条；0 表示不限'),
  'group.context_max_chars': _Spec('期间的群消息最多带多少字', _Kind.integer, helper: '0 表示不限'),
  'c2c.enabled': _Spec('回复单聊消息', _Kind.toggle),
  'vision.enabled': _Spec('让模型看图片', _Kind.toggle, helper: '每张图大约多花 1000~1600 个输入 token'),
  'provider_profile_id': _Spec('模型配置', _Kind.profile, nullLabel: '第一个模型配置'),
  'history.compact_at_tokens': _Spec('历史超过多少 token 时压缩', _Kind.integer,
      helper: '较早的部分压成摘要、最近几轮原文保留；0 表示不按 token 压缩'),
  'history.max_turns': _Spec('历史超过多少轮时压缩', _Kind.integer, helper: '0 表示不按轮数压缩'),
  'history.keep_recent_turns': _Spec('压缩时原文保留最近几轮', _Kind.integer),
  'reply.max_chunks': _Spec('一条回复最多拆几段', _Kind.integer, min: 1, nullLabel: '平台上限（群 5、单聊 4）'),
  'reply.rich_media.enabled': _Spec('图文回复', _Kind.toggle, helper: '模型按需要回复文字、图片或图文卡片'),
  'tools.web_search.enabled': _Spec('联网搜索', _Kind.toggle, helper: '用提供商自带的搜索，按次计费，算进每日预算'),
  'tools.web_search.max_uses': _Spec('每轮最多联网搜索几次', _Kind.integer, helper: '0 表示不限'),
  'rate_limit.per_user_per_min': _Spec('每个用户每分钟最多触发', _Kind.integer, helper: '0 表示不限'),
  'command.prefix': _Spec('指令前缀', _Kind.text, helper: '如 /清空、/帮助 里的 /'),
  'budget.daily_usd': _Spec('这个账号的每日费用上限（美元）', _Kind.decimal,
      helper: '和全局上限各算各的，任何一个用完都暂停', nullLabel: '不单独设上限（全局上限照常生效）'),
  'retention.messages_days': _Spec('聊天记录保留天数', _Kind.integer, helper: '0 表示永久保留'),
  'retention.raw_events_days': _Spec('原始事件保留天数', _Kind.integer, helper: '0 表示永久'),
  'retention.request_snapshots_days':
      _Spec('请求快照保留天数', _Kind.integer, helper: '问答过程里的完整上下文；0 表示跟着聊天记录清理'),
};

/// 不从上一层继承的配置项：账号预算只算这个账号的花费，和全局预算同时生效。
const _notInherited = {'budget.daily_usd'};

/// 账号可以单独设置的配置项。
const accountSettingKeys = [
  'group.reply_mode',
  'group.judge.profile_id',
  'group.judge.prompt',
  'group.context_max_messages',
  'group.context_max_chars',
  'c2c.enabled',
  'vision.enabled',
  'provider_profile_id',
  'history.compact_at_tokens',
  'history.max_turns',
  'history.keep_recent_turns',
  'reply.max_chunks',
  'reply.rich_media.enabled',
  'tools.web_search.enabled',
  'tools.web_search.max_uses',
  'rate_limit.per_user_per_min',
  'command.prefix',
  'budget.daily_usd',
  'retention.messages_days',
  'retention.raw_events_days',
  'retention.request_snapshots_days',
];

/// 会话可以单独设置的配置项；群和单聊各自只显示用得上的。
List<String> conversationSettingKeys({required bool isGroup}) => [
      if (isGroup) ...[
        'group.reply_mode',
        'group.judge.profile_id',
        'group.judge.prompt',
        'group.context_max_messages',
        'group.context_max_chars',
      ] else
        'c2c.enabled',
      'vision.enabled',
      'provider_profile_id',
      'history.compact_at_tokens',
      'history.max_turns',
      'history.keep_recent_turns',
      'reply.max_chunks',
      'reply.rich_media.enabled',
      'tools.web_search.enabled',
      'tools.web_search.max_uses',
      'rate_limit.per_user_per_min',
      'command.prefix',
    ];

/// 打开「单独配置」对话框。scope 是 account 或 conversation；会话要同时给出所属账号，用来算继承下来的值。
Future<void> showScopedSettingsDialog(
  BuildContext context, {
  required String scope,
  required int scopeId,
  required String title,
  required List<String> keys,
  int? accountId,
}) =>
    showDialog<void>(
      context: context,
      builder: (_) => _ScopedSettingsDialog(scope: scope, scopeId: scopeId, title: title, keys: keys, accountId: accountId),
    );

class _ScopedSettingsDialog extends StatefulWidget {
  const _ScopedSettingsDialog({
    required this.scope,
    required this.scopeId,
    required this.title,
    required this.keys,
    this.accountId,
  });

  final String scope;
  final int scopeId;
  final String title;
  final List<String> keys;
  final int? accountId;

  @override
  State<_ScopedSettingsDialog> createState() => _ScopedSettingsDialogState();
}

class _ScopedSettingsDialogState extends State<_ScopedSettingsDialog> {
  /// 上一层的值（默认值叠加全局，会话再叠加账号）和它来自哪一层。
  final _inherited = <String, (Object?, String)>{};

  /// 这一层现在保存着的覆盖值。
  Map<String, dynamic> _saved = {};

  /// 界面上勾选了「单独设置」的项和编辑中的值。
  final _overridden = <String>{};
  final _values = <String, Object?>{};
  final _texts = <String, TextEditingController>{};
  List<ProfileInfo> _profiles = [];

  bool _loading = true;
  bool _saving = false;
  String? _error;

  CoreApiClient get _api {
    final store = Get.find<ConnectionStore>();
    final api = store.api;
    if (api == null || store.status.value != CoreStatus.running) throw CoreApiException('核心服务未运行');
    return api;
  }

  /// 模型配置只有一个时，单独选模型没有意义，不显示这两项。
  List<String> get _keys => [
        for (final k in widget.keys)
          if (_specs[k]!.kind != _Kind.profile || _profiles.length > 1) k,
      ];

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    for (final t in _texts.values) {
      t.dispose();
    }
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final api = _api;
      final global = await api.getSettings();
      final account = widget.scope == 'conversation' && widget.accountId != null
          ? (await api.getSettings(scope: 'account', scopeId: widget.accountId!)).values
          : const <String, dynamic>{};
      final own = await api.getSettings(scope: widget.scope, scopeId: widget.scopeId);
      _profiles = await api.listProfiles();
      for (final k in widget.keys) {
        _inherited[k] = _notInherited.contains(k)
            ? (null, '')
            : account.containsKey(k)
                ? (account[k], '账号配置')
                : (global.values.containsKey(k) ? global.values[k] : global.defaults[k], '全局配置');
      }
      _saved = own.values;
      for (final k in widget.keys) {
        final overridden = _saved.containsKey(k);
        if (overridden) _overridden.add(k);
        final value = overridden ? _saved[k] : _inherited[k]!.$1;
        _values[k] = value;
        _texts[k] = TextEditingController(text: value == null ? '' : '$value');
      }
      _error = null;
    } on Object catch (e) {
      _error = e.toString();
    }
    if (mounted) setState(() => _loading = false);
  }

  String _display(String key, Object? value) {
    final spec = _specs[key]!;
    if (value == null) return spec.nullLabel ?? '未设置';
    return switch (spec.kind) {
      _Kind.toggle => value == true ? '开' : '关',
      _Kind.profile => _profiles.firstWhereOrNull((p) => p.id == value)?.name ?? '#$value',
      _Kind.choice => spec.choices['$value'] ?? '$value',
      _Kind.longText => _ellipsis('$value'.replaceAll('\n', ' '), 40),
      _ => '$value',
    };
  }

  /// 把输入框里的文字转成要保存的值；不合法时返回错误说明。
  (Object?, String?) _parse(String key) {
    final spec = _specs[key]!;
    final text = _texts[key]!.text.trim();
    switch (spec.kind) {
      case _Kind.integer:
        final v = int.tryParse(text);
        if (v == null || v < spec.min) return (null, '「${spec.label}」要填不小于 ${spec.min} 的整数');
        return (v, null);
      case _Kind.decimal:
        final v = double.tryParse(text);
        if (v == null || v <= 0) return (null, '「${spec.label}」要填大于 0 的数字');
        return (v, null);
      case _Kind.text || _Kind.longText:
        if (text.isEmpty) return (null, '「${spec.label}」不能为空');
        return (text, null);
      default:
        return (_values[key], null);
    }
  }

  Future<void> _save() async {
    final changes = <String, Object?>{};
    for (final k in _keys) {
      if (_overridden.contains(k)) {
        final (value, error) = _parse(k);
        if (error != null) return setState(() => _error = error);
        if (!_saved.containsKey(k) || _saved[k] != value) changes[k] = value;
      } else if (_saved.containsKey(k)) {
        changes[k] = null; // 取消单独设置：删掉这一层的值，改为沿用上一层
      }
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      for (final e in changes.entries) {
        await _api.putSetting(e.key, e.value, scope: widget.scope, scopeId: widget.scopeId);
      }
      if (mounted) Navigator.pop(context);
    } on Object catch (e) {
      if (mounted) {
        setState(() {
          _saving = false;
          _error = e.toString();
        });
      }
    }
  }

  Widget _editor(String key) {
    final spec = _specs[key]!;
    void set(Object? v) => setState(() => _values[key] = v);
    return switch (spec.kind) {
      _Kind.toggle => Switch(value: _values[key] == true, onChanged: set),
      _Kind.profile => DropdownButton<int>(
          value: _profiles.any((p) => p.id == _values[key]) ? _values[key] as int : _profiles.first.id,
          items: [for (final p in _profiles) DropdownMenuItem(value: p.id, child: Text('${p.name}（${p.model}）'))],
          onChanged: set,
        ),
      _Kind.choice => DropdownButton<String>(
          value: spec.choices.containsKey(_values[key]) ? _values[key] as String : spec.choices.keys.first,
          items: [for (final e in spec.choices.entries) DropdownMenuItem(value: e.key, child: Text(e.value))],
          onChanged: set,
        ),
      _Kind.longText => const SizedBox.shrink(),
      _ => SizedBox(
          width: 140,
          child: TextField(
            controller: _texts[key],
            keyboardType: spec.kind == _Kind.text ? TextInputType.text : TextInputType.number,
            decoration: const InputDecoration(isDense: true),
          ),
        ),
    };
  }

  Widget _row(BuildContext context, String key) {
    final theme = Theme.of(context);
    final spec = _specs[key]!;
    final overridden = _overridden.contains(key);
    final (inheritedValue, source) = _inherited[key]!;
    final row = Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Checkbox(
            value: overridden,
            onChanged: (v) => setState(() {
              if (v == true) {
                _overridden.add(key);
                // 开始单独设置时，从继承下来的值改起。
                _values[key] ??= inheritedValue;
                // 继承下来的是「空」（比如「第一个模型配置」）时，下拉框显示第一项，保存的也要是这一项。
                if (spec.kind == _Kind.profile) _values[key] ??= _profiles.first.id;
                if (spec.kind == _Kind.choice) _values[key] ??= spec.choices.keys.first;
                if (_texts[key]!.text.isEmpty && inheritedValue != null) _texts[key]!.text = '$inheritedValue';
              } else {
                _overridden.remove(key);
              }
            }),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(spec.label),
                Text(
                  overridden
                      ? (spec.helper ?? '单独设置')
                      : source.isEmpty
                          ? _display(key, inheritedValue)
                          : '沿用$source：${_display(key, inheritedValue)}',
                  style: theme.textTheme.bodySmall,
                ),
              ],
            ),
          ),
          if (overridden) _editor(key),
        ],
      ),
    );
    if (spec.kind != _Kind.longText || !overridden) return row;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        row,
        Padding(
          padding: const EdgeInsets.only(left: 48, bottom: 8),
          child: TextField(controller: _texts[key], minLines: 3, maxLines: 8),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return AlertDialog(
      title: Text(widget.title),
      content: SizedBox(
        width: 560,
        child: _loading
            ? const SizedBox(height: 120, child: Center(child: CircularProgressIndicator()))
            : SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      widget.scope == 'account'
                          ? '勾选的项只对这个机器人生效，其余沿用全局配置。'
                          : '勾选的项只对这个会话生效，其余沿用账号或全局配置。',
                      style: theme.textTheme.bodySmall,
                    ),
                    const SizedBox(height: 8),
                    for (final k in _keys) _row(context, k),
                    if (_error != null)
                      Padding(
                        padding: const EdgeInsets.only(top: 8),
                        child: Text(_error!, style: TextStyle(color: theme.colorScheme.error)),
                      ),
                  ],
                ),
              ),
      ),
      actions: [
        TextButton(onPressed: _saving ? null : () => Navigator.pop(context), child: const Text('取消')),
        FilledButton(onPressed: _saving || _loading || _inherited.isEmpty ? null : _save, child: const Text('保存')),
      ],
    );
  }
}

String _ellipsis(String text, int n) => text.length <= n ? text : '${text.substring(0, n - 1)}…';
