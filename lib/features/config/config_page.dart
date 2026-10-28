import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../core/api/models.dart';
import '../../core/ui/placeholder_view.dart';
import 'config_controller.dart';

const _models = {
  'claude-haiku-5-5': 'Claude Haiku 5.5（最快最省，适合闲聊）',
  'claude-sonnet-5-5': 'Claude Sonnet 5.5（推理更强）',
  'claude-opus-5-5': 'Claude Opus 5.5（长文和复杂分析）',
};

const _efforts = {'low': '低（更快更省）', 'medium': '中', 'high': '高'};

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
      return ListView(
        padding: const EdgeInsets.all(24),
        children: [
          Row(
            children: [
              Text('配置', style: theme.textTheme.headlineSmall),
              const Spacer(),
              IconButton(tooltip: '刷新', onPressed: controller.load, icon: const Icon(Icons.refresh)),
            ],
          ),
          if (controller.error.value != null) ...[
            const SizedBox(height: 8),
            Text(controller.error.value!, style: TextStyle(color: theme.colorScheme.error)),
          ],
          const SizedBox(height: 16),
          const _AccountsCard(),
          const SizedBox(height: 16),
          for (final p in controller.profiles) ...[
            _ProfileCard(key: ValueKey(_profileKey(p)), profile: p),
            const SizedBox(height: 16),
          ],
          const _BehaviorCard(),
        ],
      );
    });
  }

  static String _profileKey(ProfileInfo p) => '${p.id}|${p.name}|${p.model}|${p.apiKeyHint}|${p.params}|${p.systemPrompt.hashCode}';
}

// ---------------- 机器人账号 ----------------

class _AccountsCard extends GetView<ConfigController> {
  const _AccountsCard();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Obx(() {
          final accounts = controller.accounts;
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Text('机器人账号', style: theme.textTheme.titleMedium),
                  const Spacer(),
                  FilledButton.tonalIcon(
                    onPressed: () => showDialog<void>(context: context, builder: (_) => const _AccountDialog()),
                    icon: const Icon(Icons.add),
                    label: const Text('添加机器人'),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              if (accounts.isEmpty)
                Text(
                  '还没有机器人。在 QQ 开放平台（q.qq.com）创建机器人，在开发设置里拿到 AppID 和 AppSecret 后添加到这里。'
                  '群聊和单聊权限审核通过前，只能在沙箱群和沙箱用户里测试。',
                  style: theme.textTheme.bodyMedium,
                ),
              for (final a in accounts)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.smart_toy_outlined),
                  title: Text(a.displayName),
                  subtitle: Text([
                    'AppID ${a.appId}',
                    if (a.isSandbox) '沙箱',
                    if (!a.enabled) '已停用',
                    a.secretSet ? 'AppSecret ${a.secretHint}' : 'AppSecret 未设置',
                  ].join(' · ')),
                  trailing: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Text('启动后自动连接'),
                      Switch(
                        value: a.autoConnect,
                        onChanged: (v) => _report(controller.updateAccount(a.id, {'auto_connect': v})),
                      ),
                      IconButton(
                        tooltip: '编辑',
                        icon: const Icon(Icons.edit_outlined),
                        onPressed: () => showDialog<void>(context: context, builder: (_) => _AccountDialog(account: a)),
                      ),
                      IconButton(
                        tooltip: '删除',
                        icon: const Icon(Icons.delete_outline),
                        onPressed: () => _confirmDelete(context, a),
                      ),
                    ],
                  ),
                ),
            ],
          );
        }),
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
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('删除')),
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
  late bool _sandbox = widget.account?.isSandbox ?? true;
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
            'is_sandbox': _sandbox,
            if (_secret.text.trim().isNotEmpty) 'app_secret': _secret.text.trim(),
          })
        : await c.addAccount(
            appId: _appId.text.trim(),
            appSecret: _secret.text.trim(),
            displayName: _name.text.trim(),
            isSandbox: _sandbox,
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
    return AlertDialog(
      title: Text(_editing ? '编辑机器人' : '添加机器人'),
      content: SizedBox(
        width: 440,
        child: Form(
          key: _form,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextFormField(
                controller: _appId,
                enabled: !_editing,
                decoration: const InputDecoration(labelText: 'AppID'),
                validator: (v) => (v == null || v.trim().isEmpty) ? '请填写 AppID' : null,
              ),
              TextFormField(
                controller: _secret,
                obscureText: true,
                decoration: InputDecoration(
                  labelText: 'AppSecret',
                  helperText: _editing ? '留空表示不修改（当前 ${widget.account!.secretHint ?? '未设置'}）' : '只存系统密钥库，不写数据库和日志',
                ),
                validator: (v) => (!_editing && (v == null || v.trim().isEmpty)) ? '请填写 AppSecret' : null,
              ),
              TextFormField(
                controller: _name,
                decoration: const InputDecoration(labelText: '名称（可选）', helperText: '只在本机显示'),
              ),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                value: _sandbox,
                onChanged: (v) => setState(() => _sandbox = v ?? true),
                title: const Text('沙箱环境'),
                subtitle: const Text('群聊和单聊权限审核通过前，只能在沙箱群和沙箱用户里测试'),
              ),
              if (!_editing)
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  value: _autoConnect,
                  onChanged: (v) => setState(() => _autoConnect = v ?? false),
                  title: const Text('核心服务启动后自动连接'),
                ),
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
                ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(onPressed: _saving ? null : () => Navigator.pop(context), child: const Text('取消')),
        FilledButton(onPressed: _saving ? null : _save, child: Text(_editing ? '保存' : '添加')),
      ],
    );
  }
}

