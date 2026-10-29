/// 控制接口的数据结构。以后改为从 FastAPI 的 OpenAPI 描述生成。
class HealthInfo {
  const HealthInfo({
    required this.status,
    required this.version,
    required this.apiVersion,
    required this.pid,
    required this.uptimeSeconds,
    required this.dataDir,
    required this.dbRevision,
    required this.secretBackend,
    required this.plugins,
    required this.shuttingDown,
  });

  final String status;
  final String version;
  final int apiVersion;
  final int pid;
  final double uptimeSeconds;
  final String dataDir;
  final String? dbRevision;
  final String secretBackend;
  final Map<String, String> plugins;
  final bool shuttingDown;

  factory HealthInfo.fromJson(Map<String, dynamic> json) => HealthInfo(
        status: json['status'] as String,
        version: json['version'] as String,
        apiVersion: json['api_version'] as int,
        pid: json['pid'] as int,
        uptimeSeconds: (json['uptime_s'] as num).toDouble(),
        dataDir: json['data_dir'] as String,
        dbRevision: json['db_revision'] as String?,
        secretBackend: json['secret_backend'] as String,
        plugins: (json['plugins'] as Map<String, dynamic>).map((k, v) => MapEntry(k, v as String)),
        shuttingDown: json['shutting_down'] as bool? ?? false,
      );
}

/// QQ 连接状态：stopped / connecting / online / reconnecting / error。
class ConnectionInfo {
  const ConnectionInfo({required this.state, this.lastError, this.lastReadyAt, this.lastEventAt});

  final String state;
  final String? lastError;
  final DateTime? lastReadyAt;
  final DateTime? lastEventAt;

  bool get isActive => state == 'connecting' || state == 'online' || state == 'reconnecting';

  factory ConnectionInfo.fromJson(Map<String, dynamic> json) => ConnectionInfo(
        state: json['state'] as String,
        lastError: json['last_error'] as String?,
        lastReadyAt: _time(json['last_ready_at']),
        lastEventAt: _time(json['last_event_at']),
      );
}

class AccountInfo {
  const AccountInfo({
    required this.id,
    required this.appId,
    required this.displayName,
    required this.enabled,
    required this.autoConnect,
    required this.secretSet,
    required this.secretHint,
    required this.connection,
  });

  final int id;
  final String appId;
  final String displayName;
  final bool enabled;
  final bool autoConnect;
  final bool secretSet;
  final String? secretHint;
  final ConnectionInfo connection;

  factory AccountInfo.fromJson(Map<String, dynamic> json) => AccountInfo(
        id: json['id'] as int,
        appId: json['app_id'] as String,
        displayName: json['display_name'] as String,
        enabled: json['enabled'] as bool,
        autoConnect: json['auto_connect'] as bool,
        secretSet: json['secret_set'] as bool,
        secretHint: json['secret_hint'] as String?,
        connection: ConnectionInfo.fromJson(json['connection'] as Map<String, dynamic>),
      );
}

class ProfileInfo {
  const ProfileInfo({
    required this.id,
    required this.name,
    required this.provider,
    required this.model,
    required this.systemPrompt,
    required this.params,
    required this.apiKeySet,
    required this.apiKeyHint,
    required this.isDefault,
  });

  final int id;
  final String name;
  final String provider;
  final String model;
  final String systemPrompt;
  final Map<String, dynamic> params;
  final bool apiKeySet;
  final String? apiKeyHint;
  final bool isDefault;

  factory ProfileInfo.fromJson(Map<String, dynamic> json) => ProfileInfo(
        id: json['id'] as int,
        name: json['name'] as String,
        provider: json['provider'] as String,
        model: json['model'] as String,
        systemPrompt: json['system_prompt'] as String,
        params: json['params'] as Map<String, dynamic>,
        apiKeySet: json['api_key_set'] as bool,
        apiKeyHint: json['api_key_hint'] as String?,
        isDefault: json['is_default'] as bool,
      );
}

/// 提供商的一个模型（GET /providers）。
class ModelSpec {
  const ModelSpec({
    required this.id,
    required this.label,
    required this.efforts,
    required this.defaultEffort,
    required this.maxOutputTokens,
    required this.inputPrice,
    required this.outputPrice,
    required this.legacy,
  });

  final String id;
  final String label;

  /// 支持的 effort 级别，从低到高；空表示这个模型不接受 effort。
  final List<String> efforts;
  final String? defaultEffort;
  final int maxOutputTokens;

  /// 美元 / 百万 token。
  final double inputPrice;
  final double outputPrice;

  /// 上一代模型。
  final bool legacy;

  factory ModelSpec.fromJson(Map<String, dynamic> json) => ModelSpec(
        id: json['id'] as String,
        label: json['label'] as String,
        efforts: [for (final e in json['efforts'] as List<dynamic>) e as String],
        defaultEffort: json['default_effort'] as String?,
        maxOutputTokens: json['max_output_tokens'] as int,
        inputPrice: (json['input_price'] as num).toDouble(),
        outputPrice: (json['output_price'] as num).toDouble(),
        legacy: json['legacy'] as bool,
      );
}

