# arcaid-cad 작업 안내

기존 CAD(GstarCAD 등)를 MCP로 조작하는 서버이자, 도면을 읽고·만들고·검사하는 엔진을 키우는 저장소다.

## 문서

계획의 원본은 Claude Docs 문서이고, `docs/`는 Claude Code가 읽는 사본이다.

| 문서 | 사본 | 원본 |
|---|---|---|
| 로드맵 | `docs/1-roadmap.md` | https://claude.ai/artifact/GFbKbZVqSELL7Nu93KFhqh |
| 구조 결정(D1~D9) | `docs/2-decisions.md` | https://claude.ai/artifact/9zCNeUvqjGNbgLFoy84oYc |
| 0단계 실행 계획(T0~T10) | `docs/3-phase0-plan.md` | https://claude.ai/artifact/K4wE78SmhsBUaxn5xexHd4 |
| 0-B 실행 계획(B0~B6) | `docs/4-phase0b-plan.md` | https://claude.ai/artifact/YUiX4XGEB3nipxPFUL4GXB |
| 사전 검증 결과(T0) | `docs/spike-report.md` | 저장소가 원본 |

- 계획과 다르게 구현하거나 결정을 바꿔야 하면 코드보다 문서를 먼저 고친다. 바꿀 내용을 사용자에게 먼저 보여 주고, 확인되면 원본과 사본을 함께 고친다.

## 코드 배치

