import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../app/feature_module.dart';
import 'settings_controller.dart';
import 'settings_page.dart';
import 'settings_repository.dart';

final settingsFeature = FeatureModule(
  id: 'settings',
  title: '设置',
  icon: Icons.settings_outlined,
  page: () => const SettingsPage(),
  bind: () => Get.lazyPut(() => SettingsController(SettingsRepository())),
);
