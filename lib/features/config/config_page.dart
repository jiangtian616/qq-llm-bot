import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../core/api/models.dart';
import '../../core/ui/placeholder_view.dart';
import '../../core/ui/scoped_settings_dialog.dart';
import 'config_controller.dart';

const _effortLabels = {
  'none': '不思考（最快最省）',
  'low': '低（更快更省）',
  'medium': '中',
  'high': '高',
  'xhigh': '很高（费用明显更高）',
  'max': '最高（不限 token，费用最高）',
};

/// 新建或换模型时优先用的 effort：聊天用 low 更快更省。
const _preferredEffort = 'low';

/// 内容区最大宽度，窗口再宽也不把输入框拉得太长。
const _maxContentWidth = 920.0;

class ConfigPage extends GetView<ConfigController> {
  const ConfigPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Obx(() {
      if (!controller.coreRunning) {
        return const PlaceholderView(
          title: '核心服务未运行',
          icon: Icons.power_off_outlined,
          description: '配置保存在核心服务里。请先在仪表盘启动核心服务。',
        );
      }
      final theme = Theme.of(context);
      final providers = Map.of(controller.providers);
      return Theme(
        data: _formTheme(theme),
        child: LayoutBuilder(
          builder: (context, constraints) {
            final side = ((constraints.maxWidth - _maxContentWidth) / 2).clamp(24.0, double.infinity);
            return ListView(
              padding: EdgeInsets.fromLTRB(side, 24, side, 32),
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('配置', style: theme.textTheme.headlineSmall),
                          const SizedBox(height: 4),
                          Text(
                            '机器人账号、模型和回复规则，保存后立即生效',
                            style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                          ),
                        ],
                      ),
                    ),
                    IconButton(
                      tooltip: '刷新',
                      onPressed: controller.loading.value ? null : controller.load,
                      icon: const Icon(Icons.refresh),
                    ),
                  ],
                ),
                if (controller.error.value != null) ...[
                  const SizedBox(height: 16),
                  _Banner(text: controller.error.value!),
                ],
                const SizedBox(height: 24),
                const _AccountsSection(),
                for (final p in controller.profiles) ...[
                  const SizedBox(height: 20),
                  _ProfileSection(key: ValueKey(_profileKey(p)), profile: p, providers: providers),
                ],
                const SizedBox(height: 20),
                const _BehaviorSection(),
                const SizedBox(height: 20),
                _ContextSection(key: ValueKey(_contextKey(controller.settings))),
                const SizedBox(height: 20),
                _LimitsSection(key: ValueKey(_limitsKey(controller.settings))),
              ],
            );
          },
        ),
      );
    });
  }

  /// 配置变了就用新值重建输入框；保存结果的提示放在 controller 里，重建后还在。
  static String _limitsKey(Map<String, dynamic> s) => [for (final k in _LimitsSectionState.keys) s[k]].join('|');

  static String _contextKey(Map<String, dynamic> s) => [for (final k in _ContextSectionState.keys) s[k]].join('|');

  static String _profileKey(ProfileInfo p) =>
      '${p.id}|${p.name}|${p.provider}|${p.model}|${p.apiKeyHint}|${p.params}|${p.systemPrompt.hashCode}';
}

/// 配置页和它弹出的对话框共用的输入框样式：描边、圆角、紧凑。
ThemeData _formTheme(ThemeData theme) => theme.copyWith(
      inputDecorationTheme: InputDecorationTheme(
        isDense: true,
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
        helperMaxLines: 2,
        errorMaxLines: 2,
      ),
    );

// ---------------- 通用部件 ----------------

/// 一组配置：图标、标题、说明，右上角放操作按钮。
class _Section extends StatelessWidget {
  const _Section({
    required this.icon,
    required this.title,
    this.subtitle,
    this.badges = const [],
    this.action,
    required this.child,
  });

