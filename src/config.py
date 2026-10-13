"""配置加载模块：全局单例

此前 config.json 在 server.py / cad_controller.py / nlp_processor.py 三处被重复读取，
现统一收敛到本模块，整个进程只加载一次配置。
"""

import json
import logging
import os
from functools import lru_cache

logger = logging.getLogger('cad_mcp_config')

# 配置文件与本模块位于同一目录
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
# PC별 설정 파일: git에 올리지 않으며, config.json 위에 덮어쓴다
_LOCAL_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.local.json')

# 默认配置（配置文件缺失或损坏时的兜底，与旧版 __init__.py 行为一致）
_DEFAULT_CONFIG = {
    "server": {
        "name": "CAD MCP Server",
        "version": "2.0.0",
        "transport": "stdio",
        "host": "127.0.0.1",
        "port": 8000
    },
    "cad": {
        "type": "AUTOCAD",
        "startup_wait_time": 20,
        "command_delay": 0.5
    },
    "output": {
        "directory": "./output",
        "default_filename": "cad_drawing.dwg"
    }
}


def _deep_merge(base: dict, override: dict) -> dict:
    """override의 값을 base 위에 재귀적으로 덮어쓴 새 dict를 돌려준다"""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_local_override(config: dict) -> dict:
    """config.local.json이 있으면 덮어쓴다. 파일이 깨졌으면 경고만 남기고 무시한다"""
    if not os.path.exists(_LOCAL_CONFIG_PATH):
        return config
    try:
        with open(_LOCAL_CONFIG_PATH, 'r', encoding='utf-8-sig') as f:
            local = json.load(f)
    except Exception as e:
        logger.warning(f"config.local.json 읽기 실패, 무시함: {str(e)}")
        return config
    if not isinstance(local, dict):
        logger.warning("config.local.json 최상위가 객체가 아니라서 무시함")
        return config
    logger.info("config.local.json 적용")
    return _deep_merge(config, local)


@lru_cache(maxsize=1)
def get_config() -> dict:
    """加载并缓存配置文件（进程内单例）"""
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
        logger.info("配置文件加载成功")
    except Exception as e:
        logger.error(f"加载配置文件失败: {str(e)}，使用默认配置")
        config = _DEFAULT_CONFIG
    return _load_local_override(config)
