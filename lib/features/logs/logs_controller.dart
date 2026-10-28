import 'package:get/get.dart';

import 'logs_repository.dart';

class LogsController extends GetxController {
  LogsController(this.repository);

  final LogsRepository repository;
}
