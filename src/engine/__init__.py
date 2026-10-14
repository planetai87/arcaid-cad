"""arcaid-cad 엔진: 도면 읽기·생성·검사(CAD에 의존하지 않는다)

구조 결정 D1: 이 패키지는 ezdxf·pydantic·PyYAML과 표준 라이브러리만 쓴다.
win32com·pythoncom 같은 COM 모듈을 불러오면 안 된다(tests/engine/test_no_win32com.py가 확인한다).
MCP(src/server.py)와 CLI가 같은 엔진 함수를 부르며, 엔진 테스트는 CAD 없이 Linux에서도 돈다.
"""

ENGINE_VERSION = "3.0.0"   # pyproject.toml version과 같게 둔다(생성 객체 태그 ver)
