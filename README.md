# arcaid-cad

Claude 같은 AI가 Windows의 CAD 프로그램(GstarCAD, BricsCAD, AutoCAD, ZWCAD)을 직접 조작하게 해 주는 MCP 서버입니다.
COM 자동화로 실행 중인 CAD에 붙어서 그리기·조회·편집·도면 관리·CAD 명령 실행을 합니다.

> 이 프로젝트는 [daobataotie/CAD-MCP](https://github.com/daobataotie/CAD-MCP)(MIT, © 2025 曹瑞)를 바탕으로 독립해 개발합니다.
> 원본은 `upstream` remote에 참고용으로만 두고, 원본 업데이트를 따라가지 않습니다.

## 지원 CAD

| CAD | `cad.type` 값 | COM ProgID |
|---|---|---|
| GstarCAD | `GSTARCAD` (또는 `GCAD`) | `GCAD.Application`, `GstarCAD.Application` |
| BricsCAD | `BRICSCAD` | `BricscadApp.AcadApplication` |
| AutoCAD | `AUTOCAD` | `AutoCAD.Application` |
| ZWCAD | `ZWCAD` | `ZWCAD.Application` |

- 실제로 쓰고 검증하는 환경은 GstarCAD 2021입니다. BricsCAD는 동료 PC에서 검증 중입니다.
- 이미 실행 중인 CAD가 있으면 그 창에 붙습니다. 없으면 새로 띄웁니다.

## 설치

필요한 것: Windows, Python 3.10 이상, [uv](https://docs.astral.sh/uv/), 위 CAD 중 하나, Claude Code.

```powershell
git clone https://github.com/planetai87/arcaid-cad.git
cd arcaid-cad
uv venv --python 3.10
uv pip install -r requirements.txt
```

비공개 저장소이므로 clone하려면 저장소에 초대된 GitHub 계정이 필요합니다.

## CLI

`arcaid`는 CAD 없이 DXF 파일을 읽고·검사하고·만듭니다. Windows와 Linux에서 **저장소 checkout 기준**으로 씁니다.
표준(`standards/`)과 정책(`config/`)이 패키지 밖에 있어 wheel만 설치해서는 실행할 수 없습니다.

```powershell
.venv\Scripts\python.exe -m src.cli read "도면.dxf"
.venv\Scripts\python.exe -m src.cli check "도면.dxf" --scale 100 --strict
.venv\Scripts\python.exe -m src.cli build tests/engine/specs/dangsan_typical.json -o "출력.dxf"

# 저장소에서 편집 가능 설치 후에는 arcaid 명령을 쓸 수 있습니다
uv pip install -e .
.venv\Scripts\arcaid.exe check "출력.dxf" --strict
```

Linux에서는 `python -m src.cli`로 실행합니다. 엔진 의존성(`ezdxf`, `pydantic>=2`, `PyYAML`)만 있어도 됩니다.
저장소 밖에서 모듈로 실행하려면 `PYTHONPATH`에 저장소 경로를 지정합니다.

표준 출력은 MCP와 같은 결과 모델·외부 반환 정책으로 거른 **UTF-8 JSON 하나**입니다(`PYTHONIOENCODING`보다 우선).
검사 위반과 고칠 핸들은 코드별 20건(`--limit N`으로 조정), 작성 manifest는 20줄까지만 반환하고 `truncated`로 알립니다.
`check --scale`을 생략하면 표준의 기본 축척(1/100)을 가정하고 `assumptions`에 적습니다.
DWG는 MCP `export_snapshot`으로 DXF 사본을 만든 뒤 입력합니다.

각 명령에 `--full <파일.json>`을 붙이면 필터 적용 전의 전체 결과를 그 파일에 씁니다.
`read`는 메타와 객체 전체 목록, `check`는 상한 없는 검사 결과(`fix` 없음), `build`는 manifest 전체를 포함한 작성 결과입니다.
`--full`과 `build -o` 이외에는 결과·로그·가린 원본을 저장하지 않습니다. 입력·출력·`--full`이 같은 파일이면 실행 전에 거부합니다.

공통 옵션은 명령 뒤에 지정합니다.

- `--policy <정책.yaml>`: cwd 기준입니다. 생략하면 `ARCAID_EGRESS_FILE` → 설정 `egress.file` → `config/egress.yaml` 순으로 찾습니다(이 세 경로는 저장소 기준). 정책을 읽거나 검증하지 못하면 실행 전에 고정 실패 JSON을 반환합니다.
- `--standard <프로파일 폴더>`: cwd 기준입니다. 생략하면 `ARCAID_STANDARD_DIR` → 저장소의 `standards/heo-2026`을 씁니다.

| 종료 코드 | 의미 |
|---|---|
| 0 | 실행 성공(일반 검사는 위반이 있어도 0) |
| 1 | 실행·검증·저장·정책 오류 또는 경로 충돌(`--full` 저장 실패 포함) |
| 2 | argparse 사용 오류(도움말을 출력하며 JSON이 아님) |
| 3 | `check --strict`에서 위반 또는 검사하지 못한 항목이 있음 |

실패(1)는 strict 불합격(3)보다 우선합니다. `build_dxf`의 자체 검사 실패 시 작성된 DXF가 남을 수 있습니다.

## 설정

공통 기본값은 `src/config.json`에 있습니다. **PC마다 다른 값은 `src/config.local.json`에 적습니다.**
이 파일은 git에 올라가지 않고, `config.json` 위에 덮어써집니다.

```json
{
    "cad": { "type": "BRICSCAD" },
    "output": { "directory": "D:\\work\\arcaid-cad\\output" }
}
```

주요 설정 항목:

- `cad.type`: 위 표의 CAD 종류
- `cad.startup_wait_time`: CAD를 새로 띄울 때 기다리는 시간(초)
- `cad.command_delay`: 일부 명령 뒤에 기다리는 시간(초)
- `cad.command_timeout`: `send_command`, `run_lisp`가 명령 완료를 기다리는 최대 시간(초, 기본 10)
- `output.directory`: 도면 저장 기본 폴더. 상대 경로는 MCP를 실행한 위치 기준이라, 절대 경로로 적는 것을 권장합니다.
- `work.directory`: 스냅샷(DXF 사본)과 읽기 결과를 두는 작업 폴더(기본 `C:\arcaid\work`, 한글·공백 없는 로컬 경로 권장). 사본은 `snapshots\` 아래에 쌓입니다.
- `server.transport`: `stdio`(기본) 또는 `streamable-http`

## Claude Code에 등록

경로는 실제 설치 위치로 바꿉니다. 등록 이름은 모두 `arcaid-cad`로 통일합니다.

```powershell
claude mcp add arcaid-cad -s user -- "C:\path\to\arcaid-cad\.venv\Scripts\python.exe" "C:\path\to\arcaid-cad\src\server.py"
```

등록한 뒤 Claude Code에서 `/mcp`로 연결 상태를 확인합니다. 도구 이름은 `mcp__arcaid-cad__draw_line`처럼 보입니다.

## 도구

**그리기**
- `draw_line`: 선
- `draw_circle`: 원
- `draw_arc`: 호
- `draw_ellipse`: 타원
- `draw_polyline`: 폴리선
- `draw_rectangle`: 사각형
- `draw_text`: 문자
- `draw_hatch`: 해치(경계 폴리선 포함). GstarCAD는 COM 해치가 화면에 그려지지 않아 명령행 `-HATCH`로 만듭니다
- `add_dimension`: 선형 치수(`textheight`를 주지 않으면 치수 스타일 값을 따름)

**조회**
- `list_layers`: 레이어 목록
- `list_entities`: 모형 공간 객체 목록(편집에 쓰는 핸들 포함). 종류·레이어·색으로 거를 수 있음
- `get_entity_properties`: 핸들로 객체 상세 정보 조회
- `screenshot`: CAD 창 캡처. 창을 앞으로 가져오지 않고, 다른 창에 가려져 있어도 CAD 화면만 찍습니다

**편집**
- `erase_entity`, `move_entity`, `rotate_entity`, `scale_entity`: 지우기, 이동, 회전, 크기 조정
- `copy_entity`, `mirror_entity`, `offset_entity`: 복사, 대칭, 간격 띄우기
- `array_linear_entity`, `array_polar_entity`: 직선 배열, 원형 배열
- `undo`, `redo`: 실행 취소, 다시 실행

**레이어·도면·블록**
- `new_from_template`: 회사 템플릿 DWG를 새 경로로 복사해 엽니다(이미 있으면 덮어쓰지 않음)
- `ensure_standard_layers`: 없는 표준 레이어를 표준 색·선종류로 만듭니다(보호된 경로, 같은 이름이 다른 정의면 멈춤)
- `set_current_layer`: 현재 레이어 바꾸기
- `open_drawing`, `close_drawing`, `save_drawing`: 열기, 닫기, 저장(`save_drawing`은 DWG만. DXF 사본은 `export_snapshot`)
- `list_drawings`, `switch_drawing`: 열린 도면 목록, 활성 도면 전환
- `create_block`, `insert_block`, `list_blocks`: 블록 만들기, 넣기, 목록

**도면 읽기**
- `export_snapshot`: 도면을 DXF 사본으로 내보냅니다(COM Export). 열린 문서의 이름·경로·미저장 상태는 바뀌지 않습니다. 닫힌 파일은 읽기 전용으로 열었다 닫고, 바뀌지 않았으면 이전 사본을 다시 씁니다.
- `read_drawing`: 사본을 읽어 레이어별 개수·비표준 레이어, 그리드 축과 이름(CEN 축선 + 축열 버블), 실명(ROM), 블록, Xref, 단위 경고를 돌려줍니다. 객체 전체 목록은 작업 폴더의 JSON 파일(`full_path`)에 있습니다.

**표준**
- `get_standard`: 회사 표준(레이어 용도·색·선종류, 문자 높이, 치수 스타일·간격, 블록)을 CAD 연결 없이 조회합니다. `scale`에 A3 축척 분모를 주면 그 축척의 실제 값으로 환산합니다.
- `check_standard`: 도면을 회사 표준과 대조합니다(도면 고르기는 `read_drawing`과 같음). 비표준 레이어·레이어 0, ByLayer가 아닌 색·선종류·선굵기, 문자 스타일·높이, 치수 스타일·DIMSCALE·재정의, 주석 객체, 표준 블록의 레이어·배율, 같은 이름 다른 정의, 도면 단위·LTSCALE을 봅니다. `scale`(A3 축척 분모)을 주지 않으면 1/100으로 가정합니다. 정답이 하나인 위반은 고칠 목록(`fixes`)으로 묶어 줍니다. `fix=true`면 활성 도면에서 그중 레이어·색·선종류·선굵기를 보호된 경로로 고칩니다(사람이 고친 엔진 객체는 `overwrite_manual=true`일 때만). 위반은 코드별 20건까지 돌려주고 전체는 작업 폴더의 JSON 파일(`full_path`)에 있습니다.

**선택·출력**
- `get_selection`: 사용자가 CAD에서 고른 객체의 핸들, 종류·레이어별 개수, 범위, 엔진 객체의 oid
- `plot`: 도곽(또는 창) 범위를 회사 CTB(`HEO(2020)_A3.ctb`)·`DWG To PDF.pc3`·A3·1/k로 PDF 출력합니다. 사용자 도면의 출력 설정을 바꾸지 않도록 DXF 사본을 열어 출력하고 닫습니다(약 10초). 정식 출력기·A3 용지를 확인하지 못하면 `trial=true`('시험 출력')

**넣기**
- `draw_batch`: 객체 명세(JSON, 반복·대칭·그리드 기준 배치 규칙)를 엔진이 표준 DXF로 만들어 활성 도면에 넣습니다. 보호된 경로입니다: 적용 직전 확인(단위 mm, 같은 이름 정의, 재실행 판정) → Undo 묶음 안에서 넣기 → 결과 확인 → 검사. 실패하면 되돌리고 확인해 `recovered`, 확인하지 못하면 `unverified`로 보고하고 이후 보호된 변경을 멈춥니다(`resume_after_unverified=true`로 해제). 같은 `op`를 다시 보내면 '이미 완료'로 건너뛰고, 같은 `op`에 다른 명세·일부만 남은 객체·사람이 고친 엔진 객체는 `conflict`로 멈춥니다.

**명령**
- `send_command`: CAD 명령행에 명령 문자열을 그대로 보냅니다. 명령이 끝나거나 입력을 기다릴 때까지 기다리고, 아직 입력을 기다리면 `still_active=true`를 돌려줍니다.
- `cancel_command`: 진행 중인 명령을 ESC처럼 취소합니다. 대화상자나 문자 편집기가 열려 CAD가 응답하지 않을 때는 CAD 창에 키보드 ESC를 보냅니다.
- `run_lisp`: AutoLISP 식을 실행하고 결과 값을 돌려받습니다(예: `(getvar "CLAYER")`).

## 동작 원칙

- **활성 도면에 작업합니다.** 도구를 호출할 때마다 CAD의 활성 도면을 확인하므로, CAD에서 탭을 바꾸면 그 도면에 작업합니다.
- **엔진이 넣은 객체에는 태그(XDATA `ARCAID`: op·oid·path·ver·in·state)가 붙습니다.** 태그로 재실행과 사람의 수정을 구분합니다. 저수준 그리기·편집 도구와 `send_command`·`run_lisp`는 보호되지 않는 경로라, 태그가 붙은 객체를 고치면 다음 `draw_batch`에서 충돌로 잡힙니다.
- **조회·편집 도구는 실행 중인 CAD에 자동으로 붙습니다.** 단 `undo`, `redo`, `close_drawing`, `save_drawing`은 연결 전에는 실행하지 않습니다. 사용자가 손으로 한 작업을 되돌리거나 열린 도면을 닫지 않기 위해서입니다.

- **외부 반환 필터(D8)**: 모든 툴 결과는 AI로 돌아가기 전에 `config/egress.yaml` 정책을 거칩니다. 지금 정책은 A안(제한 없음, 2026-10-14 결정)이라 `run_lisp` 결과 안의 파일 경로만 `[경로]`로 가립니다. 항목을 `deny`로 바꾸면 그 값이 `[비공개]`로 나가고 원본은 작업 폴더 `egress\`에 남습니다.
- **저수준 툴은 보호되지 않습니다.** 그리기·편집·`send_command`·`run_lisp` 등 25개 툴 설명문에 표시돼 있습니다.

## 구조

```
CAD-MCP/
├── CLAUDE.md              # Claude Code 작업 안내(문서 위치, 규칙, 테스트)
├── pyproject.toml
├── requirements.txt
├── docs/                  # 로드맵·구조 결정·0단계 실행 계획 사본(원본은 Claude Docs)
├── src/
│   ├── server.py          # MCP 서버와 도구 정의 (FastMCP)
│   ├── cli.py             # CAD 없이 read·check·build 실행(argparse)
│   ├── egress.py          # MCP·CLI 공용 외부 반환 정책·필터
│   ├── cad_controller.py  # COM 계층: 전용 COM 스레드, 재시도, 그리기·조회·편집
│   ├── colors.py          # 색 인자 해석(한국어·영어·중국어 색 이름 → ACI)
│   ├── config.py          # 설정 로더 (config.json + config.local.json)
│   ├── config.json        # 공통 기본 설정
│   ├── models.py          # 구조화 출력 모델
│   └── engine/            # CAD 없이 도는 엔진(읽기·생성·검사, ops.py 공용 출력). COM·MCP 모듈 금지
├── standards/
│   └── heo-2026/          # 회사 표준 프로파일(YAML)과 template.dxf(표준값의 단일 원본)
├── tests/                 # engine·cli(어디서나), mcp(Windows), cad(CAD 실행 중)
├── skills/
│   └── cad-drawing-standard/  # 회사 도면 표준 스킬 (SKILL.md, references/, assets/회사표준_템플릿.dwg)
└── tools/
    └── template/          # 표준매뉴얼 DWG에서 템플릿을 다시 만드는 스크립트
```

## 개발

```powershell
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\python.exe -m pytest                       # 엔진·단위·MCP 툴 목록
$env:ARCAID_CAD_TESTS="1"; .venv\Scripts\python.exe -m pytest tests/cad   # CAD 연동(GstarCAD 실행 중)
```

- `src/engine/`은 CAD 없이 돌아야 합니다(ezdxf·pydantic·PyYAML만 사용). GitHub Actions가 Linux에서 엔진·CLI 시험과 실제 CLI 명령을, Windows에서 MCP·CLI·엔진 시험을 돌립니다.
- 엔진은 도면 읽기(`read_drawing`)·표준 검사(`check_standard`)와 함께, 배치 규칙(반복·대칭·그리드 기준)을 담은 객체 명세로 표준에 맞는 DXF를 쓰는 `build_dxf`(`src/engine/write.py`)를 갖고 있습니다. 열린 도면에 넣는 MCP 툴(`draw_batch`)은 다음 작업에서 붙습니다.
- 진행 계획과 구조 결정은 `docs/`와 `CLAUDE.md`를 봅니다.

## 회사 도면 표준 스킬

`skills/cad-drawing-standard`는 회사(HEO architects) 도면작성 표준에 맞춰 이 MCP로 도면을 그리게 하는 Claude 스킬입니다.
스킬이 쓰는 템플릿 DWG(표준 레이어, 문자·치수 스타일, 블록)는 `assets/`에 함께 들어 있습니다.

**Claude Code에 설치**: 저장소 폴더를 사용자 스킬 폴더에 정션으로 연결합니다. 저장소를 고치면 바로 반영됩니다.

```bat
mklink /J "%USERPROFILE%\.claude\skills\cad-drawing-standard" "<저장소>\skills\cad-drawing-standard"
```

**claude.ai에 올리기**: `.venv\Scripts\python.exe tools/skills/package_skill.py -o ../cad-drawing-standard.zip`으로 만듭니다. 템플릿·상대 링크·한글 파일명·zip 무결성을 확인하고 백업·임시 파일을 제외합니다. 만들어진 zip을 claude.ai의 스킬 설정에서 올립니다. 저장소에서 스킬을 고친 뒤에는 다시 올려야 합니다.

스킬은 `get_standard` → `export_snapshot`·`read_drawing` → 객체 명세 → `draw_batch` → `check_standard` → 보고 순서입니다. 실행 가능한 합성 예제는 `skills/cad-drawing-standard/references/examples/synthetic-plan.json`에 있습니다. 스킬 zip은 서버·CLI 설치본을 포함하지 않습니다.

**템플릿 다시 만들기**(GstarCAD 실행 중, 저장소 `.venv`의 파이썬):

```bat
python tools\template\build_template.py "<표준매뉴얼.dwg>" report.json
python tools\template\add_standard_blocks.py
python tools\template\fix_block_origins.py
python tools\template\convert_dimstyles.py
python tools\template\set_plot_setup.py
python tools\template\export_template_dxf.py
```

- `convert_dimstyles.py`: DIM330·DIM270을 비주석으로, 스타일 DIMSCALE을 50(A3 1/100)으로 맞춥니다.
- `set_plot_setup.py`: 모든 레이아웃의 출력 설정을 회사 CTB `HEO(2020)_A3.ctb`·`DWG To PDF.pc3`·A3로 맞춥니다(출력 범위·축척은 그대로).
- `export_template_dxf.py`: `standards/heo-2026/template.dxf`(R2013)를 만들고 템플릿 DWG·DXF 해시를 `profile.yaml`에 적습니다. 템플릿을 고친 뒤에는 꼭 다시 실행합니다(`--check`는 CAD 없이 해시만 비교).

표준매뉴얼 원본 DWG는 저장소에 넣지 않습니다.

## 라이선스

MIT. 원저작권 표시는 `LICENSE`에 유지합니다.
