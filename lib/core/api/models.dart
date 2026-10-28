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
    required this.isSandbox,
    required this.enabled,
    required this.autoConnect,
    required this.secretSet,
    required this.secretHint,
    required this.connection,
  });

  final int id;
  final String appId;
  final String displayName;
  final bool isSandbox;
  final bool enabled;
  final bool autoConnect;
  final bool secretSet;
  final String? secretHint;
  final ConnectionInfo connection;

  factory AccountInfo.fromJson(Map<String, dynamic> json) => AccountInfo(
        id: json['id'] as int,
        appId: json['app_id'] as String,
        displayName: json['display_name'] as String,
        isSandbox: json['is_sandbox'] as bool,
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
