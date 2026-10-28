import 'package:get/get.dart';

import 'config_repository.dart';

class ConfigController extends GetxController {
  ConfigController(this.repository);

  final ConfigRepository repository;
}
