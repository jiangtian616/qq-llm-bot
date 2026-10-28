import 'package:dio/dio.dart';

import '../connection/control_endpoint.dart';
import 'models.dart';

/// 桌面端与核心服务的接口版本。核心服务返回的 api_version 不一致时提示同时升级两端。
const supportedApiVersion = 1;

/// 本机控制接口的客户端。以后用 openapi-generator 从 FastAPI 的接口描述生成，替换这个手写版本。
class CoreApiClient {
  CoreApiClient(this.endpoint)
      : _dio = Dio(BaseOptions(
          baseUrl: endpoint.baseUri.toString(),
          headers: {'Authorization': 'Bearer ${endpoint.token}'},
          connectTimeout: const Duration(seconds: 2),
          receiveTimeout: const Duration(seconds: 5),
        ));

  final ControlEndpoint endpoint;
  final Dio _dio;

  Future<HealthInfo> health() async {
    final res = await _dio.get<Map<String, dynamic>>('/health');
    return HealthInfo.fromJson(res.data!);
  }

  Future<Map<String, dynamic>> version() async {
    final res = await _dio.get<Map<String, dynamic>>('/version');
    return res.data!;
  }

  /// 平稳停止：核心服务发完在途回复、保存 seq 后自己退出。
  Future<void> shutdown() => _dio.post<void>('/shutdown');

  void close() => _dio.close(force: true);
}
