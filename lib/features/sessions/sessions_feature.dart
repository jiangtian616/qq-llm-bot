import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../app/feature_module.dart';
import 'sessions_controller.dart';
import 'sessions_page.dart';
import 'sessions_repository.dart';

final sessionsFeature = FeatureModule(
  id: 'sessions',
  title: '会话',
  icon: Icons.chat_bubble_outline,
  page: () => const SessionsPage(),
  bind: () => Get.lazyPut(() => SessionsController(SessionsRepository())),
);
