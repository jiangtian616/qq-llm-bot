import 'dart:io';

import 'package:path/path.dart' as p;

/// 数据目录。和核心服务的 platformdirs 规则保持一致，单独运行核心服务时两边也能找到同一份数据：
/// Windows `%APPDATA%\qq-llm-bot`，macOS `~/Library/Application Support/qq-llm-bot`，
/// Linux `$XDG_DATA_HOME/qq-llm-bot`（默认 `~/.local/share/qq-llm-bot`）。
class AppPaths {
  AppPaths._(this.dataDir);

  static const appName = 'qq-llm-bot';

  final String dataDir;

  String get controlFile => p.join(dataDir, 'control.json');
  String get desktopLockFile => p.join(dataDir, 'desktop.lock');
  String get desktopPortFile => p.join(dataDir, 'desktop.port');
  String get logsDir => p.join(dataDir, 'logs');

  static AppPaths resolve() {
    final override = Platform.environment['QQ_LLM_BOT_DATA_DIR'];
    final dir = (override != null && override.isNotEmpty) ? override : _defaultDataDir();
    Directory(dir).createSync(recursive: true);
    return AppPaths._(dir);
  }

  static String _defaultDataDir() {
    final env = Platform.environment;
    if (Platform.isWindows) {
      return p.join(env['APPDATA']!, appName);
    }
    final home = env['HOME']!;
    if (Platform.isMacOS) {
      return p.join(home, 'Library', 'Application Support', appName);
    }
    final xdg = env['XDG_DATA_HOME'];
    return p.join((xdg != null && xdg.isNotEmpty) ? xdg : p.join(home, '.local', 'share'), appName);
  }
}
