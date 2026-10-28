import 'dart:convert';
import 'dart:io';

/// 核心服务启动时写在数据目录里的 control.json：端口、随机令牌、进程号、接口版本。
class ControlEndpoint {
  const ControlEndpoint({
    required this.pid,
    required this.port,
    required this.token,
    required this.version,
    required this.apiVersion,
  });

  final int pid;
  final int port;
  final String token;
  final String version;
  final int apiVersion;

  Uri get baseUri => Uri.parse('http://127.0.0.1:$port');
  Uri get eventsUri => Uri.parse('ws://127.0.0.1:$port/events');

  static Future<ControlEndpoint?> read(String path) async {
    final file = File(path);
    if (!await file.exists()) return null;
    try {
      final json = jsonDecode(await file.readAsString()) as Map<String, dynamic>;
      return ControlEndpoint(
        pid: json['pid'] as int,
        port: json['port'] as int,
        token: json['token'] as String,
        version: json['version'] as String,
        apiVersion: json['api_version'] as int,
      );
    } on Object {
      // 核心服务正在写这个文件，下一轮再读。
      return null;
    }
  }
}