  final IconData icon;
  final String title;
  final String? subtitle;
  final List<Widget> badges;
  final Widget? action;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return Card(
      margin: EdgeInsets.zero,
      elevation: 0,
      color: cs.surfaceContainerLowest,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(color: cs.outlineVariant),
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(24, 20, 24, 24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Container(
                  width: 36,
                  height: 36,
                  decoration: BoxDecoration(color: cs.primaryContainer, borderRadius: BorderRadius.circular(10)),
                  child: Icon(icon, size: 20, color: cs.onPrimaryContainer),
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Wrap(
                        spacing: 8,
                        runSpacing: 4,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: [
                          Text(title, style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
                          ...badges,
                        ],
                      ),
                      if (subtitle != null) ...[
                        const SizedBox(height: 2),
                        Text(subtitle!, style: theme.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant)),
                      ],
                    ],
                  ),
                ),
                if (action != null) ...[const SizedBox(width: 12), action!],
              ],
            ),
            const SizedBox(height: 20),
            child,
          ],
        ),
      ),
    );
  }
}

/// 小标签，用在标题旁边。
class _Badge extends StatelessWidget {
  const _Badge(this.text, {this.warning = false});

  final String text;
  final bool warning;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: warning ? cs.errorContainer : cs.secondaryContainer,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        text,
        style: Theme.of(context).textTheme.labelSmall?.copyWith(
              color: warning ? cs.onErrorContainer : cs.onSecondaryContainer,
            ),
      ),
    );
  }
}

/// 页面顶部的错误条。
class _Banner extends StatelessWidget {
  const _Banner({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(color: cs.errorContainer, borderRadius: BorderRadius.circular(12)),
      child: Row(
        children: [
          Icon(Icons.error_outline, size: 20, color: cs.onErrorContainer),
          const SizedBox(width: 12),
          Expanded(child: Text(text, style: TextStyle(color: cs.onErrorContainer))),
        ],
      ),
    );
  }
}

/// 输入框按两列排，窄窗口时一列。
class _FieldGrid extends StatelessWidget {
  const _FieldGrid({required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, constraints) {
      const gap = 16.0;
      final columns = constraints.maxWidth >= 560 ? 2 : 1;
      final width = (constraints.maxWidth - gap * (columns - 1)) / columns;
      return Wrap(
        spacing: gap,
        runSpacing: 20,
        children: [for (final c in children) SizedBox(width: width, child: c)],
      );
    });
  }
}

/// 一行开关或选择：左边标题和说明，右边控件。
class _SettingRow extends StatelessWidget {
  const _SettingRow({required this.title, this.subtitle, required this.trailing});

  final String title;
  final String? subtitle;
  final Widget trailing;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: theme.textTheme.bodyLarge),
                if (subtitle != null) ...[
                  const SizedBox(height: 2),
                  Text(subtitle!, style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
                ],
              ],
            ),
          ),
          const SizedBox(width: 24),
          trailing,
        ],
      ),
    );
  }
}

/// 保存结果的提示：成功是主题色，失败是错误色。
class _Notice extends StatelessWidget {
  const _Notice(this.text, {required this.error});

  final String text;
  final bool error;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final color = error ? cs.error : cs.primary;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(error ? Icons.error_outline : Icons.check_circle_outline, size: 18, color: color),
        const SizedBox(width: 6),
        Flexible(child: Text(text, style: TextStyle(color: color))),
      ],
    );
  }
}

/// 卡片底部：左边次要操作，右边提示和保存按钮。
class _Footer extends StatelessWidget {
  const _Footer({this.leading, this.notice, required this.onSave});

  final Widget? leading;
  final Widget? notice;
  final VoidCallback? onSave;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 24),
      child: Row(
        children: [
          ?leading,
          // 提示占满中间的空间并靠右，保存按钮始终贴在右边。
          Expanded(child: Align(alignment: Alignment.centerRight, child: notice)),
          const SizedBox(width: 16),
          FilledButton(onPressed: onSave, child: const Text('保存')),
        ],
      ),
    );
  }
}

// ---------------- 机器人账号 ----------------

