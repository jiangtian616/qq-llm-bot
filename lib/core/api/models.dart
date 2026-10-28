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
