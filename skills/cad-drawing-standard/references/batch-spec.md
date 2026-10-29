# draw_batch 명세 작성

형식의 원본은 서버 저장소 `src/engine/spec.py`다. 스킬 zip에서는 이 문서와 [합성 예제 JSON](examples/synthetic-plan.json)을 사용한다. 예제는 프로파일 버전과 리허설 입력을 고정한 시험용 도면이다. 실제 도면에 그대로 넣지 말고 `get_standard`·도면 판독 결과로 표준·축척·대상·간격을 정한다.

## 요청과 좌표

`draw_batch(spec=<JSON 객체 또는 JSON 문자열>)`로 보낸다. spec 문자열은 파일 경로가 아니다.

| 필드 | 의미 |
|---|---|
| `schema_version` | 명세 버전. 현재 `1` |
| `standard` | `get_standard`가 반환한 `id@version` |
| `op` | 논리적 추가 작업의 고유 ID. 동일 요청의 재전송에서는 유지 |
| `units`, `scale` | mm, A3 축척 분모 k |
| `target` | `drawing_id`, `snapshot_id`. 도면 ID는 활성 도면 대조, 스냅샷 ID는 읽은 기준 기록 |
| `grid` | X·Y별 축 이름과 실제 mm 좌표 |
| `items` | 객체 또는 group 목록 |

좌표는 `[x,y]`다. 최상위 객체와 최상위 group의 `place.at`은 숫자 또는 `"X2+300"` 같은 축 참조를 받는다. x 자리에는 X축, y 자리에는 Y축을 쓴다. 그룹 내부 객체와 중첩 배치는 **숫자 지역 좌표**다. 축 참조 문자열에는 단일 축과 수치 오프셋만 쓴다. 수식 평가·변수·함수 호출은 지원하지 않는다.

`target.snapshot_id`만으로 읽은 뒤 도면 전체가 바뀌지 않았음을 보장하지 않는다. 새 스냅샷을 기준으로 적용 직전 엔진 확인이 실행된다. 도면 변경을 확인했다면 다시 읽어 배치 판단을 갱신한다.

## 객체 계약

| kind | 지정할 값 |
|---|---|
| `line` | layer, start, end |
| `polyline` | layer, points, 선택 bulges·closed |
| `wall` | layer, parts, 선택 openings·hatch |
| `circle` | layer, center, radius |
| `arc` | layer, center, radius, start_angle, end_angle |
| `text` | layer, role, content, at, 선택 align·rotation |
| `block` | name, at, 선택 rotation·attribs |
| `hatch` | layer, pattern, boundary, 선택 holes·pattern_scale·pattern_angle |
| `dim` | style, p1, p2, direction, line_at |
| `dim_chain` | style, points, direction, line_at |
| `group` | items, place |

모든 객체에 식별용 `id`, 의미 경로 `path`를 줄 수 있다. 객체 색·선굵기는 ByLayer다. 문자 높이·스타일은 role, 블록 레이어·배율은 name, 치수 레이어·DIMSCALE은 style·scale로 엔진이 결정한다. 추가 필드로 임의 덮어쓰기하면 거부된다. 허용된 `linetype` 예외는 LIM의 PHANTOM·CENTER이며 `get_standard(section="layers")`로 확인한다.

문자 정렬은 L/C/R, ML/MC/MR, TL/TC/TR, BL/BC/BR을 받는다. role·레이어는 [생성표](annotation.md)대로 조합한다. 현재 PAK1·TXT1에는 허용 문자 role이 없다. 블록 attribs는 `get_standard(section="blocks", name=...)`의 **속성 태그를 키로** 사용한다. 임의로 이름을 번역하거나 배열 순서로 값을 넣지 않는다.

해치는 바깥 `boundary`와 섬별 `holes`의 닫힌 좌표 목록을 준다. 선분 끝점을 첫 점에 반복하지 않아도 된다. 지원 패턴명은 엔진이 검증한다. `dim.direction="x"`의 `line_at`은 y, `"y"`면 x다. 치수 문자열 재정의·임의 문자 높이는 현재 계약에 없다.

