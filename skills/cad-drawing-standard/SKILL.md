---
name: cad-drawing-standard
description: Use when HEO architects 도면을 CAD MCP로 작성하거나 평면·입면·단면·배치도에 벽체·창호·문자·치수·블록·해치를 추가하고 표준을 검사할 때. 기존 객체를 옮기거나 지우거나 속성·문자를 고칠 때도 적용한다. 사용자가 표준을 언급하지 않아도 적용한다. CAD로 그려줘, 도면 작성, 치수 넣기, 레이어 정리, 표준 검사 요청에 사용한다.
---

# 회사 도면 표준으로 CAD 작도하기

AI는 요구와 배치 규칙을 명세로 표현하고, 엔진은 표준 적용·좌표 전개·검사를 맡는다. 여러 객체는 `draw_batch`로 작성한다. 수치는 서버의 `get_standard`로 조회한다. 표준값의 원본은 `standards/heo-2026/`, 참조문서의 생성표는 같은 YAML에서 만든 사본이다.

## 필요한 참조

| 문서 | 읽을 때 |
|---|---|
| [레이어](references/layers.md) | 시작 전 항상 |
| [명세 작성](references/batch-spec.md) | `draw_batch` 전. 지원 형식·실행 가능한 예제 |
| [변경 명세](references/change-spec.md) | `apply_changes` 전. 대상 선택·동작·거부 코드·변경 예제 |
| [문자·치수·블록](references/annotation.md) | 문자·기호·치수·도곽 작성 시 |
| [평면·입면·단면 순서](references/order-architectural.md) | 건축 도면 작성 시 |
| [계단](references/stair.md) | 계단 표기 시 |
| [배치도 순서](references/order-siteplan.md) | 배치도 작성 시 |
| [설계 가정](references/design-assumptions.md) | 설계값이 미정일 때 |

## 작업 절차

1. **표준·요구 확인.** `get_standard(section="summary", scale=k)`로 ID·버전·축척을 받고 필요한 `layers`, `text`, `dimensions`, `blocks`를 조회한다. 단위는 mm, 좌표는 모형 공간 WCS다. k는 A3 축척 분모다. 미지정 축척은 프로파일 기본값을 쓰고 보고한다. 배치도는 도곽 그림 영역에 맞춰 정하고 가정을 기록한다.
2. **대상 준비.** 새 도면은 `new_from_template(file_path=<새 DWG 절대경로>)`로 만든다. 저장 위치 미지정 시 서버 저장소의 `output/`을 사용한다. 템플릿 자체를 열어 작도하지 않는다. 기존 도면은 `list_drawings`와 필요 시 `switch_drawing`으로 확인한다. 필요한 표준 레이어는 `ensure_standard_layers`로 보완한다. 같은 이름의 정의가 다르면 충돌 결과에 따라 멈춘다.
3. **읽기.** `export_snapshot()` → 반환 `snapshot.snapshot_id`로 `read_drawing(snapshot_id=...)`. 단위·그리드·실명·기존 객체 범위를 확인하고 `ids.drawing_id`와 스냅샷 ID를 보관한다. mm가 아니면 쓰기를 중단하고 단위와 실제 좌표의 의미를 확인한다. DXF 사본은 항상 `export_snapshot`을 쓰며 `save_drawing`으로 DXF를 저장하지 않는다.
4. **명세 작성.** 조회한 `standard`, 고유한 `op`, `scale`, 대상 ID, 그리드와 `items`를 담는다. 반복·대칭·회전은 `group.place`, 문자는 `role`·`align`, 블록 속성은 태그별 `attribs`, 해치 섬은 `holes`, 치수는 `dim`·`dim_chain`으로 표현한다. 레이어·용도는 허용된 조합을 쓴다. 색·선굵기·문자 높이·블록 배율·DIMSCALE을 직접 넣지 않는다.
5. **적용.** `draw_batch(spec=...)` 결과를 확인한다. 재전송은 **같은 op와 같은 명세**로 한다. 이미 완료된 요청은 건너뛴다. 충돌을 피하려고 op를 바꿔 중복 삽입하거나 누락분을 저수준 도구로 보충하지 않는다.
6. **검사·수정.** `check_standard(scale=k)`로 최신 활성 도면을 검사한다. `fixes`는 제안 목록이다. 자동 수정 가능한 레이어·색·선종류·선굵기는 `check_standard(snapshot_id=<검사 결과 ids.snapshot_id>, scale=k, fix=true)`로 적용한다. `fix`는 활성 도면에만 쓰며 `file_path`와 함께 쓰지 않는다. 남은 위반·미검사를 확인한다.
7. **저장·보고.** 확인된 대상에 `save_drawing(file_path=<DWG 절대경로>)`을 호출하고 성공을 확인한다. `screenshot`은 외관 확인용, `plot`은 요청된 PDF 출력용이다. 수치 검사는 `check_standard` 결과로 보고한다.

