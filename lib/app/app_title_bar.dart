import 'package:flutter/material.dart';
import 'package:window_manager/window_manager.dart';

/// 自绘标题栏：不显示图标和标题，空白处拖动窗口、双击最大化，右边是最小化、最大化、关闭按钮（Windows 11 样式）。
/// 背景透明，和页面连成一片。关闭按钮走 WindowService 的逻辑（最小化到托盘）。
class AppTitleBar extends StatelessWidget {
  const AppTitleBar({super.key});

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: kWindowCaptionHeight,
      child: WindowCaption(brightness: Theme.of(context).brightness, backgroundColor: Colors.transparent),
    );
  }
}
