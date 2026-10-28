import 'package:flutter/material.dart';
import 'package:get/get.dart';

import '../../app/feature_module.dart';
import 'queue_controller.dart';
import 'queue_page.dart';
import 'queue_repository.dart';

final queueFeature = FeatureModule(
  id: 'queue',
  title: '消息队列',
  icon: Icons.inbox_outlined,
  page: () => const QueuePage(),
  bind: () => Get.lazyPut(() => QueueController(QueueRepository())),
);
