# apply_changes 변경 명세

형식의 원본은 서버 저장소 `src/engine/changes.py` docstring이다. 스킬 zip에서는 이 문서와 [변경 예제 JSON](examples/synthetic-change.json)을 사용한다. 표준값은 `get_standard`로 조회한다. 명세의 표시 속성에 임의 표준 수치를 넣지 않는다.

## 요청과 필드

`apply_changes(spec=<JSON 객체 또는 JSON 문자열>, scale=k)`로 보낸다. spec 문자열은 파일 경로가 아니다. `scale`은 표준 검사에 쓰는 A3 축척 분모이며 spec 안에 넣지 않는다.

| 필드 | 의미 |
|---|---|
| `schema_version` | 명세 버전. 현재 `1` |
| `standard` | `get_standard`가 반환한 `id@version` |
| `op` | 논리적 변경 작업의 고유 ID. 영문·숫자·점·밑줄·하이픈으로 된 1~64자 |
| `target.snapshot_id` | 대상을 읽은 기준 스냅샷 ID. 필수 |
| `target.drawing_id` | 기준 사본의 도면 ID. 선택. 활성 도면·사본 출처 대조에 사용 |
| `changes` | 변경 목록. 하나 이상 |
| `changes[].id` | 요청 안에서 고유한 변경 ID |
| `changes[].select` | 아래 세 방식 중 하나로 고른 대상 |
| `changes[].action` | `move`, `delete`, `set` 중 하나 |
| `changes[].delta` | `move` 전용. 유한한 `[dx,dy]`, mm·모형 공간 WCS |
| `changes[].props` | `set` 전용. 아래 속성 중 하나 이상 |

모르는 필드와 `null` 속성값은 받지 않는다. `delete`에는 `delta`·`props`를 넣지 않는다.

## 대상 선택과 핸들 출처

| select | 고르는 기준 |
|---|---|
| `{"handles": ["A1", "F2"]}` | 16진 핸들 목록. 대문자로 정규화 |
| `{"oids": ["skill-synthetic-plan:wall/WAL1/1/outer"]}` | 엔진 객체의 oid 목록 |
| `{"op": "skill-synthetic-plan", "path_prefix": "synthetic/room/102/"}` | 같은 생성 op이며 의미 path가 접두사로 시작하는 모든 객체 |

oid와 의미 path는 다르다. [합성 작성 예제](examples/synthetic-plan.json)의 같은 벽은 다음 두 값을 갖는다.

| 구분 | 실제 값 |
|---|---|
| oid | `skill-synthetic-plan:wall/WAL1/1/outer` |
| path | `synthetic/room` |

`oids`는 oid 기준, `path_prefix`는 path 기준이다. 생성 op는 변경 요청의 새 `op`와 구분한다.

합쳐진 벽 고리(LWPOLYLINE)는 `oids`로 골라 옮기거나 지울 수 있다. 고리는 공유벽을 포함한 외곽 단위이므로 실 하나의 벽만 따로 옮기지 못한다. wall.hatch는 apply_changes 지원 밖이다.

- `get_selection`은 `handles`, `engine_objects[].oid`, 자체 `ids.snapshot_id`를 반환한다. 성공한 선택 결과의 스냅샷을 기준으로 써도 된다.
- `get_selection.truncated=true`면 일부만 바꾸지 않는다. limit을 올려 다시 읽거나 엔진 객체 묶음을 생성 `op` + `path_prefix`로 확인한다. 요청 범위 전체를 확인한 뒤 명세를 만든다.
- `export_snapshot` → `read_drawing(snapshot_id=...)`으로 도면을 읽을 수 있다. `read_drawing`에는 limit이 없다. 일반 객체 핸들은 `full_path` JSON의 `entities[].handle`에서 얻는다. 이 목록에는 oid가 없다. 엔진 객체 묶음은 생성 `op` + `path_prefix`, 개별 oid는 `get_selection`을 쓴다.

각 선택은 하나 이상 맞아야 한다. 한 객체는 요청 전체에서 변경 하나만 받는다. 같은 객체의 이동과 내용 변경은 두 요청으로 나눈다. 첫 요청 성공 뒤 새 기준 스냅샷·새 op로 두 번째 요청을 만든다. 두 요청 전체는 원자적이지 않음을 보고한다.