/// 模型提供商的说明：模型目录、API Key 的叫法、max_tokens 上限。
class ProviderSpec {
  const ProviderSpec({
    required this.name,
    required this.displayName,
    required this.apiKeyLabel,
    required this.apiKeyHelp,
    required this.models,
    required this.defaultModel,
    required this.maxTokensLimit,
  });

  final String name;
  final String displayName;
  final String apiKeyLabel;
  final String apiKeyHelp;
  final List<ModelSpec> models;
  final String defaultModel;
  final int maxTokensLimit;

  ModelSpec? model(String id) => models.where((m) => m.id == id).firstOrNull;

  factory ProviderSpec.fromJson(Map<String, dynamic> json) => ProviderSpec(
        name: json['name'] as String,
        displayName: json['display_name'] as String,
        apiKeyLabel: json['api_key_label'] as String,
        apiKeyHelp: json['api_key_help'] as String,
        models: [for (final m in json['models'] as List<dynamic>) ModelSpec.fromJson(m as Map<String, dynamic>)],
        defaultModel: json['default_model'] as String,
        maxTokensLimit: json['max_tokens_limit'] as int,
      );
}

/// 机器人在群里的状态（只读，消息接收设置只能在 QQ 客户端里改）。
class BotState {
  const BotState({
    this.recvMsgSetting,
    this.memberRole,
    this.allowProactiveMsg,
    this.joinedAt,
    this.checkedAt,
    this.error,
    required this.apiUnavailable,
  });

  /// all / only_mention / mention_and_context
  final String? recvMsgSetting;
  final String? memberRole;
  final bool? allowProactiveMsg;
  final String? joinedAt;
  final DateTime? checkedAt;
  final String? error;
  final bool apiUnavailable;

  factory BotState.fromJson(Map<String, dynamic> json) => BotState(
        recvMsgSetting: json['recv_msg_setting'] as String?,
        memberRole: json['member_role'] as String?,
        allowProactiveMsg: json['allow_proactive_msg'] as bool?,
        joinedAt: json['joined_at'] as String?,
        checkedAt: _time(json['checked_at']),
        error: json['error'] as String?,
        apiUnavailable: json['api_unavailable'] as bool? ?? false,
      );
}

class ConversationInfo {
  const ConversationInfo({
    required this.id,
    required this.accountId,
    required this.scene,
    required this.targetOpenid,
    required this.displayName,
    required this.label,
    required this.historyEpoch,
    required this.lastMessageAt,
    required this.messageCount,
    required this.receivedUnmentioned,
    required this.botState,
  });

  final int id;
  final int accountId;
  final String scene;
  final String targetOpenid;
  final String? displayName;
  final String label;
  final int historyEpoch;
  final DateTime? lastMessageAt;
  final int messageCount;

  /// 收到过未 @ 的群消息，说明群里开了「接收全部消息」。
  final bool receivedUnmentioned;
  final BotState botState;

  bool get isGroup => scene == 'group';

  factory ConversationInfo.fromJson(Map<String, dynamic> json) => ConversationInfo(
        id: json['id'] as int,
        accountId: json['account_id'] as int,
        scene: json['scene'] as String,
        targetOpenid: json['target_openid'] as String,
        displayName: json['display_name'] as String?,
        label: json['label'] as String,
        historyEpoch: json['history_epoch'] as int,
        lastMessageAt: _time(json['last_message_at']),
        messageCount: json['message_count'] as int,
        receivedUnmentioned: json['received_unmentioned'] as bool,
        botState: BotState.fromJson(json['bot_state'] as Map<String, dynamic>),
      );
}

class TurnSummary {
  const TurnSummary({
    required this.status,
    required this.model,
    required this.inputTokens,
    required this.outputTokens,
    required this.cacheReadTokens,
    required this.costUsd,
    this.latencyMs,
  });

  final String status;
  final String model;
  final int inputTokens;
  final int outputTokens;
  final int cacheReadTokens;
  final double costUsd;
  final int? latencyMs;

  factory TurnSummary.fromJson(Map<String, dynamic> json) => TurnSummary(
        status: json['status'] as String,
        model: json['model'] as String,
        inputTokens: json['input_tokens'] as int,
        outputTokens: json['output_tokens'] as int,
        cacheReadTokens: json['cache_read_tokens'] as int,
        costUsd: (json['cost_usd'] as num).toDouble(),
        latencyMs: json['latency_ms'] as int?,
      );
}

class ChatMessage {
  const ChatMessage({
    required this.id,
    required this.isOutgoing,
    required this.historyEpoch,
    required this.content,
    required this.attachmentCount,
    required this.isMention,
    required this.triggered,
    required this.createdAt,
    this.senderLabel,
    this.turn,
  });

  final int id;
  final bool isOutgoing;
  final int historyEpoch;
  final String content;
  final int attachmentCount;
  final bool isMention;
  final bool triggered;
  final DateTime createdAt;
  final String? senderLabel;
  final TurnSummary? turn;