class _AccountsSection extends GetView<ConfigController> {
  const _AccountsSection();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return _Section(
      icon: Icons.smart_toy_outlined,
      title: '机器人账号',
      subtitle: '在 QQ 开放平台（q.qq.com）创建的机器人',
      action: FilledButton.tonalIcon(
        onPressed: () => showDialog<void>(context: context, builder: (_) => const _AccountDialog()),
        icon: const Icon(Icons.add, size: 18),
        label: const Text('添加机器人'),
      ),
      child: Obx(() {
        final accounts = controller.accounts;
        if (accounts.isEmpty) {
          return Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: theme.colorScheme.surfaceContainer,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Text(
              '还没有机器人。在 QQ 开放平台创建机器人，在开发设置里拿到 AppID 和 AppSecret 后添加到这里。',
              style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
          );
        }
        return Container(
          decoration: BoxDecoration(
            border: Border.all(color: theme.colorScheme.outlineVariant),
            borderRadius: BorderRadius.circular(12),
          ),
          child: Column(
            children: [
              for (final (i, a) in accounts.indexed) ...[
                if (i > 0) const Divider(height: 1),
                _AccountRow(account: a),
              ],
            ],
          ),
        );
      }),
    );
  }
}

class _AccountRow extends GetView<ConfigController> {
  const _AccountRow({required this.account});

  final AccountInfo account;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final a = account;
    final online = a.connection.state == 'online';
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 10, 4, 10),
      child: Row(
        children: [
          Stack(
            clipBehavior: Clip.none,
            children: [
              CircleAvatar(
                radius: 18,
                backgroundColor: cs.secondaryContainer,
                child: Icon(Icons.smart_toy_outlined, size: 20, color: cs.onSecondaryContainer),
              ),
              Positioned(
                right: -1,
                bottom: -1,
                child: Tooltip(
                  message: online ? '已连接' : '未连接',
                  child: Container(
                    width: 11,
                    height: 11,
                    decoration: BoxDecoration(
                      color: online ? Colors.green : cs.outline,
                      shape: BoxShape.circle,
                      border: Border.all(color: cs.surfaceContainerLowest, width: 2),
                    ),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Wrap(
                  spacing: 8,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    Text(a.displayName, style: theme.textTheme.bodyLarge?.copyWith(fontWeight: FontWeight.w500)),
                    if (!a.enabled) const _Badge('已停用'),
                    if (!a.secretSet) const _Badge('AppSecret 未设置', warning: true),
                  ],
                ),
                const SizedBox(height: 2),
                Text(
                  ['AppID ${a.appId}', if (a.secretSet) 'AppSecret ${a.secretHint}'].join('  ·  '),
                  style: theme.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant),
                ),
              ],
            ),
          ),
          Text('自动连接', style: theme.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant)),
          const SizedBox(width: 4),
          Transform.scale(
            scale: 0.8,
            child: Switch(
              value: a.autoConnect,
              onChanged: (v) => _report(controller.updateAccount(a.id, {'auto_connect': v})),
            ),
          ),
          PopupMenuButton<String>(
            tooltip: '更多',
            icon: const Icon(Icons.more_vert),
            onSelected: (v) => switch (v) {
              'scoped' => showScopedSettingsDialog(
                  context,
                  scope: 'account',
                  scopeId: a.id,
                  title: '「${a.displayName}」单独配置',
                  keys: accountSettingKeys,
                ),
              'edit' => showDialog<void>(context: context, builder: (_) => _AccountDialog(account: a)),
              _ => _confirmDelete(context, a),
            },
            itemBuilder: (context) => const [
              PopupMenuItem(value: 'scoped', child: ListTile(leading: Icon(Icons.tune), title: Text('单独配置'))),
              PopupMenuItem(value: 'edit', child: ListTile(leading: Icon(Icons.edit_outlined), title: Text('编辑'))),
              PopupMenuDivider(),
              PopupMenuItem(value: 'delete', child: ListTile(leading: Icon(Icons.delete_outline), title: Text('删除'))),
            ],
          ),
        ],
      ),
    );
  }

  Future<void> _confirmDelete(BuildContext context, AccountInfo a) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('删除「${a.displayName}」？'),
        content: const Text('会断开连接，删除这个机器人的会话记录，并从系统密钥库里删除它的 AppSecret。'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('取消')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Theme.of(context).colorScheme.error),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (ok == true) await _report(controller.deleteAccount(a.id));
  }
}

