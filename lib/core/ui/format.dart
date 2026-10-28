/// 界面上常用的时间格式：今天的只显示时分秒，其余加上日期。
String formatTime(DateTime? t) {
  if (t == null) return '—';
  final now = DateTime.now();
  String two(int n) => n.toString().padLeft(2, '0');
  final hms = '${two(t.hour)}:${two(t.minute)}:${two(t.second)}';
  if (t.year == now.year && t.month == now.month && t.day == now.day) return hms;
  return '${t.year == now.year ? '' : '${t.year}-'}${two(t.month)}-${two(t.day)} $hms';
}

/// 美元金额：很小的金额多保留几位小数。
String formatUsd(double v) => v >= 0.01 ? '\$${v.toStringAsFixed(2)}' : '\$${v.toStringAsFixed(5)}';
