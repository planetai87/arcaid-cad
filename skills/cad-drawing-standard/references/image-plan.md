# 평면 이미지를 표준 도면으로 다시 그리기

CAD에서 내보낸 평면도 이미지(PNG 등)를 회사 표준 DWG로 다시 그릴 때 쓴다. 원근이 있는 사진, 크게 휜 스캔, 벡터 PDF는 정확도를 보장하지 못한다. 진행은 하되 사용자에게 먼저 알린다.

환경에 따라 두 경로가 있다. 어느 경로든 그 환경에서 낼 수 있는 최선의 결과를 만든다.

| 환경 | 경로 | 결과 |
|---|---|---|
| arcaid-cad 저장소가 있는 Claude Code(측정·생성 도구 사용 가능) | [측정 경로](#측정-경로) | 측정값으로 만든 도면(검증됨) |
| 저장소의 측정·생성 도구는 없고 CAD MCP는 연결된 환경(스킬만 설치한 Claude Desktop 등) | [어림 경로](#어림-경로) | 어림 좌표로 그린 도면, **추정·미검증** 표시 |

CAD MCP도 없는 환경(claude.ai 웹 대화 등)에서는 어림 경로의 명세 JSON과 출처·오차 표까지 만들어 주고, CAD가 연결된 곳에서 적용하도록 안내한다.

## 측정 경로

**치수 우선.** 원근·휨이 있는 사진이나 손으로 그린 도면은 보정 전제(회전과 균일 축척)에 맞지 않는다. 이런 이미지에서는 다음처럼 나눈다.
- 축과 벽의 위치는 원본 치수 문자로 정해 `record.py set-value`로 적는다. 출처는 원본표기이고, 근거가 된 치수 문자를 함께 적는다.
- 측정은 치수가 없는 곳(문 위치, 기구, 계단 등)과 원본 대조에만 쓴다.
- 보정 결과 축척 차이나 잔차가 크거나, 직선 맞춤에서 방향이 위치마다 달라지면 이 방식으로 바꾼다.

모든 위치값은 측정 도구가 기록한 것이나 원본 치수 문자만 쓴다. 눈대중으로 정한 좌표는 쓰지 않는다(지상6층 재작도에서 눈대중 오차가 최대 170 mm였다). 도구는 저장소 루트에서 아래 명령 문자열로 부른다. 옵션은 각 도구의 `--help`와 docstring을 따른다.

0. **시작 전 확인.** 사용자에게 다음을 확인한다: 축척 k, 그릴 범위, 해치 여부, 표준 예외(예: 원본처럼 하단 버블), 결과 이미지 커밋 여부.
1. **보정.** 축 교점처럼 WCS 위치를 아는 점 3개 이상을 대응점으로 쓰고, 보정에 쓰지 않은 독립 검증점을 하나 더 준다. 축 간격은 원본 치수 문자에서 얻는다.
   - 결과의 `err_mm`은 **보정 오차**다.
   - 관측 하나의 허용오차는 `tol_mm`에 따로 나온다: profile은 보정 오차 + 0.5 px, blob은 + 1 px다.
   - 두 선의 교점 오차는 교차각이 작을수록 커진다.
   - 명령: `python tools/raster/measure.py calib IMG --point U,V=X,Y … --check U,V=X,Y -o calib.json`
   - **사진(휨·원근)**: 위 명령에 선 대응을 더한다. `--vline U1,V1,U2,V2=X`는 X만 아는 세로선(축선, 치수 연장선)이고, `--hline …=Y`는 Y만 아는 가로선(외곽면, 치수 연장선)이다. 여기에 `--warp 2`를 붙인다.
     - 선 구간은 대략이면 된다. 다른 선과 겹치는 조각은 도구가 버린다.
     - 서로 다른 X와 서로 다른 Y가 각각 차수 + 1개 이상 필요하고, 그림 전체에 고르게 있어야 한다.
     - 선이 덮지 않는 바깥은 외삽이므로 검증점으로 확인한다.
     - Y(또는 X)를 모르는 곧은 선(치수선 줄 등)은 `=?`로 주면 선마다 미지값으로 함께 풀어 전체 폭의 원근을 잡는다. 굵게 번진 연필선은 `--piece-rms 3.5`.
   - **빗금 벽**: 면선 사이를 빗금으로 채운 벽은 단면에서 넓은 띠 하나로 잡힌다. 띠의 시작·끝 가장자리를 `--names=바깥/안쪽`으로 두 면선으로 기록한다.
2. **기록 머리말.** `python tools/raster/record.py init --image IMG --calib calib.json --standard heo-2026@<버전> --k k -o measure.yaml`로 measure.yaml을 만들고 머리말을 쓴다.
   - `frames`: 회전 구역이 있으면 지역 좌표계를 둔다. 각도는 `measure.py angles`로 구한다.
   - `grids`: 축 위치. 키는 frame 이름이고, WCS는 `wcs`다.
   - `legend`: "선 형태 → 뜻" 판독 범례. **사용자가 승인한 것만** `approved_by: user`로 적는다.
   - `regions`: 영역마다 window, frame, 배정 요소 id를 적는다. 영역 경계에 걸친 벽은 경계 축선을 끝선으로 삼아 두 요소로 나눠 배정한다. 생성기가 둘을 합친다.
3. **잠금.**
   - `python tools/raster/record.py lock measure.yaml -o lock.json`을 실행한다.
   - 영역마다 `python tools/raster/record.py region-init measure.yaml --lock lock.json --region R1 -o region-R1.yaml`을 실행한다.
   - 이 뒤로 머리말을 고치면 잠금이 깨진다.
4. **영역 판독.** 영역마다 plan-reader 서브에이전트를 **동시에** 부른다.
   - 입력: 폴더 경로, 이미지·calib.json·measure.yaml·lock.json·영역 파일 경로, 그림 폴더, 배정 요소 표(id, type, 대략 위치, 잴 역할). 정답이나 다른 영역 파일은 주지 않는다.
   - 보고에서 질문, 재지 못한 요소, 참고 사항을 확인한다.
5. **합치기.**
   - `python tools/raster/record.py merge measure.yaml --lock lock.json region-R1.yaml region-R2.yaml … -o merged.yaml`을 실행한다. 영역 파일은 하나씩 나열한다(와일드카드는 펼쳐지지 않음).
   - 불일치(errors)가 있으면 해당 에이전트에 SendMessage로 재측정을 요청한다. 에이전트는 새 이름으로 기록하고 `add-element --replace`, `drop-observation` 순으로 처리한다.
   - 재측정이 끝나면 **잠근 measure.yaml에서** 다시 merge한다.
   - **누락 대조**: 영역마다 배정한 요소 id와 merged.yaml의 요소 id를 대조한다. merge는 배정 밖 요소만 잡고, 빠진 요소는 잡지 않는다. 빠진 요소는 재측정을 맡기거나, 사용자에게 범위에서 뺄지 확인한다.
   - `python tools/raster/record.py validate merged.yaml --lock lock.json --reverify`로 기록된 명령을 다시 실행해, 손으로 고친 값이 없는지 확인한다.
6. **확정.**
   - 질문의 답은 사용자에게 받아 `record.py question answer … --by user`로 적는다.
   - 재료(RC·조적·건식), 문 열림·경첩, 문자 용도 같은 해석은 `record.py interpret … --by user`로 적는다.
   - 측정값이 아닌 값은 주 대화가 `record.py set-value merged.yaml --element E --key K (--u U | --v V | --value 문자 | --number 수) --source 원본표기 --text "근거 치수"`로 적는다. 문자 내용·단면 번호·간격, 그리고 치수 우선일 때의 역할 선이 여기에 해당한다. 필수 값이 빠지면 생성기가 거부한다.
   - 범례에 없는 요소는 사용자 판단 전에는 생성하지 않는다.
   - 확정한 뒤 `validate … --lock lock.json --reverify`를 다시 돌려 `errors`가 비고 `ready`가 true인지 확인한다. 미해결 질문이 있어도 종료 코드는 0이므로, 반드시 `ready`를 본다.
7. **생성.** `python tools/raster/generate.py merged.yaml --prefix P --out runs/<id> --lock lock.json`
   - 상태가 ready여야 ops 폴더가 생긴다. preview면 남은 확인 항목을 처리한다.
   - 다시 생성할 때마다 새 빈 폴더(`runs/<새 id>`)를 쓴다. 비어 있지 않은 폴더는 거부된다. 이후 build·verify·bind도 새 폴더의 결과로 한다.
   - 결과의 `assumptions`(표준에 없어 가정한 기호 치수)와 `warnings`는 보고한다.
   - 지원 요소와 거부 범위는 `generate.py`·`annotate.py` docstring의 지원표를 따른다.
   - 지원표 밖 형상(곡면 벽, 곡선·직선 계단, 등분 치수, 개구부·외벽 치수 단 등)은 생성기를 우회해 손으로 그리지 않는다. 목록으로 보고하고 사용자와 정한다.
8. **미리보기 검토.**
   - `python -m src.cli build runs/<id>/preview/preview.json -o preview.dxf`를 실행하고, `check preview.dxf --scale k --strict`로 확인한다.
   - `python tools/review/render.py overlay preview.dxf --image IMG --calib calib.json -o ov.png --window … --compare --layers <형상 레이어>`로 원본에 겹친다. 회전 구역은 `--frame`으로 따로 본다. `only_image`·`only_dxf`는 불일치 **후보**다. 원본 문자·해치는 후보로 나온다.
   - plan-reviewer에 복원 검토를 맡긴다. '반드시 수정'은 0건이어야 하고, '확인 필요'는 사용자가 처리한다.
   - 겹친 그림을 사용자에게 보여 주고 확인을 받는다.
9. **적용.**
   - `new_from_template`로 새 도면을 만들고 `export_snapshot`을 받는다.
   - `python tools/ops/ops.py verify runs/<id>/ops`로 확인한다.
   - `bind runs/<id>/ops --out runs/<id>/bound --drawing-id … --snapshot-id …`로 대상을 묶는다.
   - `hashes.json` 순서대로 `draw_batch(spec_path=<bound 안 op 파일 절대경로>)`를 보낸다. 명세는 옮겨 적지 않는다.
   - op마다 `status=success`를 확인한다.
10. **검사·저장·대조.**
    - `check_standard(scale=k)`로 검사하고 `save_drawing`으로 저장한다.
    - 최종 `export_snapshot` DXF로 overlay `--compare`를 다시 돌려, 미리보기 때 없던 누락이나 추가가 없는지 확인한다. overlay는 허용거리 안의 차이를 일치로 보는 후보 검사다. 그래서 동일성은 `python tools/review/render.py diff preview.dxf <최종 DXF>`로 함께 확인한다.
    - 최종 보고는 SKILL.md의 보고 계약을 따른다.

## 어림 경로

저장소의 측정·생성 도구가 없으면 이미지를 직접 읽어 좌표를 어림한다(CAD MCP는 연결되어 있어야 한다). 이렇게 그린 도면은 **추정·미검증**이다. 그래도 그 환경에서 낼 수 있는 최선의 도면을 만든다.

1. **새 도면에만 그린다.** `new_from_template`로 새 DWG를 만든다. 기존 프로젝트 도면에는 넣지 않는다.
2. **좌표의 근거를 고르는 순서**
   1. 원본에 적힌 치수 문자(원본표기). 축 간격, 실 치수, 벽 두께 등.
   2. 축 간격 치수로 이미지 축척(px/mm)을 구한 뒤 픽셀 거리로 환산한 값. 회전 구역은 그 축의 각도로 지역 좌표를 둔다.
   3. 눈대중. 마지막 수단이다.

   벽 두께처럼 원본에 없는 값은 [설계 가정](design-assumptions.md)이나 일반값(외벽 200 등)을 쓰고 가정으로 보고한다.
3. **명세와 작도.** [명세 작성](batch-spec.md)대로 쓰고 `draw_batch`로 그린다. 표준(레이어, 문자, 치수, 블록)은 측정 경로와 똑같이 지킨다. 축선, 치수, 버블은 [문자·치수·블록](annotation.md), 계단은 [계단](stair.md)을 따른다.
4. **추정 표시**
   - op 이름은 `est-`로 시작한다. 예: `est-1f-walls`.
   - 도면 안에 REM 레이어 `general` 문자로 주기를 하나 넣는다: `추정·미검증: 이미지 어림 판독(예상 오차 %%Pn mm). 측정 확인 전 설계 근거로 쓰지 않음`. `%%P`는 ±의 CAD 코드다. 그림 영역 왼쪽 위 등 도면과 겹치지 않는 곳에 둔다.
5. **검사·보고.**
   - `check_standard(scale=k)`로 표준 검사를 하고 저장한다.
   - 보고 첫 줄에 **추정·미검증**을 쓴다.
   - 값마다 출처(원본표기, 축척 환산, 눈대중)와 예상 오차를 표로 적는다. 예상 오차의 기준값은 다음과 같다. 둘 다 보장된 상한이 아니라 가정이다.
     - 축척 환산: 이미지 1~2 px
     - 눈대중: ±200 mm 안팎(지상6층 실측 최대 170 mm)
   - SKILL.md 보고 계약도 그대로 따른다.
   - 확인이 필요한 곳(흐린 선, 겹친 문자, 뜻을 모르는 선)을 함께 적는다.
   - 정식 확정은 저장소가 있는 Claude Code의 측정 경로(측정·원본 겹침 대조)로 하도록 안내한다.

## 교훈

- **회전 구역.** frame 지역 좌표의 회전 group으로 그린다. 그 구역의 치수와 버블도 group 안에 둔다.
- **버블 위치.** 평면 버블은 상부·좌측이 표준이다. 원본처럼 하·우에 두려면 사용자가 승인한 예외로만 한다. 속성 블록을 뒤집으면 글자가 거꾸로 서므로, 하·우 버블은 원·선·문자로 그린다.
- **반복 형상.** 디딤판·물결선처럼 반복되는 형상은 긴 점·bulge 배열이 아니라 `group` 반복으로 만든다. 긴 배열을 옮겨 적다가 개수가 틀려 거부된 적이 있다.
- **LTSCALE.** k가 100이 아니면 템플릿 LTSCALE과 맞지 않아 위반 1건이 남을 수 있다. 보호된 도구로는 고칠 수 없으므로 보고하고 사용자 확인을 받는다.
- **tilted 경고.** profile 경고 `tilted`는 frame이 틀렸다는 신호다. 경고가 없다고 frame이 맞다는 뜻은 아니므로 `angles`로 확인한다.
- **PowerShell 변수.** 대소문자를 가리지 않는다(`$r`와 `$R`은 같은 변수).