벽은 직교 다각형 조각 `parts`와 개구부 `openings`를 준다. 둘 중 하나 이상이 있어야 하며 문 group에는 openings만 둘 수 있다. 엔진이 같은 op·같은 레이어의 조각을 모두 합친 뒤 개구부를 뺀다(RC 기둥도 wall의 CON 조각이면 RC 벽과 합침). 다른 레이어와 op 사이는 합치지 않는다. 결과는 외곽·섬마다 닫힌 폴리선이고 oid는 `op:wall/레이어/번호/outer`, `…/hole1`이다. 번호는 영역 바깥 고리의 최소 x·최소 y 순서이며 path는 기여한 조각 경로의 공통 앞부분(없으면 `wall`)이다. 해치는 요청 시만 `wall.hatch={"layer":"HAT","pattern":"SOLID"}`로 넣으며 영역마다 `…/hatch` 하나가 같은 고리들을 쓴다. 선택 pattern_scale·pattern_angle은 일반 hatch와 같은 제약이고, 같은 레이어의 wall.hatch 설정은 생략 여부까지 모두 같아야 한다. 직교만 지원하며 방향이 다른 접합부는 합치지 않고 bbox 접촉·겹침 경고를 보고한다. 방향 허용오차는 1e-6도, 좌표 맞춤은 1e-3 mm(묶음 최솟값 기준), 압축 격자는 방향 묶음당 4,000,000칸까지이며 넘으면 벽 op를 나눈다.

벽 좌표는 합치는 중간에 반올림하지 않고 최종 고리만 소수 여섯 자리로 쓴다. 반복을 포함해 전개될 parts·openings 합계도 20,000개까지다(`SPEC_LIMIT`). 해치 패턴은 일반 hatch와 같은 group 회전을 따르고 떨어진 영역은 각자의 각도를 쓴다. 한 영역으로 합쳐진 조각들의 변환 후 패턴 각도가 충돌하면 `SPEC_WALL`이다(SOLID는 각도 차이를 무시).

## 반복·대칭·회전

`group.place`의 각 배치에 at, rotation, mirror, repeat, vars를 준다. `repeat={"count": ..., "step": [dx,dy]}`는 엔진이 펼친다. mirror x는 지역 x 부호, mirror y는 지역 y 부호를 뒤집는다. 순서는 **대칭 → 회전 → 이동(at + 반복 번호 × step)**이다. 각도는 도 단위다.

`vars={"no":{"start":101,"step":1}}`은 content·attribs 값·id·path의 `{no}`를 치환한다. `{i}`는 배치 안에서 영부터 시작하는 반복 번호다. 속성 블록은 대칭 그룹에 넣지 않는다. 예제처럼 실 그룹만 대칭하고 버블은 별도 그룹으로 반복한다.

## 예제 실행과 재전송

1. 새 합성 DWG를 `new_from_template`로 만든다. `export_snapshot`·`read_drawing` 후 예제 JSON의 `target`에 반환 ID를 넣는다.
2. 예제는 방 그룹을 반복·대칭하며 공유벽을 합친다(두 영역, 바깥 2개·섬 4개). 중앙 실명, 섬이 있는 바닥 해치, 번호 버블, 치수 체인과 전체 치수를 작성한다. 표준 간격이 필요한 실제 작업은 조회 결과로 좌표 오프셋을 정한다.
3. `draw_batch(spec=...)` → `check_standard(scale=k)` → 저장·보고한다. 같은 JSON을 다시 보내면 op를 유지하며 중복 생성 없이 건너뛰어야 한다.
4. 같은 op의 다른 입력, 일부 객체만 남음, 수동 변경은 충돌이다. 새 op나 저수준 수정으로 우회하지 않는다. 복귀 확인 불가·수동 변경 덮어쓰기는 SKILL.md의 사용자 확인 절차를 따른다.

CAD 없이 확인하려면 서버 저장소에서 다음 명령을 실행한다. `--strict` 검사 종료 코드와 미검사 목록까지 확인한다.

```powershell
arcaid build skills/cad-drawing-standard/references/examples/synthetic-plan.json -o output/skill-example.dxf
arcaid check output/skill-example.dxf --scale 100 --strict
arcaid read output/skill-example.dxf
```

CLI는 파일만 작성한다. 열린 도면 적용·재전송 보호 확인은 MCP `draw_batch`로 한다. `check_standard` 상세가 잘리면 violation_counts·full_path를 확인하며, 새 객체 위반 집계를 도면 전체 검사로 해석하지 않는다.

그린 뒤 기존 객체를 바꾸려면 [변경 명세와 예제 실행](change-spec.md)을 따른다.