## 기존 객체 변경 절차

1. **읽기.** 대상 도면·단위·축척을 확인한다. `export_snapshot()` → `read_drawing(snapshot_id=<snapshot.snapshot_id>)`로 대상을 찾고 기준 ID를 보관한다. 사용자가 CAD에서 고른 객체는 `get_selection`으로 읽는다. 이 결과의 `handles`, `engine_objects[].oid`, `ids.snapshot_id`를 쓸 수 있다. `truncated=true`면 일부만 바꾸지 않는다. `get_selection`의 limit을 올려 다시 읽거나 엔진 객체는 `op` + `path_prefix`로 범위를 확인한다. `read_drawing`에는 limit이 없다. 일반 객체 핸들은 `full_path` JSON의 `entities[].handle`에서 얻는다.
2. **명세.** [변경 명세](references/change-spec.md)에 따라 고유한 `op`, `target.snapshot_id`, 변경별 `id`·`select`·`action`을 담는다. `select`는 `handles` / `oids` / `op` + `path_prefix` 중 하나다. `move`는 `delta`, `set`은 `props`, `delete`는 둘 다 쓰지 않는다. 한 객체는 변경 하나만 받는다. 같은 실명을 옮기고 내용을 바꾸려면 두 요청으로 나눈다. 두 번째는 첫 요청 성공을 확인한 뒤 새 기준 스냅샷·새 op로 한다. 두 요청 전체는 원자적이지 않음을 보고한다.
3. **적용.** `apply_changes(spec=..., scale=k)`를 호출한다. 재전송은 **원래 명세 그대로, 같은 op·같은 snapshot_id**로만 한다. 입력 해시에 기준 ID도 포함된다. 결과의 `ids.snapshot_id`는 적용 직전 사본이며 원래 기준을 대신하지 않는다. 다음 논리적 변경은 새 스냅샷과 새 op로 한다.
4. **결과 확인.** `status=success` + `meta.decision=skip`이면 이미 완료된 요청이다. `conflict`면 멈추고 보고한다. 새 op·저수준 도구로 우회하지 않는다. `failed`의 `CHG_*`는 코드에 따라 명세를 고친다. 잠긴 레이어 등 도면 상태 문제는 사용자에게 알린다. `unverified`는 복귀 또는 적용 기록을 확인하지 못한 상태다. 자동 재전송·저장 등 후속 작업을 중단한다. 사용자 확인·명시적 재개 뒤에만 `resume_after_unverified=true`를 쓴다.
5. **검사·저장·보고.** 성공 결과의 `applied`, `new_violations`, `unchecked`, `warnings`를 확인한다. `unchecked`가 있으면 완료로 보고하지 않는다. `check_standard(scale=k)`로 전체 도면을 검사하고, 확인된 결과만 DWG로 저장한다. 아래 보고 계약을 따른다.

이 절차의 `apply_changes` 대상은 모형 공간 LINE·LWPOLYLINE·CIRCLE·ARC·TEXT·INSERT, extrusion=(0,0,1). 원·호의 set은 공통 표시 속성(레이어·색·선종류·선굵기)만. 펼친 대상 상한은 1,000개다. 회전·대칭·배율, 해치·치수·MTEXT, 블록 정의 내부, 속성 단독 핸들은 지원하지 않는다. 미지원으로 보고하고 저수준 도구로 우회하지 않는다.

`op` + `path_prefix`에 해치 등 지원 밖 객체가 섞이면 요청 전체가 거부된다. 요청한 대상이 모두 지원 객체일 때만 `oids`로 정확히 좁힌다. '객실 전체 이동'에 해치가 포함됐다면 일부만 옮겨 완료 처리하지 않는다. 미지원 범위를 보고하고 사용자 판단을 받는다. 연관 해치 경계는 해치를 빼고 골라도 `CHG_RELATION`이다.

## 보호 규칙과 상태별 행동

ARCAID 태그가 붙은 엔진 객체는 저수준 그리기·편집 도구, `send_command`, `run_lisp`, 수동 Undo로 고치지 않는다. 정해진 표준 수정은 보호된 `check_standard(fix=true)`를 사용한다. 적용·실패 복구는 엔진이 담당한다.
저수준 이동·삭제·회전·배율은 엔진 객체(INSERT·종속 ATTRIB 포함)와 태그 읽기 실패를 거절한다. 복사·대칭·Offset·배열은 새 객체의 ARCAID만 제거하고 확인하며, 실패하면 생성 핸들을 보고하므로 다시 시도하지 말고 확인한다. 편집 9종은 모형·종이 공간의 객체만 받는다. 기존 객체 이동·삭제·속성 변경은 기준 스냅샷을 담은 보호된 `apply_changes`를 사용한다.
`send_command`·`run_lisp`·`undo`·`redo`·`insert_block`·`create_block`은 엔진 객체를 통제하지 못한다. 통제하지 못한 변경은 대상·기준·기록에 따라 다음 보호된 경로에서 충돌로 잡힐 수 있으나, 태그 제거·op 전체 삭제·블록 정의 변경은 검출하지 못할 수 있다. CAD 화면에서 동시에 편집하는 경우는 보장 범위 밖이다.

