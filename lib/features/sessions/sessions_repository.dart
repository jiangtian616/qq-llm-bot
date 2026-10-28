import 'package:get/get.dart';

import '../../core/api/core_api_client.dart';
import '../../core/api/models.dart';
import '../../core/connection/connection_store.dart';

/// 会话页的数据来源：会话列表、聊天记录、群状态，都走本机控制接口。
class SessionsRepository {
  SessionsRepository({ConnectionStore? store}) : store = store ?? Get.find<ConnectionStore>();

  final ConnectionStore store;

  CoreApiClient get _api {
    final api = store.api;
    if (api == null || store.status.value != CoreStatus.running) throw CoreApiException('核心服务未运行');
    return api;
  }

  Rx<CoreStatus> get coreStatus => store.status;
  RxList<CoreEvent> get events => store.events;

  Future<List<ConversationInfo>> conversations() => _api.listConversations();
  Future<List<ChatMessage>> messages(int id) => _api.conversationMessages(id, limit: 200);
  Future<ConversationInfo> rename(int id, String name) => _api.renameConversation(id, name);
  Future<ConversationInfo> clear(int id) => _api.clearConversation(id);
  Future<ConversationInfo> refreshBotState(int id, {required bool force}) => _api.refreshBotState(id, force: force);
}
