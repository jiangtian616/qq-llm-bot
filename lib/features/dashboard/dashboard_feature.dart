import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../app/feature_module.dart';
import 'dashboard_controller.dart';
import 'dashboard_page.dart';
import 'dashboard_repository.dart';

final dashboardFeature = FeatureModule(
  id: 'dashboard',
  title: '仪表盘',
  icon: Icons.dashboard_outlined,
  page: () => const DashboardPage(),
  bind: () => Get.lazyPut(() => DashboardController(DashboardRepository())),
);