class _AccountDialog extends StatefulWidget {
  const _AccountDialog({this.account});

  final AccountInfo? account;

  @override
  State<_AccountDialog> createState() => _AccountDialogState();
}

class _AccountDialogState extends State<_AccountDialog> {
  final _form = GlobalKey<FormState>();
  late final _appId = TextEditingController(text: widget.account?.appId);
  late final _secret = TextEditingController();
  late final _name = TextEditingController(text: widget.account?.displayName);
  bool _autoConnect = false;
  bool _saving = false;
  String? _error;

  bool get _editing => widget.account != null;

  @override
  void dispose() {
    _appId.dispose();
    _secret.dispose();
    _name.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (!_form.currentState!.validate()) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    final c = Get.find<ConfigController>();
    final error = _editing
        ? await c.updateAccount(widget.account!.id, {
            'display_name': _name.text.trim(),
            if (_secret.text.trim().isNotEmpty) 'app_secret': _secret.text.trim(),
          })
        : await c.addAccount(
            appId: _appId.text.trim(),
            appSecret: _secret.text.trim(),
            displayName: _name.text.trim(),
            autoConnect: _autoConnect,
          );
    if (!mounted) return;
    if (error == null) {
      Navigator.pop(context);
    } else {
      setState(() {
        _saving = false;
        _error = error;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Theme(
      data: _formTheme(Theme.of(context)),
      child: AlertDialog(
        title: Text(_editing ? '编辑机器人' : '添加机器人'),
        content: SizedBox(
          width: 440,
          child: Form(
            key: _form,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const SizedBox(height: 8),
                TextFormField(
                  controller: _appId,
                  enabled: !_editing,
                  decoration: const InputDecoration(labelText: 'AppID'),
                  validator: (v) => (v == null || v.trim().isEmpty) ? '请填写 AppID' : null,
                ),
                const SizedBox(height: 20),
                TextFormField(
                  controller: _secret,
                  obscureText: true,
                  decoration: InputDecoration(
                    labelText: 'AppSecret',
                    helperText: _editing ? '留空表示不修改（当前 ${widget.account!.secretHint ?? '未设置'}）' : '只存系统密钥库，不写数据库和日志',
                  ),
                  validator: (v) => (!_editing && (v == null || v.trim().isEmpty)) ? '请填写 AppSecret' : null,
                ),
                const SizedBox(height: 20),
                TextFormField(
                  controller: _name,
                  decoration: const InputDecoration(labelText: '名称（可选）', helperText: '只在本机显示'),
                ),
                if (!_editing) ...[
                  const SizedBox(height: 8),
                  CheckboxListTile(
                    contentPadding: EdgeInsets.zero,
                    value: _autoConnect,
                    onChanged: (v) => setState(() => _autoConnect = v ?? false),
                    title: const Text('核心服务启动后自动连接'),
                  ),
                ],
                if (_error != null) ...[
                  const SizedBox(height: 12),
                  Align(alignment: Alignment.centerLeft, child: _Notice(_error!, error: true)),
                ],
              ],
            ),
          ),
        ),
        actions: [
          TextButton(onPressed: _saving ? null : () => Navigator.pop(context), child: const Text('取消')),
          FilledButton(onPressed: _saving ? null : _save, child: Text(_editing ? '保存' : '添加')),
        ],
      ),
    );
  }
}

// ---------------- 模型配置 ----------------

class _ProfileSection extends StatefulWidget {
  const _ProfileSection({super.key, required this.profile, required this.providers});

  final ProfileInfo profile;

  /// 核心服务里装了的提供商，按名字索引。
  final Map<String, ProviderSpec> providers;

