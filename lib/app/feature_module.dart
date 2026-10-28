import 'package:flutter/widgets.dart';

/// 一个桌面功能：路由、菜单项、页面，以及注册自己的 controller / repository。
/// 新增页面就是新建一个 features/ 目录，在 FeatureRegistry 里登记一行。
class FeatureModule {
  const FeatureModule({
    required this.id,
    required this.title,
    required this.icon,
    required this.page,
    required this.bind,
  });

  final String id;
  final String title;
  final IconData icon;
  final Widget Function() page;
  final void Function() bind;

  String get route => '/$id';
}
