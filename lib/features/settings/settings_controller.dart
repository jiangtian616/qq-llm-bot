import 'dart:io';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/widgets.dart';
import 'package:get/get.dart';

import '../../core/api/models.dart';
import '../../core/connection/connection_store.dart';
import '../../platform/app_paths.dart';
import '../../platform/data_dir_mover.dart';
import '../../platform/desktop_prefs.dart';
import 'settings_repository.dart';

class SettingsController extends GetxController {
  SettingsController(this.repository);

  final SettingsRepository repository;

  final error = RxnString();

  RxBool get autostartEnabled => repository.autostartEnabled;
  Rx<CloseAction> get closeAction => repository.closeAction;
  bool get trayAvailable => repository.trayAvailable;

  // ---- 数据目录 ----

  AppPaths get paths => repository.paths;
  final dataDirBusy = false.obs;

  /// 数据目录卡片上的提示：(内容, 是否错误)。
  final dataDirNotice = Rxn<(String, bool)>();

  /// 选一个文件夹；取消或不能用时返回 null。
  Future<String?> pickDataDir() async {
    dataDirNotice.value = null;
    final dir = await getDirectoryPath(confirmButtonText: '选择');
    if (dir == null) return null;
    return checkDataDir(dir) ? dir : null;
  }

  bool checkDataDir(String dir) {
    final problem = DataDirMover.validate(paths.dataDir, dir);
    if (problem != null) dataDirNotice.value = ('不能用 $dir：$problem', true);
    return problem == null;
  }

  Future<void> changeDataDir(String target, DataDirAction action) async {
    dataDirBusy.value = true;
    dataDirNotice.value = null;
    try {
      await repository.changeDataDir(target, action);
    } on Object catch (e) {
      dataDirNotice.value = ('更换数据目录失败，仍在使用原来的目录：$e', true);
    } finally {
      dataDirBusy.value = false;
    }
  }

  Future<void> openDataDir() async {
    final command = Platform.isWindows ? 'explorer' : (Platform.isMacOS ? 'open' : 'xdg-open');
    try {
      // explorer 打开成功也返回退出码 1，不看退出码。
      await Process.run(command, [paths.dataDir]);
    } on Object catch (e) {
      dataDirNotice.value = ('打不开文件夹：$e', true);
    }
  }

  // ---- 网络代理（核心服务的 network.* 配置项，只有全局级） ----

  /// none 直连、system 跟随系统代理、custom 自定义。
  final proxyMode = 'system'.obs;
  final proxyUrl = TextEditingController();
  final proxyForProviders = true.obs;
  final proxyForQq = false.obs;
  final proxyLoaded = false.obs;
  final proxySaving = false.obs;
  final proxyTesting = false.obs;

  /// 代理卡片上的提示：(内容, 是否错误)。
  final proxyNotice = Rxn<(String, bool)>();
  final proxyTest = Rxn<({List<ProxyTestResult> results, String? systemProxy})>();

  bool get coreRunning => repository.coreStatus.value == CoreStatus.running;

  @override
  void onInit() {
    super.onInit();
    ever(repository.coreStatus, (status) {
      if (status == CoreStatus.running) loadProxy();
    });
    if (coreRunning) loadProxy();
  }

  @override
  void onClose() {
    proxyUrl.dispose();
    super.onClose();
  }

  Future<void> setAutostart(bool value) async {
    error.value = null;
    try {
      await repository.setAutostart(value);
    } on Object catch (e) {
      error.value = '设置开机自启失败：$e';
    }
  }

  Future<void> setCloseAction(CloseAction value) async {
    error.value = null;
    try {
      await repository.setCloseAction(value);
    } on Object catch (e) {
      error.value = '保存关闭窗口时的设置失败：$e';
    }
  }

  Future<void> loadProxy() async {
    try {
      final s = await repository.globalSettings();
      proxyMode.value = s['network.proxy_mode'] as String? ?? 'system';
      proxyUrl.text = s['network.proxy_url'] as String? ?? '';
      proxyForProviders.value = s['network.proxy_for_providers'] as bool? ?? true;
      proxyForQq.value = s['network.proxy_for_qq'] as bool? ?? false;
      proxyLoaded.value = true;
    } on Object catch (e) {
      proxyNotice.value = ('读取代理设置失败：$e', true);
    }
  }

  Future<void> saveProxy() async {
    final url = proxyUrl.text.trim();
    if (proxyMode.value == 'custom' && url.isEmpty) {
      proxyNotice.value = ('请填写代理地址，比如 http://127.0.0.1:7890', true);
      return;
    }
    proxySaving.value = true;
    proxyNotice.value = null;
    try {
      // 先存地址：地址不合法时核心服务会拒绝，方式就还停在原来的。
      await repository.putGlobalSetting('network.proxy_url', url);
      await repository.putGlobalSetting('network.proxy_mode', proxyMode.value);
      await repository.putGlobalSetting('network.proxy_for_providers', proxyForProviders.value);
      await repository.putGlobalSetting('network.proxy_for_qq', proxyForQq.value);
      await loadProxy();
      proxyNotice.value = ('已保存。模型接口下一次请求就用新的设置；QQ 的 WebSocket 连接要在仪表盘重新启动后才会用上。', false);
    } on Object catch (e) {
      proxyNotice.value = ('保存失败：$e', true);
    } finally {
      proxySaving.value = false;
    }
  }

  /// 用界面上的设置（不用先保存）试连各个接口。
  Future<void> testProxy() async {
    proxyTesting.value = true;
    proxyNotice.value = null;
    proxyTest.value = null;
    try {
      proxyTest.value = await repository.testProxy(mode: proxyMode.value, url: proxyUrl.text.trim());
    } on Object catch (e) {
      proxyNotice.value = ('测试失败：$e', true);
    } finally {
      proxyTesting.value = false;
    }
  }
}
