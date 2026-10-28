import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../app/feature_module.dart';
import 'config_controller.dart';
import 'config_page.dart';
import 'config_repository.dart';

final configFeature = FeatureModule(
  id: 'config',
  title: '配置',
  icon: Icons.tune,
  page: () => const ConfigPage(),
  bind: () => Get.lazyPut(() => ConfigController(ConfigRepository())),
);
