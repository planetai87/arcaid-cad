# T0 사전 검증 실험 (제품 코드 아님)

0단계 실행 계획 T0의 실험 스크립트다. 결과는 `docs/spike-report.md`에 있다. 여기 코드는 제품 코드로 옮기지 않는다.

## 실행

GstarCAD가 실행 중이어야 한다. 실험하는 동안 CAD를 쓰지 않는다.

```powershell
cd spikes
$env:PYTHONIOENCODING = "utf-8"
..\.venv\Scripts\python.exe make_synthetic.py   # 먼저: 합성 시험 도면
..\.venv\Scripts\python.exe e1_dimstyle.py      # template.dxf도 만든다(E4 이후의 바탕)
..\.venv\Scripts\python.exe gen_dxf.py gen.dxf
..\.venv\Scripts\python.exe gen_dxf.py gen100.dxf --op e4n --lines 100 --no-basic
..\.venv\Scripts\python.exe e2_export.py        # 이후 e4 ~ e9
```

작업 폴더는 `C:\arcaid\work\spike`(`ARCAID_SPIKE_DIR`로 바꿀 수 있다). 시작 전에 회사 템플릿을 `template.dwg`로 복사해 둔다.

## 안전 규칙 (`_harness.py`)

- 실행 중인 CAD에 붙되, 작업 폴더 아래 시험 도면만 고친다(guard).
- 끝나면 진행 중 명령 취소 → 시험 도면 닫기(저장 안 함) → 바꾼 앱 변수 복원 → 사용자 도면 재활성화 → 사용자 도면 저장 상태가 그대로인지 기록.
- COM은 스크립트 메인 스레드에서 직접 부른다. 제품의 `COMExecutor`에 본문을 넘기면 '바쁨' 오류 때 본문 전체가 다시 실행될 수 있다.

## 파일

| 파일 | 내용 |
|---|---|
| `_harness.py`, `_inspect.py` | 공통 하네스, DXF·열린 도면 요약과 비교 |
| `e0_harness_check.py` | 하네스 자체 시험 |
| `make_synthetic.py`, `_shot.py` | 합성 시험 도면, 화면 캡처 |
| `e1_dimstyle.py` | 치수 스타일 주석 여부와 비주석 전환 |
| `e2_export.py` | 내보내기 후보 비교와 핸들 일치(E2·E3) |
| `gen_dxf.py` | 생성 DXF(태그 포함) |
| `e4_insert.py`, `_hatch_probe.py`, `_hatch_variants.py` | 삽입 방법 비교, 섬 해치 |
| `e5_same_name.py` | 같은 이름·다른 정의 |
| `e6_undo.py`, `e6b_undo_fallback.py` | Undo 복구와 태그 삭제 대안 |
| `e7_xdata.py` | XDATA 조회 속도, 최소 DXF 삽입 속도 |
| `e8_units_paths.py`, `_lisp_path_probe.py` | 단위, 한글·공백 경로 |
| `e9_plot.py`, `e9b_plot_variants.py`, `e9_compare.py` | PDF 출력, 출력 범위 방식, 손 출력 비교 |