  @override
  State<_ProfileSection> createState() => _ProfileSectionState();
}

class _ProfileSectionState extends State<_ProfileSection> {
  late final _name = TextEditingController(text: widget.profile.name);
  late final _apiKey = TextEditingController();
  late final _maxTokens = TextEditingController(text: '${widget.profile.params['max_tokens'] ?? 1500}');
  late final _prompt = TextEditingController(text: widget.profile.systemPrompt);
  late String _provider = widget.profile.provider;
  late String _model = widget.profile.model;
  late String? _effort = widget.profile.params['effort'] as String?;
  bool _saving = false;
  String? _message;
  bool _messageIsError = false;

  @override
  void dispose() {
    _name.dispose();
    _apiKey.dispose();
    _maxTokens.dispose();
    _prompt.dispose();
    super.dispose();
  }

  /// 当前选的提供商的说明；核心服务没有这个插件时为 null，只能改名称、API Key 和提示词。
  ProviderSpec? get _spec => widget.providers[_provider];

  bool get _providerChanged => _provider != widget.profile.provider;

  ModelSpec? get _modelSpec => _spec?.model(_model);

  /// 当前模型能用的 effort：已选的不支持时换成 low，没有 low 就用模型默认值。
  String? _effortFor(ModelSpec? model, String? current) {
    if (model == null || model.efforts.isEmpty) return null;
    if (current != null && model.efforts.contains(current)) return current;
    return model.efforts.contains(_preferredEffort) ? _preferredEffort : model.defaultEffort;
  }

  int get _maxTokensLimit {
    final spec = _spec;
    final model = _modelSpec;
    if (spec == null || model == null) return 16000;
    return model.maxOutputTokens < spec.maxTokensLimit ? model.maxOutputTokens : spec.maxTokensLimit;
  }

  void _selectProvider(String? name) {
    if (name == null || name == _provider) return;
    setState(() {
      _provider = name;
      // 换回原来的提供商时恢复原来的模型，否则用新提供商的默认模型。
      _model = _providerChanged ? widget.providers[name]!.defaultModel : widget.profile.model;
      _effort = _effortFor(_modelSpec, _effort);
      _message = null;
    });
  }

  void _fail(String message) => setState(() {
        _message = message;
        _messageIsError = true;
      });

  Future<void> _save() async {
    final spec = _spec;
    final maxTokens = int.tryParse(_maxTokens.text.trim());
    if (maxTokens == null || maxTokens < 64 || maxTokens > _maxTokensLimit) {
      return _fail('最大输出 token 要是 64 到 $_maxTokensLimit 之间的整数');
    }
    if (_providerChanged && _apiKey.text.trim().isEmpty) {
      return _fail('换了提供商，要填写 ${spec?.displayName ?? _provider} 的 API Key');
    }
    final effort = _effortFor(_modelSpec, _effort);
    // 换提供商时旧的参数不一定通用，只带上界面里的这几项。
    final params = {if (!_providerChanged) ...widget.profile.params, 'max_tokens': maxTokens}..remove('effort');
    setState(() => _saving = true);
    final error = await Get.find<ConfigController>().updateProfile(widget.profile.id, {
      'name': _name.text.trim().isEmpty ? widget.profile.name : _name.text.trim(),
      if (_providerChanged) 'provider': _provider,
      if (spec != null) 'model': _model,
      'system_prompt': _prompt.text,
      'params': {...params, 'effort': ?effort},
      if (_apiKey.text.trim().isNotEmpty) 'api_key': _apiKey.text.trim(),
    });
    if (!mounted) return;
    setState(() {
      _saving = false;
      _message = error ?? '已保存';
      _messageIsError = error != null;
    });
  }

