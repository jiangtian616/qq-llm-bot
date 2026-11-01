import 'package:dio/dio.dart';

import '../connection/control_endpoint.dart';
import 'models.dart';

/// 桌面端与核心服务的接口版本。核心服务返回的 api_version 不一致时提示同时升级两端。
const supportedApiVersion = 1;

/// 控制接口返回的错误，message 是核心服务给的说明（detail）。
class CoreApiException implements Exception {
  CoreApiException(this.message, {this.statusCode});
  final String message;
  final int? statusCode;
  @override
  String toString() => message;
}

/// 本机控制接口的客户端。以后用 openapi-generator 从 FastAPI 的接口描述生成，替换这个手写版本。
class CoreApiClient {
  CoreApiClient(this.endpoint)
      : _dio = Dio(BaseOptions(
          baseUrl: endpoint.baseUri.toString(),
          headers: {'Authorization': 'Bearer ${endpoint.token}'},
          connectTimeout: const Duration(seconds: 2),
          receiveTimeout: const Duration(seconds: 15),
        ));

  final ControlEndpoint endpoint;
  final Dio _dio;

  Future<HealthInfo> health() async => HealthInfo.fromJson(await _get('/health') as Map<String, dynamic>);

  Future<Map<String, dynamic>> version() async => await _get('/version') as Map<String, dynamic>;

  /// 平稳停止：核心服务发完在途回复、保存 seq 后自己退出。
  Future<void> shutdown() => _send('POST', '/shutdown');

  // ---- 机器人账号与连接 ----

  Future<List<AccountInfo>> listAccounts() async =>
      [for (final a in await _get('/accounts') as List<dynamic>) AccountInfo.fromJson(a as Map<String, dynamic>)];

  Future<AccountInfo> createAccount({
    required String appId,
    required String appSecret,
    String displayName = '',
    bool autoConnect = false,
  }) async =>
      AccountInfo.fromJson(await _send('POST', '/accounts', {
        'app_id': appId,
        'app_secret': appSecret,
        'display_name': displayName,
        'auto_connect': autoConnect,
      }) as Map<String, dynamic>);

  Future<AccountInfo> updateAccount(int id, Map<String, dynamic> fields) async =>
      AccountInfo.fromJson(await _send('PATCH', '/accounts/$id', fields) as Map<String, dynamic>);

  Future<void> deleteAccount(int id) => _send('DELETE', '/accounts/$id');

  Future<void> startConnection(int id) => _send('POST', '/accounts/$id/connection/start');

  Future<void> stopConnection(int id) => _send('POST', '/accounts/$id/connection/stop');

  // ---- 模型配置 ----

  Future<List<ProviderSpec>> listProviders() async =>
      [for (final p in await _get('/providers') as List<dynamic>) ProviderSpec.fromJson(p as Map<String, dynamic>)];

  Future<List<ProfileInfo>> listProfiles() async =>
      [for (final p in await _get('/profiles') as List<dynamic>) ProfileInfo.fromJson(p as Map<String, dynamic>)];

  Future<ProfileInfo> updateProfile(int id, Map<String, dynamic> fields) async =>
      ProfileInfo.fromJson(await _send('PATCH', '/profiles/$id', fields) as Map<String, dynamic>);

  // ---- 分层配置 ----

  /// 返回这一层显式设置过的值和所有配置项的默认值。
  Future<({Map<String, dynamic> values, Map<String, dynamic> defaults})> getSettings(
      {String scope = 'global', int scopeId = 0}) async {
    final json = await _get('/settings', {'scope': scope, 'scope_id': scopeId}) as Map<String, dynamic>;
    return (values: json['values'] as Map<String, dynamic>, defaults: json['defaults'] as Map<String, dynamic>);
  }

  Future<void> putSetting(String key, Object? value, {String scope = 'global', int scopeId = 0}) =>
      _send('PUT', '/settings', {'scope': scope, 'scope_id': scopeId, 'key': key, 'value': value});

  // ---- 网络代理 ----

  /// 用给定的代理设置（可以还没保存）试连 Claude、DeepSeek 和 QQ；systemProxy 是读到的系统代理，没有时为 null。
  Future<({List<ProxyTestResult> results, String? systemProxy})> testProxy({required String mode, String url = ''}) async {
    final json = await _send('POST', '/network/test', {'mode': mode, 'url': url}) as Map<String, dynamic>;
    return (
      results: [
        for (final r in json['results'] as List<dynamic>) ProxyTestResult.fromJson(r as Map<String, dynamic>),
      ],
      systemProxy: json['system_proxy'] as String?,
    );
  }

  // ---- 会话 ----

  Future<List<ConversationInfo>> listConversations({int? accountId}) async => [
        for (final c in await _get('/conversations', {'account_id': ?accountId}) as List<dynamic>)
          ConversationInfo.fromJson(c as Map<String, dynamic>),
      ];

  Future<List<ChatMessage>> conversationMessages(int id, {int limit = 100, int? beforeId}) async => [
        for (final m in await _get('/conversations/$id/messages', {'limit': limit, 'before_id': ?beforeId})
            as List<dynamic>)
          ChatMessage.fromJson(m as Map<String, dynamic>),
      ];