- `src/engine/`: CAD 없이 도는 엔진. ezdxf·pydantic·PyYAML과 표준 라이브러리만 쓴다. `mcp`·`win32com`·`pythoncom` 등 MCP·COM 모듈 금지.
- `src/cli.py`: CAD 없이 파일 단위 `read`·`check`·`build` 실행(`python -m src.cli` 또는 편집 가능 설치 뒤 `arcaid`). `mcp`·COM 모듈 금지. 표준 출력은 공용 정책으로 거른 UTF-8 JSON 하나이고, 전체 결과는 `--full`일 때만 저장한다. 저장소 checkout 기준이며 wheel 단독 배포는 지원하지 않는다.
- `src/engine/ops.py`: MCP·CLI 공용 검사 모델·상한(`check_file`)과 작성 결과 출력 상한(`limit_build`). 엔진 원본 manifest를 자르지 않는다.
- `src/server.py`: MCP 서버. Claude Code에 이 경로로 등록돼 있으므로 위치·이름을 바꾸지 않는다. 패키지 import와 스크립트 직접 실행을 모두 지원한다.
- `src/cad_controller.py`: COM 계층. MCP만 쓴다. CAD마다 다른 동작은 여기서만 나눈다.
- `src/server.py`의 `apply_changes`는 B2 서비스를 공개한다. 저수준 편집 9종은 controller의 원시 XDATA·소유 공간 검사와 복제본 태그 제거를 거쳐 `EditResult`를 `ToolResult`로 변환한다.
- `src/colors.py`: 색 이름 해석. 중국어 색 이름은 기능 키워드라 번역하지 않는다.
- `spikes/`: 사전 검증(T0) 실험 코드. 제품 코드로 옮기지 않는다.
- `skills/cad-drawing-standard/`: 회사 도면 표준 스킬의 원본. claude.ai 업로드 zip은 `tools/skills/package_skill.py -o <zip>`로 만든다(템플릿·상대 링크·zip 무결성 확인).
- `src/snapshot_service.py`: 스냅샷 조율(MCP 쪽). CAD 내보내기(`cad_controller.export_dxf`·`export_closed_file`) + 엔진 메타(`engine/snapshot.py`). 도면 판독은 `engine/read.py`.
- `src/engine/check.py`: 표준 검사(`check_standard`). 위반 코드·고칠 목록의 기준. 검사 항목을 바꾸면 `tests/engine/check_cases.py`의 시험 도면과 골든 파일(`tests/engine/golden/`, `ARCAID_UPDATE_GOLDEN=1`로 다시 만든 뒤 한 줄씩 대조)을 함께 고친다.
- `src/engine/spec.py`·`layout.py`·`write.py`: 객체 명세(JSON) → 검증 → 배치 규칙 전개 → R2013 DXF(`build_dxf`). 명세 형식의 원본은 `spec.py` docstring. 대칭·회전은 `layout.py`가 직접 계산한다(ezdxf `transform()`은 대칭을 OCS로 표현해 읽기·검사가 위치를 잘못 읽음). 생성 DXF는 쓰는 정의만 담고 저장 뒤 `check_standard`로 자체 검사한다.
- `src/engine/walls.py`: 같은 op·같은 레이어의 직교 벽 조각 합치기. 표준 라이브러리만 사용한다.
- `src/engine/state.py`·`apply.py`: 태그·기준 상태와 보호된 적용 판정(재실행·정의 충돌·넣은 결과·복귀·고칠 대상). `src/apply_service.py`: `draw_batch`·`check_standard(fix)`·`apply_changes` 조율(COM 스레드). Undo 묶음 안에서는 COM 호출만 쓴다(명령·LISP·자체 묶음을 여는 `draw_*` 금지, T0 E6). 복귀는 스냅샷 핸들 집합으로 확인한다. `apply_changes`는 속성 핸들·전체 ARCAID 태그·대상 밖 도형 지문도 대조하며, 적용 기록은 사후 확인 뒤 묶음 밖 마지막에 쓴다. `draw_batch`는 인라인 `spec` 또는 로컬 절대 `.json` 경로 `spec_path` 중 하나를 받는다(파일 읽기·거부는 `apply_service.load_batch_spec`, CAD 연결 전).
- `src/engine/changes.py`: 0-B 변경 명세(`apply_changes`)·대상 확정·before/after·판정·적용 기록 형식. 형식의 원본은 docstring. 판정 비교는 반올림 없는 `change_state`로 하고 0-A 태그의 `state_hash`는 바꾸지 않는다. 적용 기록은 B2가 Undo 묶음 밖에서 마지막에 쓴다(B0 E13).
- `src/tools_service.py`: `new_from_template`·`get_selection`·`plot` 조율. `plot`은 사용자 도면 레이아웃을 바꾸지 않도록 Export한 DXF 사본에서 출력한다(원래 출력기·CTB가 이 PC에 없으면 되돌릴 수 없음, T7 사전 확인). `src/engine/sheet.py`: 도곽 → 출력 창·k.
- `src/egress.py` + `config/egress.yaml`: MCP·CLI 공용 외부 반환 필터(D8, MCP·COM 모듈 금지). `egress.load_policy`는 인자(cwd 기준) → 환경 변수 → 설정 → 기본 정책(나머지는 저장소 기준) 순으로 찾는다. 새 결과 필드가 경로·문자·속성이면 `fields`에, 상태·코드처럼 늘 보낼 것이면 `always`에 넣는다. 정책을 바꿔도 출력 스키마가 깨지지 않게 값만 같은 타입으로 바꾼다.
- 새 저수준(보호되지 않는) 툴을 만들면 `server.py`의 `UNPROTECTED_TOOLS`와 `tests/mcp/test_tool_descriptions.py`에 넣는다.
- DXF 사본은 COM `Export`로만 만든다. `save_drawing`으로 DXF를 저장하지 않는다(열린 문서가 DXF로 바뀜). DXFOUT도 쓰지 않는다(미저장 표시를 지움).
- `standards/heo-2026/`: 회사 표준 프로파일(YAML)과 `template.dxf`. 표준값의 단일 원본이다. 스킬 references의 표는 여기서 생성한다. 손으로 고치지 않고 `python tools/standards/gen_references.py --write`로 만든 뒤 `--check`로 확인한다.
- `tools/acceptance/t10_dangsan.py`: T10 당산동 통과 시험의 로컬 준비·비교 도구(고정 입력 전용).
- `.claude/agents/`: Claude Code 서브에이전트 정의(plan-reviewer: 미리보기 DXF 검토, 지적만). 부모 작업 폴더 `D:\worktable\python\cad mcp\.claude\agents`는 이 폴더로 가는 정션이다. 새 정의를 처음 추가하면 Claude Code를 다시 시작해야 목록에 뜬다. 평가 정답표는 `output/plan-reviewer-eval/`.
- `tools/ops/ops.py`: ops 폴더(op별 명세 + 적용 순서 `hashes.json`) 도구(CAD 없음). `verify`는 목록·파일 대응·해시·표준 검증·전개(SPEC_ID 등)·op 사이 standard·units·scale·target 일치를 CAD 전에 확인한다. `bind`는 원본을 두고 새 실행 사본에만 target을 넣는다(target은 해시에 들어가므로 묶은 사본은 다시 쓰지 않음). 시험은 `tests/engine/test_ops_tool.py`.
- `tools/review/render.py`: 미리보기 DXF 검토 도구(CAD 없음, PIL). `render`(창 영역 PNG, 기준 대비 공통 회색·추가 빨강·삭제 파랑, `--frame` 회전 좌표 눈금), `diff`(T10 형상 서명으로 추가·삭제 개수와 영역), `list`(창 안 객체 좌표 JSON, 지역 좌표). 시험은 `tests/engine/test_review_render.py`.
- 템플릿 DWG를 고치면 `tools/template/export_template_dxf.py`(GstarCAD 필요)로 `template.dxf`와 해시를 다시 만든다. 안 하면 엔진 테스트가 실패한다.

