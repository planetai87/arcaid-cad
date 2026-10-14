# arcaid-cad 작업 안내

기존 CAD(GstarCAD 등)를 MCP로 조작하는 서버이자, 도면을 읽고·만들고·검사하는 엔진을 키우는 저장소다.

## 문서

계획의 원본은 Claude Docs 문서이고, `docs/`는 Claude Code가 읽는 사본이다.

| 문서 | 사본 | 원본 |
|---|---|---|
| 로드맵 | `docs/1-roadmap.md` | https://claude.ai/artifact/GFbKbZVqSELL7Nu93KFhqh |
| 구조 결정(D1~D9) | `docs/2-decisions.md` | https://claude.ai/artifact/9zCNeUvqjGNbgLFoy84oYc |
| 0단계 실행 계획(T0~T10) | `docs/3-phase0-plan.md` | https://claude.ai/artifact/K4wE78SmhsBUaxn5xexHd4 |
| 사전 검증 결과(T0) | `docs/spike-report.md` | 저장소가 원본 |

- 계획과 다르게 구현하거나 결정을 바꿔야 하면 코드보다 문서를 먼저 고친다. 바꿀 내용을 사용자에게 먼저 보여 주고, 확인되면 원본과 사본을 함께 고친다.

## 코드 배치

- `src/engine/`: CAD 없이 도는 엔진. ezdxf·pydantic·PyYAML과 표준 라이브러리만 쓴다. `win32com`·`pythoncom` 등 COM 모듈 금지.
- `src/server.py`: MCP 서버. Claude Code에 이 경로로 등록돼 있으므로 위치·이름을 바꾸지 않는다. 패키지 import와 스크립트 직접 실행을 모두 지원한다.
- `src/cad_controller.py`: COM 계층. MCP만 쓴다. CAD마다 다른 동작은 여기서만 나눈다.
- `src/colors.py`: 색 이름 해석. 중국어 색 이름은 기능 키워드라 번역하지 않는다.
- `spikes/`: 사전 검증(T0) 실험 코드. 제품 코드로 옮기지 않는다.
- `skills/cad-drawing-standard/`: 회사 도면 표준 스킬의 원본.
- `src/snapshot_service.py`: 스냅샷 조율(MCP 쪽). CAD 내보내기(`cad_controller.export_dxf`·`export_closed_file`) + 엔진 메타(`engine/snapshot.py`). 도면 판독은 `engine/read.py`.
- `src/engine/check.py`: 표준 검사(`check_standard`). 위반 코드·고칠 목록의 기준. 검사 항목을 바꾸면 `tests/engine/check_cases.py`의 시험 도면과 골든 파일(`tests/engine/golden/`, `ARCAID_UPDATE_GOLDEN=1`로 다시 만든 뒤 한 줄씩 대조)을 함께 고친다.
- DXF 사본은 COM `Export`로만 만든다. `save_drawing`으로 DXF를 저장하지 않는다(열린 문서가 DXF로 바뀜). DXFOUT도 쓰지 않는다(미저장 표시를 지움).
- `standards/heo-2026/`: 회사 표준 프로파일(YAML)과 `template.dxf`. 표준값의 단일 원본이다. 스킬 references의 표는 여기서 생성한다(`tools/standards/gen_references.py`), 손으로 고치지 않는다.
- 템플릿 DWG를 고치면 `tools/template/export_template_dxf.py`(GstarCAD 필요)로 `template.dxf`와 해시를 다시 만든다. 안 하면 엔진 테스트가 실패한다.

## 규칙

- 주석과 메시지는 한국어로 쓴다.
- PC별 설정은 `src/config.local.json`에 둔다(커밋하지 않는다).
- CAD 시험은 사용자의 실제 도면이 아닌 새 시험 도면에서만 한다. 시험 작업 폴더는 `C:\arcaid\work`(한글·공백 없는 로컬 경로).
- 기준 CAD는 GstarCAD 2021. BricsCAD는 호환 확인, AutoCAD·ZWCAD는 미검증.
- 작업 하나 = 브랜치 하나 = PR 하나.

## 테스트

```powershell
.venv\Scripts\python.exe -m pytest                      # 엔진·단위·MCP 툴 목록(Windows)
$env:ARCAID_CAD_TESTS="1"; .venv\Scripts\python.exe -m pytest tests/cad   # CAD 연동(GstarCAD 실행 중)
```

- `tests/engine/`: CAD 없이 도는 엔진 테스트. GitHub Actions(Linux)에서 매 커밋 돈다.
- `tests/mcp/`: Windows 전용(pywin32), CAD 불필요. MCP 툴 목록 고정 테스트 — 툴을 더하거나 빼면 여기도 고친다.
- `tests/cad/`: 실행 중인 CAD가 필요하다. `ARCAID_CAD_TESTS=1`일 때만 모은다.
- 의존성 설치: `uv pip install --python .venv\Scripts\python.exe -r requirements.txt` (venv에 pip가 없다).
