import 'dart:io';

import 'package:flutter/material.dart';
import 'package:get/get.dart';

import 'routes.dart';

/// 中文字体：先用各平台自带的。精简 Linux 发行版可能没有中文字体，打包阶段再内置一款开源字体（如思源黑体）。
String? get _cjkFontFamily {
  if (Platform.isWindows) return 'Microsoft YaHei UI';
  if (Platform.isMacOS) return 'PingFang SC';
  return null;
}

const _cjkFallback = ['Microsoft YaHei UI', 'PingFang SC', 'Noto Sans CJK SC', 'Source Han Sans SC', 'WenQuanYi Micro Hei'];

ThemeData _theme(Brightness brightness) => ThemeData(
      colorSchemeSeed: const Color(0xFF12B7F5),
      brightness: brightness,
      fontFamily: _cjkFontFamily,
      fontFamilyFallback: _cjkFallback,
      useMaterial3: true,
    );

class QQLlmBotApp extends StatelessWidget {
  const QQLlmBotApp({super.key});

  @override
  Widget build(BuildContext context) {
    return GetMaterialApp(
      title: 'qq-llm-bot',
      debugShowCheckedModeBanner: false,
      theme: _theme(Brightness.light),
      darkTheme: _theme(Brightness.dark),
      initialRoute: AppRoutes.initial,
      getPages: AppRoutes.pages,
    );
  }
}
