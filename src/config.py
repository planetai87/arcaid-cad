"""설정 불러오기 모듈: 프로세스 전체 싱글턴

예전에는 config.json을 server.py / cad_controller.py / nlp_processor.py(지금은 지운 자연어 처리 모듈) 세 곳에서 따로 읽었다.
지금은 이 모듈로 모아서 프로세스 전체에서 한 번만 읽는다.
"""

import json
import logging
import os
from functools import lru_cache

logger = logging.getLogger('cad_mcp_config')

# 설정 파일은 이 모듈과 같은 폴더에 있다
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
# PC별 설정 파일: git에 올리지 않으며, config.json 위에 덮어쓴다
_LOCAL_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.local.json')

# 기본 설정(설정 파일이 없거나 깨졌을 때의 대체값, 옛 __init__.py 동작과 같다)
_DEFAULT_CONFIG = {
    "server": {
        "name": "arcaid-cad",
        "version": "3.0.0",
        "transport": "stdio",
        "host": "127.0.0.1",
        "port": 8000
    },
    "cad": {
        "type": "AUTOCAD",
        "startup_wait_time": 20,
        "command_delay": 0.5,
        "command_timeout": 10
    },
    "output": {
        "directory": "./output",
        "default_filename": "cad_drawing.dwg"
    },
    # 스냅샷·작업 사본 폴더(한글·공백 없는 로컬 경로 권장, D6)
    "work": {
        "directory": r"C:\arcaid\work"
    },
    # 외부 반환 필터 정책 파일(D8). 상대 경로는 저장소 루트 기준. 환경 변수 ARCAID_EGRESS_FILE이 우선
    "egress": {
        "file": "config/egress.yaml"
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
    """설정 파일을 읽어 캐시한다(프로세스 내 싱글턴)"""
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
        logger.info("설정 파일 불러옴")
    except Exception as e:
        logger.error(f"설정 파일 불러오기 실패: {str(e)}, 기본 설정 사용")
        config = _DEFAULT_CONFIG
    return _load_local_override(config)
