import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:get/get.dart';
import 'package:path/path.dart' as p;

import '../core/connection/connection_store.dart';
import '../core/connection/control_endpoint.dart';

/// 怎么拉起核心服务。
class CoreLaunchSpec {
  const CoreLaunchSpec(this.executable, this.arguments, {this.workingDirectory, required this.description});

  final String executable;
  final List<String> arguments;
  final String? workingDirectory;
  final String description;
}

class CoreLaunchException implements Exception {
  CoreLaunchException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// 负责拉起和停止核心服务进程 agent-core。
///
/// 核心服务是桌面端的子进程，跟着桌面端退出：启动时传 --parent-pid，桌面端没了（包括被强制结束）它会自己平稳停止。
/// 不用分离模式：Windows 上分离模式的进程没有控制台，uv / venv 的启动器再拉起 python 时会弹出一个控制台窗口。
/// 停止时先调 /shutdown 让它平稳退出，超时再结束进程。
class CoreProcessManager extends GetxService {
  CoreProcessManager({required this.dataDir, required this.store});

  final String dataDir;
  final ConnectionStore store;

  static const startTimeout = Duration(seconds: 60);
  static const stopTimeout = Duration(seconds: 15);
  static const _stderrTailLines = 20;

  bool get _isRunning => store.status.value == CoreStatus.running;

  Future<void> start() async {
    await store.refresh();
    if (_isRunning || store.status.value == CoreStatus.starting) return;
    final spec = resolveLaunchSpec();
    store.setTransition(CoreStatus.starting);
    try {
      final process = await Process.start(
        spec.executable,
        [...spec.arguments, '--data-dir', dataDir, '--parent-pid', '$pid'],
        workingDirectory: spec.workingDirectory,
        environment: const {'PYTHONIOENCODING': 'utf-8'},
      );
      // 核心服务的日志只写文件；stderr 里只会有启动器的报错、日志还没建好时的异常，留最后几行用来报错。
      final stderrTail = <String>[];
      process.stdout.drain<void>().ignore();
      process.stderr.transform(const Utf8Decoder(allowMalformed: true)).transform(const LineSplitter()).listen(
        (line) {
          if (kDebugMode) debugPrint('[agent-core] $line');
          stderrTail.add(line);
          if (stderrTail.length > _stderrTailLines) stderrTail.removeAt(0);
        },
        onError: (Object _) {},
      );
      int? exitCode;
      process.exitCode.then((code) => exitCode = code).ignore();

      final deadline = DateTime.now().add(startTimeout);
      while (DateTime.now().isBefore(deadline)) {
        await Future<void>.delayed(const Duration(milliseconds: 500));
        store.setTransition(CoreStatus.starting);
        await store.refresh();
        if (store.health.value != null) {
          store.setTransition(CoreStatus.running);
          return;
        }
        if (exitCode case final code?) {
          final detail = stderrTail.isEmpty ? '' : '：\n${stderrTail.join('\n')}';
          throw CoreLaunchException('核心服务启动后就退出了（退出码 $code，${spec.description}）$detail');
        }
      }
      throw CoreLaunchException('核心服务 ${startTimeout.inSeconds} 秒内没有就绪（${spec.description}），请查看 ${p.join(dataDir, 'logs')} 里的日志');
    } on ProcessException catch (e) {
      store.setTransition(CoreStatus.notRunning);
      throw CoreLaunchException('无法启动核心服务（${spec.description}）：${e.message}');
    } on Object {
      store.setTransition(CoreStatus.notRunning);
      rethrow;
    }
  }

  Future<void> stop() async {
    await store.refresh();
    final endpoint = store.endpoint;
    final api = store.api;
    if (endpoint == null || api == null) return;
    store.setTransition(CoreStatus.stopping);
    try {
      try {
        await api.shutdown();
      } on Object {
        // 接口不通就直接走强制结束。
      }
      if (!await _waitForExit(endpoint)) {
        _kill(endpoint);
      }
    } finally {
      store.setTransition(CoreStatus.notRunning);
      await store.refresh();
    }
  }