  @override
  Widget build(BuildContext context) {
    final p = widget.profile;
    final spec = _spec;
    final model = _modelSpec;
    return _Section(
      icon: Icons.psychology_outlined,
      title: '模型配置',
      subtitle: '回复用哪个提供商的哪个模型',
      badges: [
        if (p.isDefault) const _Badge('默认'),
        if (!p.apiKeySet) const _Badge('未填写 API Key，机器人无法回复', warning: true),
      ],
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _FieldGrid(children: [
            TextField(controller: _name, decoration: const InputDecoration(labelText: '名称')),
            DropdownButtonFormField<String>(
              initialValue: spec != null ? _provider : null,
              isExpanded: true,
              decoration: InputDecoration(
                labelText: '模型提供商',
                errorText: spec == null ? '核心服务没有「$_provider」插件，请重新选择' : null,
              ),
              items: [
                for (final s in widget.providers.values) DropdownMenuItem(value: s.name, child: Text(s.displayName)),
              ],
              onChanged: _selectProvider,
            ),
            if (spec != null) ...[
              DropdownButtonFormField<String>(
                key: ValueKey('model|$_provider'),
                initialValue: model != null ? _model : null,
                isExpanded: true,
                decoration: InputDecoration(
                  labelText: '模型',
                  helperText: model == null
                      ? '当前是 $_model，目录里没有这个模型，请重新选择'
                      : '每百万 token：输入 \$${model.inputPrice} / 输出 \$${model.outputPrice}'
                          '${model.vision ? '' : '；不能看图，图片只以「[图片]」告诉模型'}',
                ),
                items: [
                  for (final m in spec.models)
                    DropdownMenuItem(
                      value: m.id,
                      child: Text(m.legacy ? '${m.label}（上一代）' : m.label, overflow: TextOverflow.ellipsis),
                    ),
                ],
                onChanged: (v) => setState(() {
                  _model = v ?? _model;
                  _effort = _effortFor(_modelSpec, _effort);
                }),
              ),
              if (model != null && model.efforts.isNotEmpty)
                DropdownButtonFormField<String>(
                  key: ValueKey('effort|$_provider|${model.id}'),
                  initialValue: _effortFor(model, _effort),
                  isExpanded: true,
                  decoration: const InputDecoration(labelText: '思考力度（effort）'),
                  items: [
                    for (final e in model.efforts)
                      DropdownMenuItem(
                        value: e,
                        child: Text(
                          '${_effortLabels[e] ?? e}${e == model.defaultEffort ? '（模型默认）' : ''}',
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                  ],
                  onChanged: (v) => setState(() => _effort = v ?? _effort),
                )
              else
                const InputDecorator(
                  decoration: InputDecoration(labelText: '思考力度（effort）', enabled: false),
                  child: Text('这个模型不支持'),
                ),
            ],
            TextField(
              controller: _maxTokens,
              keyboardType: TextInputType.number,
              decoration: InputDecoration(
                labelText: '最大输出 token',
                helperText: '上限 $_maxTokensLimit；思考也算在里面',
              ),
            ),
            TextField(
              controller: _apiKey,
              obscureText: true,
              decoration: InputDecoration(
                labelText: spec?.apiKeyLabel ?? 'API Key',
                helperText: _providerChanged
                    ? '换了提供商，需要填写新的 API Key；只存系统密钥库'
                    : p.apiKeySet
                        ? '当前 ${p.apiKeyHint}；留空表示不修改'
                        : spec?.apiKeyHelp ?? '只存系统密钥库',
              ),
            ),
          ]),
          const SizedBox(height: 20),
          TextField(
            controller: _prompt,
            minLines: 3,
            maxLines: 8,
            decoration: const InputDecoration(
              labelText: '系统提示词',
              helperText: '保持固定有利于提示缓存，能省钱',
              alignLabelWithHint: true,
            ),
          ),
          _Footer(
            notice: _message == null ? null : _Notice(_message!, error: _messageIsError),
            onSave: _saving ? null : _save,
          ),
        ],
      ),
    );
  }
}

// ---------------- 机器人行为 ----------------

class _BehaviorSection extends GetView<ConfigController> {
  const _BehaviorSection();

