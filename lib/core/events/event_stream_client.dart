import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/io.dart';

import '../api/models.dart';
import '../connection/control_endpoint.dart';

/// 订阅核心服务的事件流（WebSocket /events）：连接状态、消息、日志等。
class EventStreamClient {
  EventStreamClient(this.endpoint);

  final ControlEndpoint endpoint;
  IOWebSocketChannel? _channel;

  Stream<CoreEvent> connect() {
    final channel = IOWebSocketChannel.connect(
      endpoint.eventsUri,
      headers: {'Authorization': 'Bearer ${endpoint.token}'},
    );
    _channel = channel;
    return channel.stream.map((raw) => CoreEvent.fromJson(jsonDecode(raw as String) as Map<String, dynamic>));
  }

  Future<void> close() async {
    await _channel?.sink.close();
    _channel = null;
  }
}