  /// 核心服务退出时会删掉 control.json。
  Future<bool> _waitForExit(ControlEndpoint endpoint) async {
    final deadline = DateTime.now().add(stopTimeout);
    while (DateTime.now().isBefore(deadline)) {
      await Future<void>.delayed(const Duration(milliseconds: 300));
      final current = await ControlEndpoint.read(store.controlFile);
      if (current == null || current.pid != endpoint.pid) return true;
    }
    return false;
  }

  void _kill(ControlEndpoint endpoint) {
    // Windows 上直接结束进程；macOS / Linux 先 SIGTERM，3 秒后还在就 SIGKILL。
    Process.killPid(endpoint.pid, ProcessSignal.sigterm);
    if (!Platform.isWindows) {
      Timer(const Duration(seconds: 3), () => Process.killPid(endpoint.pid, ProcessSignal.sigkill));
    }
    final file = File(store.controlFile);
    if (file.existsSync()) file.deleteSync();
  }

  /// 查找顺序：环境变量 QQ_LLM_BOT_CORE → 随安装包分发的 agent-core → 源码目录里的 agent-core（开发模式）。
  CoreLaunchSpec resolveLaunchSpec() {
    final exeName = Platform.isWindows ? 'agent-core.exe' : 'agent-core';

    final override = Platform.environment['QQ_LLM_BOT_CORE'];
    if (override != null && override.isNotEmpty) {
      return CoreLaunchSpec(override, const [], description: '环境变量 QQ_LLM_BOT_CORE');
    }

    final exeDir = p.dirname(Platform.resolvedExecutable);
    final bundled = Platform.isMacOS
        ? p.normalize(p.join(exeDir, '..', 'Helpers', 'agent-core', exeName))
        : p.join(exeDir, 'agent-core', exeName);
    if (File(bundled).existsSync()) {
      return CoreLaunchSpec(bundled, const [], description: bundled);
    }

    final project = _findSourceProject([Directory.current.path, exeDir]);
    if (project != null) {
      final venvExe = Platform.isWindows
          ? p.join(project, '.venv', 'Scripts', exeName)
          : p.join(project, '.venv', 'bin', exeName);
      if (File(venvExe).existsSync()) {
        _checkVenvBase(project);
        return CoreLaunchSpec(venvExe, const [], workingDirectory: project, description: venvExe);
      }
      return CoreLaunchSpec('uv', ['run', '--project', project, 'agent-core'],
          workingDirectory: project, description: 'uv run（$project）');
    }
    throw CoreLaunchException('找不到核心服务 agent-core：既没有随安装包分发的版本，也不在源码目录里运行');
  }

  /// 检查 .venv/pyvenv.cfg 的 home 下的基础解释器还在不在；读不出来时不拦着。
  ///
  /// 解释器不在时启动器会直接退出、什么日志都没有，只能干等超时；uv sync / uv run 只看版本号，也不会重建 venv。
  /// 常见原因：Python 被卸载或换了位置；或者 venv 是在 MSIX 打包的应用（比如 Claude 桌面版）里建的，
  /// uv 装的 Python 被重定向到了包的私有目录，包外的进程看不到。
  static void _checkVenvBase(String project) {
    final String cfg;
    try {
      cfg = File(p.join(project, '.venv', 'pyvenv.cfg')).readAsStringSync();
    } on FileSystemException {
      return;
    }
    String? field(String key) =>
        RegExp('^\\s*$key\\s*=\\s*(.+?)\\s*\$', multiLine: true).firstMatch(cfg)?.group(1);
    final home = field('home');
    if (home == null) return;
    final python = p.join(home, Platform.isWindows ? 'python.exe' : 'python3');
    if (File(python).existsSync()) return;
    final version = field('version_info') ?? '';
    throw CoreLaunchException('.venv 的基础解释器 $python 不存在，核心服务起不来。'
        '请在普通终端（不要在 Claude 桌面版这类 MSIX 应用里）到 agent-core 下运行 uv python install $version，'
        '或者删掉 .venv 后重新运行 uv sync');
  }

  static String? _findSourceProject(List<String> starts) {
    for (final start in starts) {
      var dir = Directory(start).absolute;
      for (var i = 0; i < 8; i++) {
        final candidate = p.join(dir.path, 'agent-core');
        if (File(p.join(candidate, 'pyproject.toml')).existsSync()) return candidate;
        final parent = dir.parent;
        if (parent.path == dir.path) break;
        dir = parent;
      }
    }
    return null;
  }
}
