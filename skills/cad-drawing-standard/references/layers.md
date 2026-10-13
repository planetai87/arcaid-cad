# 표준 레이어 (2026 도면작성 표준매뉴얼 'LAYER 체계')

규칙(매뉴얼 원문 요약):
- 레이어명은 문자·숫자 3자리가 원칙이고, 중복명은 숫자 한 자리로 구분한다(1, 2…).
- 작업자는 레이어를 임의로 지정하거나 바꿀 수 없다. 구분이 필요하면 팀 협의를 거쳐 정의된 레이어 안에서 숫자를 붙여 구분한다.
- 색상 = 출력 선굵기(아래 '색상별 선굵기'). 객체 색은 항상 ByLayer.

| 레이어 | 색(ACI) | 선종류 | 용도 |
|---|---|---|---|
| CEN | 1 RED | CEN | 내부벽체 기준선, 중심선(축선) — 템플릿에서 '출력 안 함' |
| Defpoints | 8 | Continuous | 출력 시 보이지 않는 참고선 |
| CON | 2 YELLOW | Continuous | 철근콘크리트 구조체(RC 벽·기둥·슬래브 단면, 배치도 건물 외곽) |
| CIL | 6 MAGENTA | Continuous | 천장재 줄눈 |
| CIL1 | 7 WHITE | Continuous | 커튼박스, 천장 단차 |
| CIR | 4 CYAN | Continuous | 승강기, 에스컬레이터, 무빙워크, 계단 |
| LIM | 2 YELLOW | HID | 대지경계선, 각종 구획선(방화구획 등), 별도공사 |
| DIM | 7 WHITE | Continuous | 축열(축 버블·축 번호), 외부 치수선 |
| DIM1 | 7 WHITE | Continuous | 내부 치수 |
| DOR | 4 CYAN | Continuous | 문 평면, 문틀 |
| ELE | 7 WHITE | Continuous | 내외부 입면 |
| FIN | 6 MAGENTA | Continuous | 내외부 마감 |
| FUR | 5 BLUE | HID | 가구, 집기, 설치물(금회 공사 외는 점선) |
| HAT | 8 GRAY | Continuous | 재료 표현 해치 |
| HAT1 | 9 GRAY | Continuous | 영역 구분 및 기타 해치 |
| HID | 8 GRAY | HID | 가상선, 상부 벽체선 |
| INS | 8 GRAY | Continuous | 단열재 |
| LAN | 9 GRAY | Continuous | 조경, 도로경계석 |
| LEV | 3 GREEN | Continuous | 평·입·단면 레벨 기호와 문자, 단차 표시 |
| LGT | 3 GREEN | Continuous | 조명, 천장 설비기구 |
| INT | 8 GRAY | Continuous | 별도 인테리어선 |
| PAK | 9 GRAY | Continuous | 주차구획선 |
| PAK1 | 7 WHITE | Continuous | 주차대수 표시, 주차 관련 표시(출구·일방통행 등) |
| PAT | 9 GRAY | Continuous | 내외부 바닥 패턴 (템플릿 이름은 소문자 `pat`, 같은 레이어) |
| REM | 3 GREEN | Continuous | 주기(NOTES)란 내용 |
| ROM | 3 GREEN | Continuous | 실명 박스와 실명 문자 |
| SHT | 7 WHITE | HID | 셔터, 자동방화셔터 |
| STL | 3 GREEN | Continuous | H형강 등 구조용 철재 |
| STL1 | 4 CYAN | Continuous | 비구조용 잡철물 |
| SPR | 2 YELLOW | Continuous | 스프링클러 |
| SYM | 3 GREEN | Continuous | 입면·단면·전개 기호와 그 문자 |
| SYM1 | 7 WHITE | Continuous | 기타 기호(방위표, 주출입구) |
| TIT | 7 WHITE | Continuous | 도면 양식(도곽) |
| TIT1 | 3 GREEN | Continuous | 도면 양식 안의 문자, 타이틀 문자 |
| TXT | 3 GREEN | Continuous | 도면 내 일반 문자 |
| TXT1 | 7 WHITE | Continuous | 각종 지시선과 그 문자 |
| TOL | 4 CYAN | Continuous | 화장실 대·소변기, 세면기 |
| WAL | 3 GREEN | Continuous | 벽돌벽, 보강블록벽 |
| WAL1 | 7 WHITE | Continuous | 건식벽, 내부 칸막이 |
| WIN | 4 CYAN | Continuous | 창문, 유리문, 커튼월 |

(매뉴얼 표의 HAD9/TID9/TXD9는 HAT1/TIT1/TXT1의 오기로 확인됨. 색상은 매뉴얼에 글로 적힌 표가 기준이다.)

## 색상별 출력 선굵기 (mm)

| 색 | A1 | A3 |
|---|---|---|
| 1 RED | 0.13 | 0.08 |
| 2 YELLOW | 0.50 | 0.30 |
| 3 GREEN | 0.35 | 0.20 |
| 4 CYAN | 0.18 | 0.10 |
| 5 BLUE | 0.15 | 0.10 |
| 6 MAGENTA | 0.15 | 0.15 |
| 7 WHITE | 0.25 | 0.18 |
| 8 GRAY | 0.10 | 0.08 |
| 9 GRAY | 0.13 | 0.10 |

굵게 보여야 할 선(구조 단면선, 입면 외곽선, 대지경계선)이 YELLOW(2) 레이어인 이유가 이것이다.

## 선의 종류 (매뉴얼 '선의 종류')

| 선 | 색 | 용도 |
|---|---|---|
| 실선(굵게, 선굵기 60) | — | 입면·단면 지반선 |
| 실선 | 2 | 건물 입면 외곽선, 구조 단면선 → CON/ELE |
| 점선 | 9 | 보이지 않는 부분(상부 벽체선) → HID |
| 일점쇄선 | 1 | 중심선 → CEN |
| 일점쇄선 | 2 | 구획 지정선(방화구획선) → LIM, 그린 직후 `CHPROP`로 `CENTER` |
| 이점쇄선 | 2 | 대지경계선 → LIM, 그린 직후 `CHPROP`로 `PHANTOM` |
| 절단선 | 7 | 계단 등 작은 면, 입단면·평면 큰 면 절단선 |
| 기타 | 9 | 가상선(하부 개구부), 무근콘크리트 와이어메시 |

LIM 레이어 기본 선종류는 HID다. 대지경계선과 방화구획선은 위처럼 객체 선종류만 바꾼다(SKILL.md의 CHPROP 명령).