  factory ChatMessage.fromJson(Map<String, dynamic> json) => ChatMessage(
        id: json['id'] as int,
        isOutgoing: json['direction'] == 'out',
        historyEpoch: json['history_epoch'] as int,
        content: json['content'] as String,
        attachmentCount: (json['attachments'] as List<dynamic>).length,
        isMention: json['is_mention'] as bool,
        triggered: json['triggered'] as bool,
        createdAt: _time(json['created_at'])!,
        senderLabel: (json['sender'] as Map<String, dynamic>?)?['label'] as String?,
        turn: json['turn'] == null ? null : TurnSummary.fromJson(json['turn'] as Map<String, dynamic>),
      );
}

class InboxItem {
  const InboxItem({
    required this.id,
    required this.eventType,
    required this.status,
    required this.receivedAt,
    required this.attempts,
    required this.preview,
    this.replyDeadline,
    this.lastError,
  });

  final int id;
  final String eventType;
  final String status;
  final DateTime receivedAt;
  final DateTime? replyDeadline;
  final int attempts;
  final String? lastError;
  final String preview;

  factory InboxItem.fromJson(Map<String, dynamic> json) => InboxItem(
        id: json['id'] as int,
        eventType: json['event_type'] as String,
        status: json['status'] as String,
        receivedAt: _time(json['received_at'])!,
        replyDeadline: _time(json['reply_deadline']),
        attempts: json['attempts'] as int,
        lastError: json['last_error'] as String?,
        preview: json['preview'] as String,
      );
}

class OutboxItem {
  const OutboxItem({
    required this.id,
    required this.conversationLabel,
    required this.msgSeq,
    required this.chunkIndex,
    required this.chunkCount,
    required this.status,
    required this.attempts,
    required this.createdAt,
    required this.preview,
    this.deadline,
    this.nextAttemptAt,
    this.lastErrorCode,
    this.lastError,
    this.sentAt,
  });

  final int id;
  final String conversationLabel;
  final int msgSeq;
  final int chunkIndex;
  final int chunkCount;
  final String status;
  final int attempts;
  final DateTime createdAt;
  final String preview;
  final DateTime? deadline;
  final DateTime? nextAttemptAt;
  final int? lastErrorCode;
  final String? lastError;
  final DateTime? sentAt;

  factory OutboxItem.fromJson(Map<String, dynamic> json) => OutboxItem(
        id: json['id'] as int,
        conversationLabel: json['conversation_label'] as String,
        msgSeq: json['msg_seq'] as int,
        chunkIndex: json['chunk_index'] as int,
        chunkCount: json['chunk_count'] as int,
        status: json['status'] as String,
        attempts: json['attempts'] as int,
        createdAt: _time(json['created_at'])!,
        preview: json['preview'] as String,
        deadline: _time(json['deadline']),
        nextAttemptAt: _time(json['next_attempt_at']),
        lastErrorCode: json['last_error_code'] as int?,
        lastError: json['last_error'] as String?,
        sentAt: _time(json['sent_at']),
      );
}

class SystemEventItem {
  const SystemEventItem({
    required this.id,
    required this.ts,
    required this.level,
    required this.category,
    required this.message,
    this.accountId,
    this.data,
  });

  final int id;
  final DateTime ts;
  final String level;
  final String category;
  final String message;
  final int? accountId;
  final Map<String, dynamic>? data;

  factory SystemEventItem.fromJson(Map<String, dynamic> json) => SystemEventItem(
        id: json['id'] as int,
        ts: _time(json['ts'])!,
        level: json['level'] as String,
        category: json['category'] as String,
        message: json['message'] as String,
        accountId: json['account_id'] as int?,
        data: json['data'] as Map<String, dynamic>?,
      );
}

/// 今天（本地日期）的用量，以及全局每日预算。
class UsageToday {
  const UsageToday({
    required this.turns,
    required this.failed,
    required this.refused,
    required this.inputTokens,
    required this.outputTokens,
    required this.cacheReadTokens,
    required this.costUsd,
    this.budgetUsd,
  });

  final int turns;
  final int failed;
  final int refused;
  final int inputTokens;
  final int outputTokens;
  final int cacheReadTokens;
  final double costUsd;
  final double? budgetUsd;

  factory UsageToday.fromJson(Map<String, dynamic> json) => UsageToday(
        turns: json['turns'] as int,
        failed: json['failed'] as int,
        refused: json['refused'] as int,
        inputTokens: json['input_tokens'] as int,
        outputTokens: json['output_tokens'] as int,
        cacheReadTokens: json['cache_read_tokens'] as int,
        costUsd: (json['cost_usd'] as num).toDouble(),
        budgetUsd: (json['budget_usd'] as num?)?.toDouble(),
      );
}

DateTime? _time(Object? ms) => ms is int ? DateTime.fromMillisecondsSinceEpoch(ms) : null;

class CoreEvent {
  const CoreEvent({required this.topic, required this.ts, required this.data});

  final String topic;
  final DateTime ts;
  final Map<String, dynamic> data;

  factory CoreEvent.fromJson(Map<String, dynamic> json) => CoreEvent(
        topic: json['topic'] as String,
        ts: DateTime.fromMillisecondsSinceEpoch(json['ts'] as int),
        data: (json['data'] as Map<String, dynamic>?) ?? const {},
      );
}
