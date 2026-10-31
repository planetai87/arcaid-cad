# 도면 검토 화면

저장소 루트에서 Python 서버를 실행합니다. CAD 연결은 사용하지 않습니다.

~~~powershell
python -m src.ui --work <명세 폴더> --no-open
~~~

기본 주소는 http://127.0.0.1:8770 입니다. --base <기준.dxf>, --port <포트>를 지정할 수 있습니다.
기준 도면을 명시하지 않으면 왼쪽 목록에서 작업 폴더의 DXF를 선택합니다.

이 폴더에서 화면을 설치하고 빌드합니다.

~~~powershell
npm install
npm run build
npm test
~~~

개발 시 Python 서버와 npm run dev를 함께 실행합니다. Vite가 /api 요청을 8770으로 전달합니다.

명세 여러 개를 켜고, 휠로 확대하거나 드래그로 이동할 수 있습니다. 객체를 클릭하면 원본 항목과 반복 배치 위치가 표시됩니다. 회색 기준 도면도 클릭하여 핸들을 확인할 수 있습니다. 원본 파일을 저장하면 자동으로 다시 읽고, 형상이 달라진 명세 객체를 3초 동안 강조합니다. 파일과 객체 값은 이 화면에서 바꾸지 않습니다.


## 메모 검토

상단의 **메모** 버튼 또는 **M**으로 메모 모드를 켭니다. 명세·기준 도면 객체를 클릭하면 해당 객체를, 빈 곳을 클릭하면 좌표를 대상으로 메모를 남깁니다. **Shift + 끌기**는 영역과 그 영역에 걸친 객체를 선택합니다(명세·기준 각각 최대 200개, 초과 시 생략 표시). Enter로 저장, Esc로 취소, Shift + Enter로 줄바꿈합니다.

오른쪽 메모 탭에서 열림/전체 필터, 답글, 해결·다시 열기를 사용합니다. 목록이나 핀을 누르면 대상 위치로 이동하고 객체를 선택·강조합니다. 사라졌거나 인스턴스 정보가 달라진 대상은 "대상이 바뀜"으로 표시합니다. 핀은 열림 주황·답함 하늘·해결 회색이며, 새 답글은 3초간 강조합니다.

메모는 작업 폴더의 review/notes.json에 저장됩니다. 화면과 CLI는 같은 저장 함수를 사용하며 임시 파일을 원자적으로 교체합니다. 먼저 저장된 변경으로 rev가 달라지면 409로 현재 내용을 다시 읽고 알립니다. 입력은 유지되므로 내용을 확인하고 다시 저장하세요. 명세·DXF 원본은 수정하지 않습니다.

저장소 루트에서 Claude Code가 사용할 명령:

~~~powershell
python -m src.ui.notes --work <명세 폴더> list --open
python -m src.ui.notes --work <명세 폴더> list --json
python -m src.ui.notes --work <명세 폴더> reply n1 "확인했습니다" --status answered
python -m src.ui.notes --work <명세 폴더> resolve n1
~~~

영역 대상은 window: [x0,y0,x1,y1], specs: [{file,oid,path,sources,layer}], bases: [{file,handle,layer}]로 기록합니다. 목록이 잘리면 truncated: true를 기록합니다. 객체 대상에는 파일·레이어·선택 좌표와 명세 원본 위치 또는 CAD handle을 보존합니다.

코드 포맷은 npm run format, 변경 없이 확인하려면 npm run format:check를 실행합니다.
