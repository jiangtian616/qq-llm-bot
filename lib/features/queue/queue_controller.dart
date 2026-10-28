import 'package:get/get.dart';

import 'queue_repository.dart';

class QueueController extends GetxController {
  QueueController(this.repository);

  final QueueRepository repository;
}
