# Repository Guidelines

## 프로젝트 구조와 모듈

- `src/engine/`: CAD 없이 DXF를 읽고·생성하고·검사한다. ezdxf·pydantic·PyYAML과 표준 라이브러리만 사용한다.
- `src/engine/walls.py`: 벽 조각 합치기. 표준 라이브러리만 사용한다.
- `src/cli.py`: 파일 단위 CLI. 엔진·CLI·`src/egress.py`에는 MCP·COM 모듈을 가져오지 않는다.
- `src/server.py`: MCP 진입점이므로 경로·이름과 직접 실행·패키지 가져오기를 유지한다. CAD별 차이는 `src/cad_controller.py`에서만 처리한다. `draw_batch`는 인라인 `spec` 또는 로컬 절대 `.json` 경로 `spec_path` 중 하나를 받는다(`apply_service.load_batch_spec`, CAD 연결 전 거부).
- `standards/heo-2026/`: 표준 YAML·DXF. `skills/cad-drawing-standard/`: 스킬·DWG 자산. `.claude/agents/`: Claude Code 서브에이전트 정의(plan-reviewer·plan-reader, Codex는 쓰지 않음). `tools/`: 유지보수 도구(`tools/review/render.py`는 미리보기 DXF 렌더·비교·좌표 목록, `tools/ops/ops.py`는 ops 폴더 verify·bind, `tools/raster/measure.py`는 평면 이미지 보정·직선·각도·프로파일·점(blob)·펴 보기, `tools/raster/record.py`는 측정 기록·잠금·합치기·재검증, `tools/raster/generate.py`는 measure.yaml → 미리보기·ops 폴더(형상, 주석·기호는 `annotate.py`). numpy·shapely는 tools/raster에서만 쓴다). `docs/`: 계획 사본. `spikes/`: 실험 전용이며 제품 코드로 옮기지 않는다.

## 개발·실행 명령

Python 3.10 이상과 uv를 사용하며 저장소 루트에서 실행한다. 표준·정책 파일이 필요하므로 wheel 단독 배포는 지원하지 않는다.

```powershell
uv venv --python 3.10                           # 가상환경 생성
uv pip install --python .venv\Scripts\python.exe -r requirements.txt  # 의존성 설치
.venv\Scripts\python.exe src/server.py          # MCP 서버 실행
.venv\Scripts\python.exe -m src.cli check standards/heo-2026/template.dxf --scale 100 --strict  # 표준 검사
.venv\Scripts\python.exe -m pytest -q           # 기본 테스트
$env:ARCAID_CAD_TESTS="1"
.venv\Scripts\python.exe -m pytest tests/cad     # 실행 중인 CAD 연동 시험
```

## 코드 작성 규칙

들여쓰기는 공백 4칸, 함수·모듈은 `snake_case`, 클래스는 `PascalCase`, 상수는 `UPPER_SNAKE_CASE`를 따른다. 기존 타입 표기와 Pydantic 모델을 참고한다. 주석·메시지는 한국어로 쓰고 중국어 색상 키워드는 보존한다. 별도 포매터·린터 설정은 없다.

## 도면 보호와 표준·결과 관리

- 사용자 실제 도면에서 시험하지 않는다. `C:\arcaid\work`의 새 시험 도면을 사용한다. 기준은 GstarCAD 2021이며 BricsCAD는 호환 확인, AutoCAD·ZWCAD는 미검증이다.
- DXF 사본은 COM `Export`로만 만든다. DXF 저장에 `save_drawing`·DXFOUT을 사용하지 않는다. `plot`도 내보낸 사본에서 수행한다.
- Undo 묶음에서는 COM 호출만 사용한다. 명령·LISP·자체 묶음을 여는 `draw_*`는 금지하며 복귀는 스냅샷 핸들 집합으로 확인한다.
- 표준값은 YAML이 단일 원본이다. 참조 표는 수동 편집하지 않고 `python tools/standards/gen_references.py --write`로 생성한 뒤 `--check`로 검증한다. 템플릿 DWG 변경 시 `tools/template/export_template_dxf.py`로 DXF·해시를 갱신한다.
- 외부 결과는 `src/egress.py`와 `config/egress.yaml`을 거친다. 새 필드의 정책 분류를 갱신하고 값의 타입을 유지한다. CLI 표준 출력은 필터링한 UTF-8 JSON 하나이며 전체 결과는 `--full` 지정 시에만 저장한다.
- PC별 설정은 커밋하지 않는 `src/config.local.json`에, 생성 도면은 무시되는 `output/`에 둔다.

## 테스트 지침

pytest를 사용하며 파일은 `test_*.py`, 함수는 `test_*`로 작성한다. `tests/engine/`·`tests/cli/`는 Linux·Windows에서, `tests/mcp/`는 Windows 의존성을 갖추고 CAD 없이 실행한다. `tests/cad/`는 실행 중인 CAD와 `ARCAID_CAD_TESTS=1`이 필요하다. 수치화된 커버리지 기준은 없다.

도구 변경 시 목록·설명 테스트를 갱신한다. 새 저수준 도구는 `UNPROTECTED_TOOLS`에도 등록한다. 검사 변경 시 시험 도면과 골든 파일을 함께 수정하고, `ARCAID_UPDATE_GOLDEN=1`로 재생성한 결과를 한 줄씩 대조한다.

## 커밋·PR과 문서 협업

작업 하나당 브랜치·PR 하나를 사용한다. 커밋은 `문서: T8 완료 반영`처럼 범위와 변경을 간결한 한국어로 작성한다. PR에는 변경 내용·관련 이슈·검증 결과를 적고 시각적 변경에는 CAD 화면을 첨부한다.

세부 작업 규칙은 `CLAUDE.md`를 참고한다. 계획·구조 결정을 바꿀 때는 변경안을 사용자에게 먼저 보여 주고, 확인 후 코드보다 문서를 먼저 수정한다. Claude Docs 원본과 `docs/` 사본을 함께 갱신한다.
