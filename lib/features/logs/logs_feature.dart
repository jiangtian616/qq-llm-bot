import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../app/feature_module.dart';
import 'logs_controller.dart';
import 'logs_page.dart';
import 'logs_repository.dart';

final logsFeature = FeatureModule(
  id: 'logs',
  title: '日志',
  icon: Icons.receipt_long_outlined,
  page: () => const LogsPage(),
  bind: () => Get.lazyPut(() => LogsController(LogsRepository())),
);