## 규칙

- 주석과 메시지는 한국어로 쓴다.
- PC별 설정은 `src/config.local.json`에 둔다(커밋하지 않는다).
- CAD 시험은 사용자의 실제 도면이 아닌 새 시험 도면에서만 한다. 시험 작업 폴더는 `C:\arcaid\work`(한글·공백 없는 로컬 경로).
- 기준 CAD는 GstarCAD 2021. BricsCAD는 호환 확인, AutoCAD·ZWCAD는 미검증.
- 작업 하나 = 브랜치 하나 = PR 하나.
- 생성 도면·시험 로그·로컬 증거는 git이 무시하는 `output/`에 둔다. 실무 DWG와 전체 명세는 커밋하지 않는다.
- 커밋 메시지는 `범위: 변경`을 간결한 한국어로 쓴다(예: `문서: T8 완료 반영`, `시험: …`). PR에는 변경과 검증 결과를 적고, 화면이 바뀌면 CAD 캡처를 붙인다.
- `AGENTS.md`는 Codex용 요약 사본이다. 이 파일의 규칙을 바꾸면 함께 고친다.

## 테스트

```powershell
.venv\Scripts\python.exe -m pytest                      # 엔진·단위·MCP 툴 목록(Windows)
$env:ARCAID_CAD_TESTS="1"; .venv\Scripts\python.exe -m pytest tests/cad   # CAD 연동(GstarCAD 실행 중)
```

- `tests/engine/`: CAD 없이 도는 엔진 테스트. GitHub Actions에서 매 커밋 돈다(Ubuntu Python 3.10·3.12는 엔진·CLI 시험과 CLI 실행 단계, Windows Python 3.12는 `tests/mcp`·`tests/cli`·`tests/engine`).
- `tests/cli/`: CAD 없이 하위 프로세스로 CLI를 실행한다(Linux·Windows). 결과 동일성·파일 쓰기·필터·UTF-8·종료 코드를 확인한다.
- `tests/mcp/`: Windows 전용(pywin32), CAD 불필요. MCP 툴 목록 고정 테스트 — 툴을 더하거나 빼면 여기도 고친다.
- `tests/cad/`: 실행 중인 CAD가 필요하다. `ARCAID_CAD_TESTS=1`일 때만 모은다.
- 의존성 설치: `uv pip install --python .venv\Scripts\python.exe -r requirements.txt` (venv에 pip가 없다).
