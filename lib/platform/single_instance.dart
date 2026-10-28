import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// 桌面端单实例：数据目录里的锁文件 + 本机端口。第二个实例发现已在运行，就通知已有窗口到前台后退出。
/// 不用只支持 Windows 的单实例插件。
class SingleInstance {
  SingleInstance._(this._lock, this._server);

  final RandomAccessFile _lock;
  final ServerSocket _server;

  static const _showCommand = 'show';

  /// 拿到锁返回实例；已有实例在运行时通知它显示窗口，并返回 null。
  static Future<SingleInstance?> acquire({
    required String lockFile,
    required String portFile,
    required void Function() onShowRequested,
  }) async {
    final raf = await File(lockFile).open(mode: FileMode.append);
    try {
      await raf.lock(FileLock.exclusive);
    } on FileSystemException {
      await raf.close();
      await _notifyExisting(portFile);
      return null;
    }
    final server = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
    await File(portFile).writeAsString('${server.port}');
    server.listen((socket) {
      utf8.decoder.bind(socket).listen((data) {
        if (data.trim() == _showCommand) onShowRequested();
        socket.destroy();
      });
    });
    return SingleInstance._(raf, server);
  }

  static Future<void> _notifyExisting(String portFile) async {
    try {
      final port = int.parse((await File(portFile).readAsString()).trim());
      final socket = await Socket.connect(InternetAddress.loopbackIPv4, port, timeout: const Duration(seconds: 2));
      socket.write('$_showCommand\n');
      await socket.flush();
      await socket.close();
    } on Object {
      // 已有实例没响应，什么也不做。
    }
  }

  Future<void> release() async {
    await _server.close();
    await _lock.unlock();
    await _lock.close();
  }
}
