import 'package:get/get.dart';

import '../../core/api/models.dart';
import '../../core/connection/connection_store.dart';
import 'sessions_repository.dart';

class SessionsController extends GetxController {
  SessionsController(this.repository);

  final SessionsRepository repository;

  final conversations = <ConversationInfo>[].obs;
  final selectedId = RxnInt();
  final messages = <ChatMessage>[].obs;
  final loadingMessages = false.obs;
  final refreshingBotState = false.obs;
  final error = RxnString();

  bool get coreRunning => repository.coreStatus.value == CoreStatus.running;

  ConversationInfo? get selected => conversations.firstWhereOrNull((c) => c.id == selectedId.value);

  static const _refreshTopics = {'message.in', 'turn.done', 'turn.failed', 'outbox.sent'};

  @override
  void onInit() {
    super.onInit();
    ever(repository.coreStatus, (status) {
      if (status == CoreStatus.running) loadConversations();
    });
    // 有新消息或新回复时刷新列表和当前会话。
    debounce(repository.events, (_) {
      if (repository.events.isNotEmpty && _refreshTopics.contains(repository.events.first.topic)) {
        loadConversations(quiet: true);
        if (selectedId.value != null) loadMessages(quiet: true);
      }
    }, time: const Duration(milliseconds: 300));
    if (coreRunning) loadConversations();
  }

  Future<void> loadConversations({bool quiet = false}) async {
    try {
      conversations.assignAll(await repository.conversations());
      error.value = null;
      if (selectedId.value == null && conversations.isNotEmpty && !quiet) await select(conversations.first.id);
    } on Object catch (e) {
      error.value = e.toString();
    }
  }

  Future<void> select(int id) async {
    selectedId.value = id;
    messages.clear();
    await loadMessages();
    final conv = selected;
    // 打开群会话时自动查一次消息接收设置（核心服务那边 10 分钟内查过就不再查；没有接口权限时不自动查）。
    if (conv != null && conv.isGroup && !conv.botState.apiUnavailable) {
      await refreshBotState(force: false);
    }
  }

  Future<void> loadMessages({bool quiet = false}) async {
    final id = selectedId.value;
    if (id == null) return;
    if (!quiet) loadingMessages.value = true;
    try {
      final list = await repository.messages(id);
      if (selectedId.value == id) messages.assignAll(list);
    } on Object catch (e) {
      error.value = e.toString();
    } finally {
      loadingMessages.value = false;
    }
  }

  Future<String?> rename(String name) => _update(() => repository.rename(selectedId.value!, name));

  Future<String?> clearHistory() async {
    final error = await _update(() => repository.clear(selectedId.value!));
    if (error == null) await loadMessages(quiet: true);
    return error;
  }

  Future<String?> refreshBotState({bool force = true}) async {
    refreshingBotState.value = true;
    try {
      return await _update(() => repository.refreshBotState(selectedId.value!, force: force));
    } finally {
      refreshingBotState.value = false;
    }
  }

  Future<String?> _update(Future<ConversationInfo> Function() action) async {
    try {
      final updated = await action();
      final i = conversations.indexWhere((c) => c.id == updated.id);
      if (i >= 0) conversations[i] = updated;
      return null;
    } on Object catch (e) {
      return e.toString();
    }
  }
}
