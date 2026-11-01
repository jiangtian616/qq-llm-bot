import 'dart:io';

import 'package:path/path.dart' as p;

import 'app_paths.dart';

/// 换数据目录时怎么处理数据。
enum DataDirAction {
  /// 新目录里没有数据，把当前数据复制过去。
  copy,

  /// 新目录里已经有数据（比如以前用过的），直接用它。
  useExisting,

  /// 新目录里已经有数据，用当前数据替换，原有的挪到它的 backups 里留底。
  replace,
}

/// 更换数据目录：复制数据、写指针文件。调用前核心服务要先停下，数据库文件不能还在写。
///
/// 原来的目录不删，用户确认新目录没问题后自己删。
class DataDirMover {
  /// 只属于正在运行的这一份进程，不复制。
  static const _skipped = {
    'agent-core.lock',
    'control.json',
    'desktop.lock',
    'desktop.port',
    '.write-test',
    AppPaths.pointerFileName,
  };

  /// 替换时要挪走留底的文件：数据库和它的 WAL，以及会被覆盖的配置。
  static const _replaced = [
    'agent-core.db',
    'agent-core.db-wal',
    'agent-core.db-shm',
    'secrets.json',
    'agent-core.toml',
    'desktop.json',
  ];

  static bool hasData(String dir) => File(p.join(dir, 'agent-core.db')).existsSync();

  /// 新目录不能用时返回原因。
  static String? validate(String current, String target) {
    if (p.equals(current, target)) return '和当前的数据目录相同';
    if (p.isWithin(current, target)) return '不能放在当前数据目录里面';
    final probe = File(p.join(target, '.write-test'));
    try {
      probe.writeAsStringSync('');
      probe.deleteSync();
    } on FileSystemException catch (e) {
      return '这个文件夹不可写（${e.osError?.message ?? e.message}）';
    }
    return null;
  }

  /// 放在程序目录里时，升级删掉程序文件夹会把数据一起删掉。
  static bool insideProgramDir(String target) {
    final anchor = AppPaths.programAnchor();
    return p.equals(anchor, target) || p.isWithin(anchor, target);
  }

  static Future<void> apply({required String current, required String target, required DataDirAction action}) async {
    if (action == DataDirAction.replace) await _setAside(target);
    if (action != DataDirAction.useExisting) await _copyTree(Directory(current), target, topLevel: true);
    AppPaths.setCustomDataDir(p.equals(target, AppPaths.standardDataDir) ? null : target);
  }

  static Future<void> _setAside(String dir) async {
    final stamp = DateTime.now().toIso8601String().replaceAll(RegExp(r'[:.]'), '-').substring(0, 19);
    final aside = Directory(p.join(dir, 'backups', 'replaced-$stamp'));
    for (final name in _replaced) {
      final file = File(p.join(dir, name));
      if (!file.existsSync()) continue;
      await aside.create(recursive: true);
      await file.rename(p.join(aside.path, name));
    }
  }

  static Future<void> _copyTree(Directory from, String to, {bool topLevel = false}) async {
    await Directory(to).create(recursive: true);
    await for (final entity in from.list(followLinks: false)) {
      final name = p.basename(entity.path);
      if (topLevel && _skipped.contains(name)) continue;
      final dest = p.join(to, name);
      if (entity is Directory) {
        await _copyTree(entity, dest);
      } else if (entity is File) {
        await entity.copy(dest);
      }
    }
  }
}