## 동작과 속성

| action | 지정할 값 |
|---|---|
| `move` | `delta`. Z·폭·bulge·정렬·반지름·각도·두께를 보존하고 위치를 옮김 |
| `delete` | 추가 필드 없음. INSERT는 종속 속성도 삭제 |
| `set` | `props`. 지정하지 않은 속성은 보존 |

| props | 허용 값 |
|---|---|
| `layer` | 도면에 존재하는 레이어 이름 |
| `color` | ACI 1~256 또는 `{"rgb": [r,g,b]}`. RGB 각 성분은 0~255 정수 |
| `linetype` | 도면에 존재하는 선종류 이름 |
| `lineweight` | DXF 허용 선굵기 정수 또는 상속값 `-3`·`-2`·`-1` |
| `text` | TEXT의 내용. 빈 문자열은 내용 지우기이며 객체 삭제가 아님 |
| `attribs` | INSERT의 `{태그: 문자열}`. 빈 값은 해당 속성 내용 지우기 |

속성 태그는 대소문자를 가리지 않는다. 없는 태그·상수 태그·중복 태그는 거부한다. 정렬 문자·속성은 내용을 바꾸면 CAD가 삽입점을 다시 계산한다. 트루컬러는 CAD가 가까운 ACI를 함께 기록한다. 이 ACI는 파생값이며 RGB를 기준으로 확인한다.

## 지원과 요청 전체 거부

모형 공간 LINE·LWPOLYLINE·CIRCLE·ARC·TEXT·INSERT, extrusion=(0,0,1). 원·호의 set은 공통 표시 속성(레이어·색·선종류·선굵기)만. 종속 속성의 extrusion도 `(0,0,1)`이어야 한다. 펼친 대상 상한은 1,000개다.

원·호 이동은 3D 중심에 `[dx,dy,0]`을 더하고 반지름·각도·두께를 보존한다. 비교 허용오차는 0.01mm이며 중심의 3D 거리, 반지름·두께 차, 호 양 끝점의 WCS 위치 차로 확인한다. 호의 각도는 도 단위 오차로 판정하지 않으며 0/360 경계를 같은 위치로 다룬다. 음수·경사 extrusion은 거부한다.

회전·대칭·배율, 해치·치수·MTEXT, 블록 정의 내부, 속성 단독 핸들은 지원하지 않는다. 내장 MTEXT가 있는 여러 줄 속성도 거부한다. 미지원으로 보고하고 저수준 도구로 우회하지 않는다.

반응자·그룹 구성원·연관 해치 경계, 잠김·동결·꺼짐 레이어도 거부한다. 하나라도 위반이면 요청 전체를 거부한다. 같은 oid가 복사돼 둘 이상이면 선택 방식과 관계없이 거부한다.

`op` + `path_prefix`가 해치까지 고르면 요청 전체가 거부된다. 요청한 대상이 모두 지원 객체일 때만 `oids`로 좁힌다. '객실 전체 이동'에 해치가 섞였다면 지원 객체 일부만 옮겨 완료 처리하지 않는다. 미지원 범위를 보고하고 사용자 판단을 받는다. 연관 해치 경계는 해치를 제외해도 거부된다.

## 거부 코드와 다음 행동

| 코드 | 의미 | 할 일 |
|---|---|---|
| `CHG_SCHEMA` | JSON·필드·선택·동작 형식이 맞지 않음 | 위반 경로를 보고 명세를 고침 |
| `CHG_STANDARD` | 서버와 명세의 표준이 다름 | `get_standard` 결과를 확인 |
| `CHG_TARGET` | 기준 스냅샷 ID가 다름 | 보관한 기준 ID와 명세를 대조 |
| `CHG_SELECT` | 일치하는 대상이 없거나 모형 공간 밖임 | 기준 사본·선택 출처·범위를 다시 확인 |
| `CHG_DUPLICATE` | 변경 ID·대상 중복 또는 복사된 oid | 중복 선택을 고침. 복사된 oid는 사용자에게 보고 |
| `CHG_KIND` | 미지원 객체·extrusion 또는 객체와 속성이 맞지 않음 | 미지원 범위를 보고. 저수준 우회 금지 |
| `CHG_RELATION` | 반응자·그룹·연관 해치 경계 관계가 있음 | 관계와 제한을 보고. 해치만 빼는 우회 금지 |
| `CHG_LAYER` | 없거나 잠김·동결·꺼짐 레이어 | 레이어 상태를 사용자에게 알리고 해결 후 진행 |
| `CHG_LINETYPE` | 선종류가 도면에 없음 | 조회한 표준·도면 정의를 확인 |
| `CHG_ATTRIB` | 없는·상수·중복 속성 태그 | 블록의 태그를 확인하고 명세를 고침 |
| `CHG_LIMIT` | 펼친 대상이 상한을 넘음 | 요청 범위를 확인. 나누면 요청 사이 원자성 없음을 보고 |