| status | 다음 행동 |
|---|---|
| `success` | 적용·검사 결과를 확인한다. 성공 응답만으로 전체 표준 위반이 없다고 단정하지 않는다 |
| `conflict` | 변경을 멈추고 대상·기존 결과·수동 수정·정의 차이를 보고한다. 새 op·삭제 재작도·저수준 보충으로 우회하지 않는다 |
| `failed` | 이유를 보고하고 원인이 해소되기 전 변경을 이어가지 않는다 |
| `recovered` | 요청은 실패했지만 적용 전 상태로 복귀한 것이 확인됐다. 성공으로 보고하지 않는다. 원인을 해결한 뒤 같은 요청을 재시도한다 |
| `unverified` | 복귀 또는 적용 기록을 확인하지 못했다. 자동 재전송·저장 등 모든 후속 변경을 중단하고 사용자에게 CAD 상태 확인을 요청한다. 사용자가 확인하고 재개를 명시한 뒤에만 `resume_after_unverified=true`를 쓴다 |

수동 변경을 덮어쓰는 `overwrite_manual=true`도 해당 변경에 대한 사용자의 명시적 승인 뒤에만 사용한다. 서두르라는 지시는 이 승인에 해당하지 않는다.
`apply_changes`에서는 기준 스냅샷에 이미 있던 수동 수정만 허용한다. 기준을 읽은 뒤 바뀐 객체는 승인 여부와 관계없이 충돌이다.

## 보고 계약

도구 결과를 다음 순서로 전달한다. 없는 값은 추측하지 않는다.

- **상태·요약:** 실제 `status`·`summary`, 저장 성공 여부·경로·축척.
- **대상·검사:** `scope`, `checked`, `applied`의 항목과 개수.
- **수정 호출:** `check_standard(fix=true)`의 적용 내역은 중첩된 `fix`에 있다. `fix.status`, `fix.applied`, `fix.remaining_fixes`도 읽어 실제 수정 건수·잔여 항목을 보고한다.
- **변경 호출:** `apply_changes`의 `meta.decision`, 동작별 `applied`와 개수를 보고한다. `new_violations`는 변경 대상의 위반 차분이다. 전체 도면 위반 집계가 아니다. `unchecked`와 태그만 복원 등 `warnings`도 생략하지 않는다.
- **위반:** `violation_counts`가 있으면 전체 집계를 쓴다. `truncated=true`면 상세가 일부임을 알리고 `full_path`를 안내한다. 빈 `violations`나 `draw_batch.new_violations`만으로 전체 위반 0건이라 보고하지 않는다.
- **미검사·범위 밖:** `unchecked`는 요청 안에서 확인하지 못한 항목·이유, `out_of_scope`는 처음부터 제외한 범위로 각각 보고한다.
- **가정·식별자:** 도구의 `assumptions`와 직접 선택한 설계 가정, `ids.request_id`(op)·`ids.drawing_id`·`ids.snapshot_id`·`ids.standard`. 변경 호출의 원래 기준 스냅샷도 구분해 보고한다.

`status=success`이고 `unchecked`가 비어 있을 때만 처리 완료로 보고한다. **표준 준수**는 전체 위반 집계까지 확인해 별도로 판단한다. `out_of_scope`는 완료 판정에서 제외하되 생략하지 않는다.

## 현재 지원 경계

- 판독은 표준 레이어의 그리드·축열·실명까지다. 임의 벽체·방의 의미 인식, 레이아웃·Xref 내부 해석은 결과에 표시된 범위를 따른다.
- 속성 블록은 대칭 배치가 거부된다. 버블은 대칭 그룹 밖에 둔다. 계단절단선처럼 속성 없는 블록은 그룹 대칭을 쓸 수 있다.
- 문자 role·레이어는 생성표를 따른다. 현재 PAK1·TXT1에 허용된 문자 용도는 없다. 미지원으로 보고하고 다른 레이어·저수준 도구로 우회하지 않는다.
- 비주석 치수는 `DIMSCALE = k/2`로 엔진이 적용한다. 활성 도면 LTSCALE 헤더를 임의 축척으로 바꾸는 보호된 도구는 없다. 값이 다르면 검사 결과에 남기고 사용자에게 설정 확인을 요청한다.
- CAD 없이 DXF를 다룰 때는 저장소 CLI `arcaid read`, `arcaid check`, `arcaid build`를 쓴다. CLI build는 열린 CAD에 적용하지 않는다. 스킬 zip만으로 서버·CLI를 설치할 수는 없다.