// ---------------- 模型配置 ----------------

class _ProfileCard extends StatefulWidget {
  const _ProfileCard({super.key, required this.profile});

  final ProfileInfo profile;

  @override
  State<_ProfileCard> createState() => _ProfileCardState();
}

class _ProfileCardState extends State<_ProfileCard> {
  late final _name = TextEditingController(text: widget.profile.name);
  late final _apiKey = TextEditingController();
  late final _maxTokens = TextEditingController(text: '${widget.profile.params['max_tokens'] ?? 1500}');
  late final _prompt = TextEditingController(text: widget.profile.systemPrompt);
  late String _model = widget.profile.model;
  late String _effort = (widget.profile.params['effort'] as String?) ?? 'low';
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

  Future<void> _save() async {
    final maxTokens = int.tryParse(_maxTokens.text.trim());
    if (maxTokens == null || maxTokens < 64) {
      setState(() {
        _message = '最大回复长度要是不小于 64 的整数';
        _messageIsError = true;
      });
      return;
    }
    setState(() => _saving = true);
    final error = await Get.find<ConfigController>().updateProfile(widget.profile.id, {
      'name': _name.text.trim().isEmpty ? widget.profile.name : _name.text.trim(),
      'model': _model,
      'system_prompt': _prompt.text,
      'params': {...widget.profile.params, 'effort': _effort, 'max_tokens': maxTokens},
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
    final theme = Theme.of(context);
    final p = widget.profile;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text('模型配置', style: theme.textTheme.titleMedium),
                const SizedBox(width: 8),
                if (p.isDefault) const Chip(label: Text('默认'), visualDensity: VisualDensity.compact),
                const Spacer(),
                if (!p.apiKeySet)
                  Text('还没有填写 API Key，机器人无法回复', style: TextStyle(color: theme.colorScheme.error)),
              ],
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 16,
              runSpacing: 8,
              children: [
                SizedBox(width: 200, child: TextField(controller: _name, decoration: const InputDecoration(labelText: '名称'))),
                SizedBox(
                  width: 340,
                  child: DropdownButtonFormField<String>(
                    initialValue: _models.containsKey(_model) ? _model : null,
                    decoration: const InputDecoration(labelText: '模型'),
                    items: [for (final e in _models.entries) DropdownMenuItem(value: e.key, child: Text(e.value))],
                    onChanged: (v) => setState(() => _model = v ?? _model),
                  ),
                ),
                SizedBox(
                  width: 160,
                  child: DropdownButtonFormField<String>(
                    initialValue: _efforts.containsKey(_effort) ? _effort : 'low',
                    decoration: const InputDecoration(labelText: '思考力度（effort）'),
                    items: [for (final e in _efforts.entries) DropdownMenuItem(value: e.key, child: Text(e.value))],
                    onChanged: (v) => setState(() => _effort = v ?? _effort),
                  ),
                ),
                SizedBox(
                  width: 160,
                  child: TextField(
                    controller: _maxTokens,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(labelText: '最大回复长度（token）'),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            TextField(
              controller: _apiKey,
              obscureText: true,
              decoration: InputDecoration(
                labelText: 'Claude API Key',
                helperText: p.apiKeySet
                    ? '当前 ${p.apiKeyHint}；留空表示不修改。只存系统密钥库'
                    : '建议为本应用单独创建一个 Key，并在 Claude Console 给它所在的工作区设花费上限',
              ),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: _prompt,
              minLines: 2,
              maxLines: 6,
              decoration: const InputDecoration(labelText: '系统提示词', helperText: '保持固定有利于提示缓存，能省钱'),
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                FilledButton(onPressed: _saving ? null : _save, child: const Text('保存')),
                const SizedBox(width: 12),
                if (_message != null)
                  Text(_message!, style: TextStyle(color: _messageIsError ? theme.colorScheme.error : Colors.green)),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

// ---------------- 机器人行为 ----------------

class _BehaviorCard extends GetView<ConfigController> {
  const _BehaviorCard();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 20, 20, 8),
        child: Obx(() {
          final s = controller.settings;
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('机器人行为（全局）', style: theme.textTheme.titleMedium),
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('群聊里哪些消息触发回复'),
                subtitle: const Text('平台推送哪些群消息，由 QQ 客户端里的群设置决定；群里设成「仅 @」时，选「全部消息」也没有效果'),
                trailing: DropdownButton<String>(
                  value: (s['group.trigger'] as String?) ?? 'at_only',
                  items: const [
                    DropdownMenuItem(value: 'at_only', child: Text('只回复 @机器人 的消息')),
                    DropdownMenuItem(value: 'all', child: Text('未 @ 的消息也回复')),
                  ],
                  onChanged: (v) => _report(controller.setSetting('group.trigger', v)),
                ),
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('没触发回复的群消息也作为上下文'),
                subtitle: const Text('机器人回复时能看到期间群里聊了什么'),
                value: (s['group.keep_untriggered'] as bool?) ?? true,
                onChanged: (v) => _report(controller.setSetting('group.keep_untriggered', v)),
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('回复单聊消息'),
                value: (s['c2c.enabled'] as bool?) ?? true,
                onChanged: (v) => _report(controller.setSetting('c2c.enabled', v)),
              ),
            ],
          );
        }),
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