## 판정과 재전송

| 판정 | 기준과 행동 |
|---|---|
| `apply` | 기록이 없고 모든 대상이 기준 before와 같음. 적용 직전 관계·레이어·속성도 다시 검사 |
| `skip` | 같은 입력·대상·동작의 기록이 있고 현재 상태가 after·기록 해시와 맞음. 삭제는 실제 부재도 확인 |
| `conflict` | 같은 op의 다른 입력, 기준 뒤 변경, 수동 수정, 중복 oid, 기록 손상 등. 멈추고 보고 |

이미 완료된 요청은 `status=success` + `meta.decision=skip`이다. `conflict`를 새 op·삭제 재작도·저수준 도구로 우회하지 않는다. `overwrite_manual=true`는 기준 스냅샷에 이미 있던 수동 수정만 허용한다. 해당 변경에 대한 사용자 명시 승인 뒤에만 쓴다. 기준 뒤 변경은 허용하지 않는다.

입력 해시에 `target.snapshot_id`도 포함된다. 재전송은 원래 기준 ID를 포함한 명세 그대로 한다. 결과의 `ids.snapshot_id`는 적용 직전 사본이며 원래 기준이 아니다. 다음 논리적 변경은 새 기준 스냅샷·새 op로 한다.

기록은 적용·사후 확인이 끝난 뒤 Undo 묶음 밖에서 마지막에 쓴다. 기록 쓰기·재읽기 실패도 `unverified`다. 복귀 또는 적용 기록을 확인하지 못했으므로 자동 재전송·저장 등 후속 변경을 중단한다. 사용자 확인·명시적 재개 뒤에만 `resume_after_unverified=true`를 쓴다.

`applied`는 동작별 개수다. `unchecked`는 변경 대상(종속 속성 포함)에 해당하는 미검사 항목만 담고(전역 항목·검사 실패·잘림은 유지), `new_violations`는 미검사가 있어도 검사한 규칙의 대상 위반 차분을 담으며 전체 도면 검사가 아니다. `unchecked`가 있으면 완료로 보고하지 않는다. `warnings`와 `ids.request_id`(op)·도면·적용 직전 스냅샷을 보고한다. 원래 기준 ID도 구분한다. 성공 뒤 `check_standard(scale=k)` → DWG 저장·보고는 [스킬 절차](../SKILL.md)를 따른다.

## 변경 예제 실행

1. [합성 작성 예제](examples/synthetic-plan.json)를 새 시험 DWG에 `draw_batch`로 넣는다.
2. 그린 뒤 `export_snapshot`으로 새 기준을 얻는다. [변경 예제](examples/synthetic-change.json)의 `target.snapshot_id` 자리표시 `"<export_snapshot의 snapshot_id>"`를 그 값으로 한 번 채운다.
3. 예제는 실명 101 위치 이동, 실명 102 내용 변경, X3 버블 속성 변경, 실명 104 삭제다. `oids`로 개별 객체를 골라 벽·해치는 보존한다. 이는 객실 전체 이동 예제가 아니다.
4. `apply_changes(spec=..., scale=k)` 성공과 미검사·위반 차분을 확인한다. 같은 명세를 그대로 재전송하면 `status=success` + `meta.decision=skip`이어야 한다. 재전송 전에 기준 ID를 다시 채우지 않는다.
5. `check_standard(scale=k)`로 전체 도면을 검사한 뒤 DWG로 저장하고 보고한다.
