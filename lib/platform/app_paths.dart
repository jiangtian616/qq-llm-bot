import 'dart:convert';
import 'dart:io';

import 'package:path/path.dart' as p;

/// 数据目录用的是哪一条规则。
enum DataDirSource { environment, portable, custom, standard }

class DataDirException implements Exception {
  DataDirException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// 数据目录。和核心服务的 infra/paths.py 按同一套规则解析，从上往下用第一个命中的：
///
/// 1. 环境变量 `QQ_LLM_BOT_DATA_DIR`；
/// 2. 便携模式：程序旁边有 `qq-llm-bot-data` 文件夹，数据就放在里面；
/// 3. 设置页选的自定义目录，路径记在系统默认目录的指针文件 `data-location.json` 里；
/// 4. 系统默认目录：Windows `%APPDATA%\qq-llm-bot`，macOS `~/Library/Application Support/qq-llm-bot`，
///    Linux `$XDG_DATA_HOME/qq-llm-bot`（默认 `~/.local/share/qq-llm-bot`）。
///
/// 安装包不再自带便携文件夹：升级时常常是删掉整个旧程序文件夹再解压新包，数据放在程序旁边会跟着一起删掉。
class AppPaths {
  AppPaths._(this.dataDir, this.source);

  static const appName = 'qq-llm-bot';
  static const envVar = 'QQ_LLM_BOT_DATA_DIR';

  /// 便携模式的标记兼数据目录。不叫 data：Flutter 的 Windows / Linux 产物里已经有一个 data 目录。
  static const portableDirName = 'qq-llm-bot-data';

  /// 自定义数据目录的指针文件，放在系统默认目录里，格式 `{"path": "..."}`。
  static const pointerFileName = 'data-location.json';

  final String dataDir;
  final DataDirSource source;

  bool get isPortable => source == DataDirSource.portable;

  static String get standardDataDir => _standardDataDir();
  static String get pointerFile => p.join(_standardDataDir(), pointerFileName);

  String get controlFile => p.join(dataDir, 'control.json');
  String get desktopLockFile => p.join(dataDir, 'desktop.lock');
  String get desktopPortFile => p.join(dataDir, 'desktop.port');
  String get desktopPrefsFile => p.join(dataDir, 'desktop.json');
  String get logsDir => p.join(dataDir, 'logs');

  static AppPaths resolve() {
    final override = Platform.environment[envVar];
    if (override != null && override.isNotEmpty) {
      return _create(override, DataDirSource.environment);
    }
    final portable = p.join(programAnchor(), portableDirName);
    if (Directory(portable).existsSync()) {
      // 便携文件夹在但写不进去（比如放在了 Program Files 下）时直接报错，不悄悄换到系统目录。
      _ensureWritable(portable);
      return AppPaths._(portable, DataDirSource.portable);
    }
    final custom = _customDataDir();
    if (custom != null) return AppPaths._(custom, DataDirSource.custom);
    return _create(_standardDataDir(), DataDirSource.standard);
  }

  /// 读指针文件；没有或内容不对时返回 null。指向的目录不存在时直接报错，不悄悄换回默认目录：那样看起来就像数据全丢了。
  static String? _customDataDir() {
    final file = File(pointerFile);
    if (!file.existsSync()) return null;
    final String? path;
    try {
      final json = jsonDecode(file.readAsStringSync());
      path = json is Map ? json['path'] as String? : null;
    } on Object {
      return null;
    }
    if (path == null || path.isEmpty) return null;
    if (!Directory(path).existsSync()) {
      throw DataDirException('设置的数据目录不存在：$path。如果它在移动硬盘或网络盘上，请先接好再启动；'
          '不再使用这个目录的话，删除 ${file.path} 即可改回默认目录。');
    }
    return p.normalize(p.absolute(path));
  }

  /// 把自定义数据目录写进指针文件；传 null 改回默认目录。下次启动生效。
  static void setCustomDataDir(String? dir) {
    final file = File(pointerFile);
    if (dir == null) {
      if (file.existsSync()) file.deleteSync();
      return;
    }
    file.parent.createSync(recursive: true);
    file.writeAsStringSync(jsonEncode({'path': dir}));
  }

  /// 「程序旁边」：Windows / Linux 免安装包是可执行文件所在目录，macOS 是 .app 所在目录，
  /// AppImage 是 AppImage 文件所在目录（可执行文件本身在临时挂载的目录里）。
  static String programAnchor() {
    final appImage = Platform.environment['APPIMAGE'];
    if (Platform.isLinux && appImage != null && appImage.isNotEmpty) {
      return p.dirname(appImage);
    }
    final exeDir = p.dirname(Platform.resolvedExecutable);
    if (Platform.isMacOS) {
      // X.app/Contents/MacOS/<exe> → X.app 所在目录
      return p.dirname(p.dirname(p.dirname(exeDir)));
    }
    return exeDir;
  }

  static AppPaths _create(String dir, DataDirSource source) {
    Directory(dir).createSync(recursive: true);
    return AppPaths._(dir, source);
  }

  static void _ensureWritable(String dir) {
    final probe = File(p.join(dir, '.write-test'));
    try {
      probe.writeAsStringSync('');
      probe.deleteSync();
    } on FileSystemException catch (e) {
      throw DataDirException('便携模式的数据目录不可写：$dir（${e.osError?.message ?? e.message}）。'
          '请把程序移到有写入权限的位置，或删除 $portableDirName 文件夹改用系统目录。');
    }
  }

  static String _standardDataDir() {
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