  Future<ConversationInfo> renameConversation(int id, String displayName) async => ConversationInfo.fromJson(
      await _send('PATCH', '/conversations/$id', {'display_name': displayName}) as Map<String, dynamic>);

  Future<ConversationInfo> clearConversation(int id) async =>
      ConversationInfo.fromJson(await _send('POST', '/conversations/$id/clear') as Map<String, dynamic>);

  /// 查询群里的消息接收设置；force 为 false 时，10 分钟内查过就不再查。
  Future<ConversationInfo> refreshBotState(int id, {bool force = true}) async => ConversationInfo.fromJson(
      await _send('POST', '/conversations/$id/bot-state/refresh?force=$force') as Map<String, dynamic>);

  // ---- 消息队列 ----

  Future<List<InboxItem>> listInbox({String? status}) async => [
        for (final e in await _get('/queue/inbox', {'status': ?status}) as List<dynamic>)
          InboxItem.fromJson(e as Map<String, dynamic>),
      ];

  Future<List<OutboxItem>> listOutbox({String? status}) async => [
        for (final o in await _get('/queue/outbox', {'status': ?status}) as List<dynamic>)
          OutboxItem.fromJson(o as Map<String, dynamic>),
      ];

  Future<QueueDetail> inboxDetail(int id) async =>
      QueueDetail.fromInboxJson(await _get('/queue/inbox/$id') as Map<String, dynamic>);

  Future<QueueDetail> outboxDetail(int id) async =>
      QueueDetail.fromOutboxJson(await _get('/queue/outbox/$id') as Map<String, dynamic>);

  Future<void> retryInbox(int id) => _send('POST', '/queue/inbox/$id/retry');

  Future<void> retryOutbox(int id) => _send('POST', '/queue/outbox/$id/retry');

  Future<void> cancelOutbox(int id) => _send('POST', '/queue/outbox/$id/cancel');

  /// 收件箱、发件箱各状态的数量。
  Future<({Map<String, int> inbox, Map<String, int> outbox})> stats() async {
    final json = await _get('/stats') as Map<String, dynamic>;
    Map<String, int> counts(Object? m) => (m as Map<String, dynamic>).map((k, v) => MapEntry(k, v as int));
    return (inbox: counts(json['inbox']), outbox: counts(json['outbox']));
  }

  // ---- 问答过程 ----

  Future<List<TurnItem>> listTurns({String? status}) async => [
        for (final t in await _get('/turns', {'status': ?status}) as List<dynamic>)
          TurnItem.fromJson(t as Map<String, dynamic>),
      ];

  /// 各状态的问答数量。
  Future<Map<String, int>> turnCounts() async =>
      (await _get('/turns/counts') as Map<String, dynamic>).map((k, v) => MapEntry(k, v as int));

  Future<TurnDetail> turnDetail(int id) async => TurnDetail.fromJson(await _get('/turns/$id') as Map<String, dynamic>);

  // ---- 用量与维护 ----

  Future<UsageToday> usageToday() async => UsageToday.fromJson(await _get('/usage/today') as Map<String, dynamic>);

  /// 立即执行一次每日维护：清理超期数据、备份数据库。返回删除的行数和备份文件。
  Future<({int deleted, String? backup})> runMaintenance() async {
    final json = await _send('POST', '/maintenance/run') as Map<String, dynamic>;
    var deleted = 0;
    for (final a in json['accounts'] as List<dynamic>) {
      (a as Map<String, dynamic>).forEach((k, v) {
        if (k != 'account_id' && k != 'epoch_bumped') deleted += v as int;
      });
    }
    return (deleted: deleted, backup: json['backup'] as String?);
  }

  // ---- 日志 ----

  Future<List<SystemEventItem>> systemEvents({int limit = 200, String? level}) async => [
        for (final e in await _get('/system-events', {'limit': limit, 'level': ?level}) as List<dynamic>)
          SystemEventItem.fromJson(e as Map<String, dynamic>),
      ];

  Future<({String file, List<String> lines})> logTail({int lines = 500}) async {
    final json = await _get('/logs', {'lines': lines}) as Map<String, dynamic>;
    return (file: json['file'] as String, lines: [for (final l in json['lines'] as List<dynamic>) l as String]);
  }

  // ----

  Future<Object?> _get(String path, [Map<String, dynamic>? query]) async {
    try {
      return (await _dio.get<Object?>(path, queryParameters: query)).data;
    } on DioException catch (e) {
      throw _wrap(e);
    }
  }

  Future<Object?> _send(String method, String path, [Object? body]) async {
    try {
      return (await _dio.request<Object?>(path, data: body, options: Options(method: method))).data;
    } on DioException catch (e) {
      throw _wrap(e);
    }
  }

  static CoreApiException _wrap(DioException e) {
    final data = e.response?.data;
    final detail = data is Map ? data['detail'] : null;
    final message = switch (detail) {
      String s => s,
      List<dynamic> errors => errors.map((x) => x is Map ? '${x['loc']?.last}: ${x['msg']}' : '$x').join('；'),
      _ => e.message ?? e.type.name,
    };
    return CoreApiException(message, statusCode: e.response?.statusCode);
  }

  void close() => _dio.close(force: true);
}
