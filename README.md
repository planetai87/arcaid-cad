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
- `add_dimension`: 선형 치수

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
- `create_layer`, `set_current_layer`: 레이어 만들기, 현재 레이어 바꾸기
- `new_drawing`, `open_drawing`, `close_drawing`, `save_drawing`: 새 도면, 열기, 닫기, 저장
- `list_drawings`, `switch_drawing`: 열린 도면 목록, 활성 도면 전환
- `create_block`, `insert_block`, `list_blocks`: 블록 만들기, 넣기, 목록

**명령**
- `send_command`: CAD 명령행에 명령 문자열을 그대로 보냅니다. 명령이 끝나거나 입력을 기다릴 때까지 기다리고, 아직 입력을 기다리면 `still_active=true`를 돌려줍니다.
- `cancel_command`: 진행 중인 명령을 ESC처럼 취소합니다. 대화상자나 문자 편집기가 열려 CAD가 응답하지 않을 때는 CAD 창에 키보드 ESC를 보냅니다.
- `run_lisp`: AutoLISP 식을 실행하고 결과 값을 돌려받습니다(예: `(getvar "CLAYER")`).
- `process_command`: 자연어 명령 해석(옛 인터페이스)

## 동작 원칙

- **활성 도면에 작업합니다.** 도구를 호출할 때마다 CAD의 활성 도면을 확인하므로, CAD에서 탭을 바꾸면 그 도면에 작업합니다.
- **조회·편집 도구는 실행 중인 CAD에 자동으로 붙습니다.** 단 `undo`, `redo`, `close_drawing`, `save_drawing`은 연결 전에는 실행하지 않습니다. 사용자가 손으로 한 작업을 되돌리거나 열린 도면을 닫지 않기 위해서입니다.

## 구조

```
CAD-MCP/
├── pyproject.toml
├── requirements.txt
└── src/
    ├── server.py          # MCP 서버와 도구 정의 (FastMCP)
    ├── cad_controller.py  # COM 계층: 전용 COM 스레드, 재시도, 그리기·조회·편집
    ├── config.py          # 설정 로더 (config.json + config.local.json)
    ├── config.json        # 공통 기본 설정
    ├── models.py          # 구조화 출력 모델
    └── nlp_processor.py   # 자연어 명령 해석(옛 인터페이스)
```

## 라이선스

MIT. 원저작권 표시는 `LICENSE`에 유지합니다.
