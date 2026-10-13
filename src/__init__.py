"""
arcaid-cad 서비스 패키지
"""

import logging

# 주의: 여기서 절대 logging.basicConfig를 호출하면 안 된다.
# 패키지 방식으로 실행하면(arcaid-cad 진입점이 src.server를 import) 이 모듈이 server.py보다 먼저 실행된다.
# 이때 basicConfig를 부르면 root logger에 기본 StreamHandler가 먼저 붙는다.
# 그러면 server.py의 로그 설정(FileHandler 포함)이 조용히 무시되고
# (root에 이미 handler가 있으면 basicConfig는 아무것도 하지 않는다) cad_mcp.log가 영원히 비어 있게 된다.
# 로그 설정은 server.py 진입점에서만 한다.

logger = logging.getLogger('cad_mcp')

# config.py의 설정 싱글턴을 사용한다(불러오기에 실패하면 기본 설정으로 대체)
try:
    from .config import get_config
    config = get_config()
except ImportError:
    from config import get_config
    config = get_config()

__all__ = [
    'config'
]