  @override
  Widget build(BuildContext context) {
    return _Section(
      icon: Icons.forum_outlined,
      title: '机器人行为',
      subtitle: '全局生效；要给某个机器人或会话单独设置，点它旁边的「单独配置」',
      child: Obx(() {
        final s = controller.settings;
        return Column(
          children: [
            _SettingRow(
              title: '群聊消息',
              subtitle: '推给机器人的群消息都会回复。推哪些由 QQ 客户端里的群设置决定：'
                  '「仅 @机器人」只回 @ 它的消息，「接收全部消息」每条都回',
              trailing: const SizedBox.shrink(),
            ),
            const Divider(height: 1),
            _SettingRow(
              title: '回复单聊消息',
              trailing: Switch(
                value: (s['c2c.enabled'] as bool?) ?? true,
                onChanged: (v) => _report(controller.setSetting('c2c.enabled', v)),
              ),
            ),
            const Divider(height: 1),
            _SettingRow(
              title: '让模型看图片',
              subtitle: '用户发的图片交给模型（每轮最多 4 张，每张大约多花 1000~1600 个输入 token）；关掉时模型只知道这里有张图',
              trailing: Switch(
                value: (s['vision.enabled'] as bool?) ?? true,
                onChanged: (v) => _report(controller.setSetting('vision.enabled', v)),
              ),
            ),
          ],
        );
      }),
    );
  }
}

// ---------------- 上下文与历史 ----------------

class _ContextSection extends StatefulWidget {
  const _ContextSection({super.key});

  @override
  State<_ContextSection> createState() => _ContextSectionState();
}

class _ContextSectionState extends State<_ContextSection> {
  static const keys = [
    'group.context_max_messages',
    'group.context_max_chars',
    'history.compact_at_tokens',
    'history.max_turns',
    'history.keep_recent_turns',
  ];

  final _c = Get.find<ConfigController>();
  late final Map<String, TextEditingController> _fields = {
    for (final k in keys) k: TextEditingController(text: _c.settings[k] == null ? '' : '${_c.settings[k]}'),
  };
  bool _saving = false;

  @override
  void dispose() {
    for (final f in _fields.values) {
      f.dispose();
    }
    super.dispose();
  }

  void _show(String message, {bool error = false}) => _c.contextNotice.value = (message, error);

  Future<void> _save() async {
    final values = <String, Object?>{};
    for (final k in keys) {
      final v = int.tryParse(_fields[k]!.text.trim());
      if (v == null || v < 0) return _show('这几项都要填不小于 0 的整数', error: true);
      values[k] = v;
    }
    setState(() => _saving = true);
    final error = await _c.setSettings(values);
    if (!mounted) return;
    setState(() => _saving = false);
    _show(error ?? '已保存', error: error != null);
  }

  Widget _field(String key, String label, String helper, {String? suffix}) => TextField(
        controller: _fields[key],
        keyboardType: TextInputType.number,
        decoration: InputDecoration(labelText: label, helperText: helper, suffixText: suffix),
      );

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return _Section(
      icon: Icons.history_outlined,
      title: '上下文与历史',
      subtitle: '全局生效；控制每次请求带多少历史，历史太长时较早的部分压成摘要',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _FieldGrid(children: [
            _field('group.context_max_messages', '期间的群消息最多带', '被限频或预算挡下、没回复的群消息，只带最新的；0 表示不限',
                suffix: '条'),
            _field('group.context_max_chars', '期间的群消息最多带', '更早的只告诉模型省略了几条；0 表示不限', suffix: '字'),
            _field('history.compact_at_tokens', '历史超过多少时压缩', '按下一次请求的大小估算；0 表示不按 token 压缩',
                suffix: 'token'),
            _field('history.max_turns', '或者超过多少轮时压缩', '0 表示不按轮数压缩', suffix: '轮'),
            _field('history.keep_recent_turns', '压缩时原文保留最近', '更早的部分换成摘要', suffix: '轮'),
          ]),
          const SizedBox(height: 16),
          Text(
            '压缩用 Claude 的按需压缩：服务端写摘要，费用和普通请求一样，算进每日预算。'
            '模型不支持压缩时（比如 Haiku 4.5），只保留最近几轮。',
            style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
          Obx(() {
            final notice = _c.contextNotice.value;
            return _Footer(
              notice: notice == null ? null : _Notice(notice.$1, error: notice.$2),
              onSave: _saving ? null : _save,
            );
          }),
        ],
      ),
    );
  }
}

// ---------------- 限频、预算与数据保留 ----------------

