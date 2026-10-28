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
    bool isSandbox = true,
    bool autoConnect = false,
  }) async =>
      AccountInfo.fromJson(await _send('POST', '/accounts', {
        'app_id': appId,
        'app_secret': appSecret,
        'display_name': displayName,
        'is_sandbox': isSandbox,
        'auto_connect': autoConnect,
      }) as Map<String, dynamic>);

  Future<AccountInfo> updateAccount(int id, Map<String, dynamic> fields) async =>
      AccountInfo.fromJson(await _send('PATCH', '/accounts/$id', fields) as Map<String, dynamic>);

  Future<void> deleteAccount(int id) => _send('DELETE', '/accounts/$id');

  Future<void> startConnection(int id) => _send('POST', '/accounts/$id/connection/start');

  Future<void> stopConnection(int id) => _send('POST', '/accounts/$id/connection/stop');

  // ---- 模型配置 ----

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
