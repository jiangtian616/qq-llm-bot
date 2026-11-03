"""qq-llm-bot 核心服务。"""

# pyproject.toml 的版本号从这里读（hatch 的动态版本），只改这一处；要和 pubspec.yaml 的版本号保持一致。
__version__ = "0.1.7"

# 本机控制接口的版本号。桌面端握手时比对，不兼容就提示用户同时升级两端。
API_VERSION = 1