class _LimitsSection extends StatefulWidget {
  const _LimitsSection({super.key});

  @override
  State<_LimitsSection> createState() => _LimitsSectionState();
}

class _LimitsSectionState extends State<_LimitsSection> {
  static const keys = [
    'rate_limit.per_user_per_min',
    'budget.daily_usd',
    'retention.messages_days',
    'retention.raw_events_days',
  ];

  final _c = Get.find<ConfigController>();
  late final Map<String, TextEditingController> _fields = {
    for (final k in keys) k: TextEditingController(text: _c.settings[k] == null ? '' : '${_c.settings[k]}'),
  };
  bool _saving = false;
  bool _running = false;

  @override
  void dispose() {
    for (final f in _fields.values) {
      f.dispose();
    }
    super.dispose();
  }

  void _show(String message, {bool error = false}) => _c.limitsNotice.value = (message, error);

  Future<void> _save() async {
    final values = <String, Object?>{};
    for (final k in ['rate_limit.per_user_per_min', 'retention.messages_days', 'retention.raw_events_days']) {
      final v = int.tryParse(_fields[k]!.text.trim());
      if (v == null || v < 0) return _show('限频和保留天数要填不小于 0 的整数', error: true);
      values[k] = v;
    }
    final budgetText = _fields['budget.daily_usd']!.text.trim();
    if (budgetText.isEmpty) {
      values['budget.daily_usd'] = null;
    } else {
      final budget = double.tryParse(budgetText);
      if (budget == null || budget <= 0) return _show('每日费用上限要填大于 0 的数字，留空表示不限', error: true);
      values['budget.daily_usd'] = budget;
    }
    setState(() => _saving = true);
    final error = await _c.setSettings(values);
    if (!mounted) return;
    setState(() => _saving = false);
    _show(error ?? '已保存', error: error != null);
  }

  Future<void> _runNow() async {
    setState(() => _running = true);
    try {
      final r = await _c.runMaintenance();
      _show('已清理 ${r.deleted} 行超期数据${r.backup != null ? '，并备份了数据库' : '；今天已经备份过'}');
    } on Object catch (e) {
      _show(e.toString(), error: true);
    } finally {
      if (mounted) setState(() => _running = false);
    }
  }

  Widget _field(String key, String label, String helper, {String? suffix}) => TextField(
        controller: _fields[key],
        keyboardType: TextInputType.number,
        decoration: InputDecoration(labelText: label, helperText: helper, suffixText: suffix),
      );

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return _Section(
      icon: Icons.savings_outlined,
      title: '限频、预算与数据保留',
      subtitle: '全局生效；每天自动清理一次超期数据，并备份数据库（保留最近 7 份）',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _FieldGrid(children: [
            _field('rate_limit.per_user_per_min', '每个用户每分钟最多触发', '超过后只回一次提醒；0 表示不限', suffix: '次'),
            _field('budget.daily_usd', '每日费用上限', '用完后今天暂停回复；留空表示不限', suffix: '美元'),
            _field('retention.messages_days', '聊天记录保留', '0 表示永久保留', suffix: '天'),
            _field('retention.raw_events_days', '原始事件保留', '收件箱里处理完的事件；0 表示永久', suffix: '天'),
          ]),
          const SizedBox(height: 16),
          Text(
            '建议同时在 Claude Console 给 API Key 所在的工作区设花费上限。',
            style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
          Obx(() {
            final notice = _c.limitsNotice.value;
            return _Footer(
              leading: OutlinedButton.icon(
                onPressed: _running ? null : _runNow,
                icon: const Icon(Icons.cleaning_services_outlined, size: 18),
                label: const Text('立即清理并备份'),
              ),
              notice: notice == null ? null : _Notice(notice.$1, error: notice.$2),
              onSave: _saving ? null : _save,
            );
          }),
        ],
      ),
    );
  }
}

Future<void> _report(Future<String?> action) async {
  final error = await action;
  if (error != null) {
    Get.snackbar('操作失败', error, snackPosition: SnackPosition.BOTTOM, maxWidth: 480);
  }
}
