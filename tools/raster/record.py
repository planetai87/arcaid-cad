"""평면 이미지 측정 기록(measure.yaml)·잠금·영역 파일 도구(CAD 없음).

사용(저장소 루트):
  python tools/raster/record.py init --image IMG --calib calib.json --standard heo-2026@2026.1 --k 200 -o measure.yaml
  python tools/raster/record.py lock measure.yaml -o lock.json
  python tools/raster/record.py region-init measure.yaml --lock lock.json --region NAME -o region-NAME.yaml
  (측정값은 measure.py profile·fitline의 --record FILE --names=a,_,b / --name으로만 기록한다)
  python tools/raster/record.py add-element FILE --id E1 --type wall --ref f1=obs1 --ref f2=obs2
                                [--link host=EW1] [--candidate meaning=창,커튼월] [--note 설명]
                                (또는 --json '{...}')
  python tools/raster/record.py question add FILE --id Q1 --text "..." [--element E1]
  python tools/raster/record.py merge measure.yaml --lock lock.json region-A.yaml region-B.yaml -o measure.yaml
  python tools/raster/record.py question answer measure.yaml --id Q1 --answer "..." --by user|main
  python tools/raster/record.py interpret measure.yaml --element E1 --attr material --value RC (--by user|main | --pending)
  python tools/raster/record.py validate measure.yaml [--lock lock.json] [--reverify]

규칙:
- 영역 파일에는 관측·요소(관측 참조·출처 있는 값·후보)·질문만 들어간다. 배정받지 않은 요소, 답, 해석은 넣지 못한다.
- merge는 잠금·영역별 잠금·이름 겹침·배정·답을 확인하고, 출처가 다른 관측 중 방향이 같고(0.5° 안) 거리가
  허용오차 안이면 같은 선(same_lines), 허용오차~2배면 불일치(오류)로 본다. 다른 frame으로 잰 선도 WCS로 비교한다.
- validate는 실측 값이 참조한 관측과 같은지, 참조가 있는지, 잠금이 같은지 보고, 미해결 질문·확인 필요 해석·추정
  값을 목록으로 낸다. ready는 오류·미해결·확인 필요가 모두 없을 때만 참이다.
- --reverify는 관측마다 기록된 명령을 다시 실행해 값을 대조한다(손으로 고친 숫자를 찾는다). 판독 에이전트의
  도구 제한은 프롬프트 규칙일 뿐이라, 이 사후 확인으로 지킨다.

파일:
- measure.yaml(kind measure): 주 대화가 쓴다. 머리말 = image·calib·output·frames·grids·legend·regions.
  init이 image·calib를 보정 파일에서 옮겨 적고, frames·grids·legend·regions는 주 대화가 쓴다.
- lock.json: 머리말의 해시(lock_hash). 판독 에이전트를 부르기 전에 주 대화가 만든다.
- 영역 파일(kind region): 판독 에이전트 하나가 쓴다. 잠금 해시·보정 파일 sha256을 담는다.
머리말의 값은 {value, source, ...}로 출처를 적는다. source: 실측(ref "관측이름.필드"), 파생(formula·refs),
원본표기(text), 추정(reason), 표준(key). 관측(observations)은 측정 도구가 명령·선택 번호와 함께 기록한다.
관측은 kind로 나뉜다: line(profile·fitline, 직선 하나)과 point(blob, 외곽 상자와 그 중심). merge의 같은 선 비교는
line만 하고, --reverify는 둘 다 다시 실행한다. 머리말 grids의 키는 frame 이름이고 WCS는 wcs다.
파일 쓰기는 같은 폴더의 임시 파일에 쓴 뒤 이름을 바꾼다(반쯤 쓴 파일이 남지 않게).
표준 출력은 UTF-8 JSON 하나이고, 성공이면 종료 코드 0, 거부·오류면 1이다.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator
import yaml

SCHEMA_VERSION = 1
HEADER_KEYS = ("image", "calib", "output", "frames", "grids", "legend", "regions")


class RecordError(ValueError):
    pass


# ---------- 형식 ----------

class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Value(_Model):
    value: Any
    source: Literal["실측", "파생", "원본표기", "추정", "표준"]
    ref: Optional[str] = None
    refs: Optional[List[str]] = None
    formula: Optional[str] = None
    text: Optional[str] = None
    reason: Optional[str] = None
    key: Optional[str] = None

    @model_validator(mode="after")
    def _needs(self):
        need = {"실측": "ref", "파생": "formula", "원본표기": "text", "추정": "reason", "표준": "key"}[self.source]
        if not getattr(self, need):
            raise ValueError(f"출처 {self.source}에는 {need}가 필요함")
        if self.source == "파생" and not self.refs:
            raise ValueError("출처 파생에는 refs가 필요함")
        return self


class FrameDef(_Model):
    x: Value
    y: Value
    deg: Value


class Region(_Model):
    frame: Optional[str] = None
    window: List[float]
    elements: List[str] = []


class Legend(_Model):
    id: str
    pattern: str
    meaning: str
    approved_by: Literal["user"]


class Image_(_Model):
    path: str
    sha256: str


class CalibRef(_Model):
    file: str
    file_sha256: str
    px_per_mm: float
    beta_deg: float
    origin_px: List[float]
    err_mm: float


class Output(_Model):
    standard: str
    k: int


class LineObservation(_Model):
    kind: Literal["line"]
    tool: Literal["profile", "fitline"]
    frame: List[float]
    axis: Literal["u", "v"]
    coord: float
    angle_local_deg: float
    angle_wcs_deg: float
    point_wcs: List[float]
    width_px: Optional[float] = None
    tol_mm: float
    flags: List[str] = []
    command: List[str]
    pick: Optional[int] = None
    edge: Optional[Literal["start", "end"]] = None     # 넓은 띠(빗금 벽)의 시작·끝 가장자리로 잰 면선
    image_sha256: str
    calib_sha256: str


class PointObservation(_Model):
    """blob 점 관측: 외곽 상자(bbox_local, frame 지역 좌표)와 그 중심(point_local·point_wcs)"""
    kind: Literal["point"]
    tool: Literal["blob"]
    frame: List[float]
    point_local: List[float]
    point_wcs: List[float]
    bbox_local: List[float]
    tol_mm: float
    flags: List[str] = []
    command: List[str]
    pick: Optional[int] = None
    image_sha256: str
    calib_sha256: str


Observation = Annotated[Union[LineObservation, PointObservation], Field(discriminator="kind")]
_OBSERVATION = TypeAdapter(Observation)


class Element(_Model):
    """요소(벽·창 등). 판독 에이전트는 관측 참조(refs)·요소 참조(links, 예: 창의 host 벽)·출처 있는 값(values)·
    후보(candidates)만 적는다. 재료·용도 같은 확정은 measure.yaml의 interpretations에 주 대화가 적는다.
    요소 종류별 필드는 생성기가 정한다"""
    type: str
    frame: Optional[str] = None
    refs: Dict[str, str] = {}
    links: Dict[str, str] = {}
    values: Dict[str, Value] = {}
    candidates: Dict[str, List[Any]] = {}
    note: Optional[str] = None
    region: Optional[str] = None      # merge가 채운다


class Question(_Model):
    id: str
    text: str
    element: Optional[str] = None
    region: Optional[str] = None
    answer: Optional[str] = None
    by: Optional[Literal["user", "main"]] = None


class MeasureFile(_Model):
    schema_version: Literal[1]
    kind: Literal["measure"]
    units: Literal["mm"]
    image: Image_
    calib: CalibRef
    output: Output
    frames: Dict[str, FrameDef] = {}
    grids: Dict[str, Dict[Literal["X", "Y"], Dict[str, Value]]] = {}
    legend: List[Legend] = []
    regions: Dict[str, Region] = {}
    observations: Dict[str, Observation] = {}
    elements: Dict[str, Element] = {}
    interpretations: Dict[str, Any] = {}
    questions: List[Question] = []


class RegionFile(_Model):
    schema_version: Literal[1]
    kind: Literal["region"]
    region: str
    lock: str
    image_sha256: str
    calib_sha256: str
    frame: Optional[str] = None
    window: List[float]
    assigned: List[str] = []
    observations: Dict[str, Observation] = {}
    elements: Dict[str, Element] = {}
    questions: List[Question] = []


# ---------- 읽기·쓰기 ----------

def _sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    """measure 또는 region 파일을 읽어 형식을 확인한 dict로"""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise RecordError(f"기록 파일을 읽지 못함: {path}: {e}") from e
    model = {"measure": MeasureFile, "region": RegionFile}.get(data.get("kind"))
    if model is None:
        raise RecordError(f"기록 파일 종류가 아님(kind {data.get('kind')}): {path}")
    try:
        model.model_validate(data)
    except ValidationError as e:
        raise RecordError(f"기록 파일 형식 오류: {path}: {e}") from e
    return data


def save(path, data):
    """임시 파일에 쓴 뒤 이름을 바꾼다"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def lock_hash(data):
    """머리말(image·calib·output·frames·grids·legend·regions)의 sha256"""
    header = {k: data.get(k) for k in HEADER_KEYS}
    text = json.dumps(header, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_lock(lock_path):
    try:
        return json.loads(Path(lock_path).read_text(encoding="utf-8"))["lock"]
    except (OSError, ValueError, KeyError) as e:
        raise RecordError(f"잠금 파일을 읽지 못함: {lock_path}") from e


# ---------- 명령 ----------

def init_measure(image, calib_path, standard, k, out):
    calib = json.loads(Path(calib_path).read_text(encoding="utf-8"))
    if calib.get("status") != "ok":
        raise RecordError("거부된 보정 파일")
    sha = _sha_file(image)
    if calib.get("sha256") != sha:
        raise RecordError("보정 파일의 이미지 sha256이 이 이미지와 다름")
    data = {
        "schema_version": SCHEMA_VERSION, "kind": "measure", "units": "mm",
        "image": {"path": str(Path(image).resolve()), "sha256": sha},
        "calib": {"file": str(Path(calib_path).resolve()), "file_sha256": _sha_file(calib_path),
                  "px_per_mm": calib["px_per_mm"], "beta_deg": calib["beta_deg"],
                  "origin_px": list(calib["origin_px"]), "err_mm": calib["err_mm"]},
        "output": {"standard": standard, "k": int(k)},
        "frames": {}, "grids": {}, "legend": [], "regions": {},
        "observations": {}, "elements": {}, "interpretations": {}, "questions": [],
    }
    MeasureFile.model_validate(data)
    save(out, data)
    return {"status": "ok", "written": str(out)}


def write_lock(measure_path, out):
    data = load(measure_path)
    h = lock_hash(data)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({"lock": h, "measure": str(Path(measure_path).resolve())},
                                    ensure_ascii=False), encoding="utf-8")
    return {"status": "ok", "lock": h, "written": str(out)}


def region_init(measure_path, lock_path, region, out):
    data = load(measure_path)
    lock = _read_lock(lock_path)
    if lock_hash(data) != lock:
        raise RecordError("measure.yaml 머리말이 잠금과 다름(잠근 뒤 바뀜). lock을 다시 만들 것")
    if region not in data["regions"]:
        raise RecordError(f"머리말에 없는 영역: {region}")
    if Path(out).exists():
        raise RecordError(f"영역 파일이 이미 있음: {out}")
    reg = data["regions"][region]
    save(out, {"schema_version": SCHEMA_VERSION, "kind": "region", "region": region, "lock": lock,
               "image_sha256": data["image"]["sha256"], "calib_sha256": data["calib"]["file_sha256"],
               "frame": reg.get("frame"), "window": reg["window"], "assigned": list(reg.get("elements", [])),
               "observations": {}, "elements": {}, "questions": []})
    return {"status": "ok", "written": str(out)}


def add_element(path, element, replace=False):
    """요소를 넣는다. replace면 이미 있는 요소를 바꾼다(재측정: 새 관측으로 refs를 바꿀 때). 영역 파일은 배정 요소만"""
    data = load(path)
    element = dict(element)
    eid = element.pop("id", None)
    if not eid:
        raise RecordError("요소에 id가 필요함")
    try:
        Element.model_validate(element)
    except ValidationError as e:
        raise RecordError(f"요소 형식 오류(판독 쪽은 확정 상태를 쓰지 않음): {e}") from e
    if element.get("region"):
        raise RecordError("region은 merge가 채움")
    if data["kind"] == "region" and eid not in data["assigned"]:
        raise RecordError(f"맡지 않은 요소: {eid}(영역 {data['region']}의 배정: {', '.join(data['assigned']) or '없음'})")
    if eid in data["elements"] and not replace:
        raise RecordError(f"요소 id가 이미 있음: {eid}(재측정이면 --replace)")
    if replace and eid not in data["elements"]:
        raise RecordError(f"바꿀 요소가 없음: {eid}")
    if data["kind"] == "region":
        if element.get("frame") not in (None, data["frame"]):
            raise RecordError(f"영역 {data['region']}의 요소 frame은 영역 frame({data['frame']})이어야 함")
        if data["frame"]:
            element["frame"] = data["frame"]
    elif element.get("frame") and element["frame"] not in data["frames"]:
        raise RecordError(f"없는 frame: {element['frame']}")
    if replace and data["elements"][eid].get("region"):
        element["region"] = data["elements"][eid]["region"]       # merge가 붙인 소유 영역은 둔다
    missing = [o for o in element.get("refs", {}).values() if o not in data["observations"]]
    if missing:
        raise RecordError(f"이 파일에 없는 관측을 참조함: {', '.join(missing)}")
    data["elements"][eid] = element
    save(path, data)
    return {"status": "ok", "element": eid}


def _users(data, name):
    """관측 name을 참조하는 곳(요소 refs, 출처 있는 값의 ref·refs)"""
    users = [f"요소 {eid}.{role}" for eid, el in data["elements"].items()
             for role, ref in el.get("refs", {}).items() if ref == name]
    if data["kind"] == "measure":
        values = _values(data)
    else:
        values = ((f"elements.{eid}.values.{k}", v) for eid, el in data["elements"].items()
                  for k, v in el.get("values", {}).items())
    for where, v in values:
        refs = ([v["ref"]] if v.get("ref") else []) + list(v.get("refs") or [])
        if any(r.partition(".")[0] == name for r in refs):
            users.append(where)
    return users


def drop_observation(path, name):
    """어느 곳도 참조하지 않는 관측만 지운다(재측정으로 버린 관측). 값을 고치는 명령이 아니다"""
    data = load(path)
    if name not in data["observations"]:
        raise RecordError(f"없는 관측: {name}")
    users = _users(data, name)
    if users:
        raise RecordError(f"관측 {name}을 참조하는 곳이 있어 지우지 않음: {', '.join(users)}")
    del data["observations"][name]
    save(path, data)
    return {"status": "ok", "dropped": name}


def add_question(path, qid, text, element=None):
    data = load(path)
    if any(q["id"] == qid for q in data["questions"]):
        raise RecordError(f"질문 id가 이미 있음: {qid}")
    q = {"id": qid, "text": text}
    if element:
        q["element"] = element
    data["questions"].append(q)
    save(path, data)
    return {"status": "ok", "question": qid}


# ---------- 합치기 ----------

SAME_ANGLE_DEG = 0.5


def _line_pairs(sources):
    """출처가 다른 관측 쌍 중 같은 선(거리 ≤ 허용오차)과 불일치(허용오차 < 거리 ≤ 2배)"""
    flat = [(src, name, o) for src, obs in sources for name, o in obs.items() if o.get("kind", "line") == "line"]
    same, conflicts = [], []
    for i, (sa, na, a) in enumerate(flat):
        for sb, nb, b in flat[i + 1:]:
            if sa == sb:
                continue
            da = (a["angle_wcs_deg"] - b["angle_wcs_deg"] + 90.0) % 180.0 - 90.0
            if abs(da) > SAME_ANGLE_DEG:
                continue
            th = math.radians(a["angle_wcs_deg"])
            d = abs((b["point_wcs"][0] - a["point_wcs"][0]) * -math.sin(th)
                    + (b["point_wcs"][1] - a["point_wcs"][1]) * math.cos(th))
            tol = max(a["tol_mm"], b["tol_mm"])
            pair = sorted([na, nb])
            if d <= tol:
                same.append(pair)
            elif d <= 2 * tol:
                conflicts.append(f"같은 면으로 보이는 관측 {pair[0]}·{pair[1]}({sa}·{sb})이 불일치: "
                                 f"거리 {d:.1f} mm(허용 {tol:.1f} mm)")
    return sorted(same), conflicts


def merge(measure_path, lock_path, region_paths, out):
    data = load(measure_path)
    lock = _read_lock(lock_path)
    if lock_hash(data) != lock:
        raise RecordError("measure.yaml 머리말이 잠금과 다름(잠근 뒤 바뀜). 영역 판독을 다시 해야 함")
    errors = []
    sources = [("main", data["observations"])]
    owner = {name: "main" for name in data["observations"]}
    elements = dict(data["elements"])
    questions = list(data["questions"])
    seen_regions = set()
    for path in map(Path, region_paths):
        rf = load(path)
        if rf["kind"] != "region":
            raise RecordError(f"영역 파일이 아님: {path.name}")
        if rf["lock"] != lock:
            raise RecordError(f"{path.name}의 잠금이 현재 잠금과 다름(다른 머리말에서 만든 영역 파일)")
        r = rf["region"]
        if r in seen_regions:
            raise RecordError(f"영역 {r}의 파일이 둘 이상")
        seen_regions.add(r)
        assigned = data["regions"][r].get("elements", [])
        for name in rf["observations"]:
            if name in owner:
                errors.append(f"관측 이름 {name}이 {owner[name]}·{r}에 겹침")
            owner.setdefault(name, r)
        sources.append((r, rf["observations"]))
        for eid, el in rf["elements"].items():
            if eid not in assigned:
                errors.append(f"영역 {r}의 요소 {eid}: 맡지 않은 요소")
            if eid in elements:
                errors.append(f"요소 id {eid}가 겹침")
            elements[eid] = {**el, "region": r}
        for q in rf["questions"]:
            if q.get("answer") or q.get("by"):
                errors.append(f"영역 {r}의 질문 {q['id']}: 영역 파일에 답이 있음(답은 주 대화가 measure.yaml에)")
            if any(x["id"] == q["id"] for x in questions):
                errors.append(f"질문 id {q['id']}가 겹침")
            questions.append({**q, "region": r})
    same, conflicts = _line_pairs(sources)
    errors += conflicts
    if errors:
        return {"status": "error", "errors": errors, "same_lines": same}
    merged = dict(data)
    merged["observations"] = {name: o for _, obs in sources for name, o in obs.items()}
    merged["elements"] = elements
    merged["questions"] = questions
    MeasureFile.model_validate(merged)
    save(out, merged)
    return {"status": "ok", "written": str(out), "same_lines": same,
            "counts": {"observations": len(merged["observations"]), "elements": len(elements),
                       "questions": len(questions)}}


def _target_shas(data):
    if data["kind"] == "measure":
        return data["image"]["sha256"], data["calib"]["file_sha256"]
    return data["image_sha256"], data["calib_sha256"]


def add_observations(path, items, image_sha, calib_sha):
    """측정 도구가 부른다. items: {이름: 관측 dict}. 이름 중복·다른 이미지·다른 보정 파일이면 아무것도 쓰지 않는다"""
    data = load(path)
    want_image, want_calib = _target_shas(data)
    if image_sha != want_image:
        raise RecordError("기록 파일의 이미지와 다른 이미지를 쟀음")
    if calib_sha != want_calib:
        raise RecordError("기록 파일의 보정 파일과 다른 보정 파일을 썼음")
    for name, obs in items.items():
        if name in data["observations"]:
            raise RecordError(f"관측 이름이 이미 있음: {name}")
        _OBSERVATION.validate_python(obs)
    data["observations"].update(items)
    save(path, data)
    return sorted(items)


# ---------- 확정·검사 ----------

def _measure_only(path, what):
    data = load(path)
    if data["kind"] != "measure":
        raise RecordError(f"{what}은 measure.yaml에만(영역 파일은 관측·후보·질문만)")
    return data


def set_value(path, eid, key, value, source, text=None, reason=None):
    """요소에 측정이 아닌 값을 적는다(measure.yaml에만, 주 대화). 원본 치수 문자(원본표기)나 추정만 받는다.
    역할 선은 {u: 좌표}·{v: 좌표}, 점은 {u, v}, 문자 내용은 문자열. 생성기가 관측 대신 쓴다"""
    data = _measure_only(path, "set-value")
    if eid not in data["elements"]:
        raise RecordError(f"없는 요소: {eid}")
    if source not in ("원본표기", "추정"):
        raise RecordError(f"set-value 출처는 원본표기·추정만(받은 것 {source})")
    entry = {"value": value, "source": source}
    if text:
        entry["text"] = text
    if reason:
        entry["reason"] = reason
    try:
        Value.model_validate(entry)
    except ValidationError as e:
        raise RecordError(f"값 형식 오류: {e}") from e
    data["elements"][eid].setdefault("values", {})[key] = entry
    save(path, data)
    return {"status": "ok", "element": eid, "key": key, "value": entry}


def answer_question(path, qid, answer, by):
    data = _measure_only(path, "질문의 답")
    for q in data["questions"]:
        if q["id"] == qid:
            q["answer"], q["by"] = answer, by
            save(path, data)
            return {"status": "ok", "question": qid}
    raise RecordError(f"없는 질문: {qid}")


def set_interpretation(path, element, attr, value, by=None, pending=False):
    """해석(재료·용도 등). --pending이면 확인 필요, --by user면 사용자 확정, --by main이면 확정"""
    data = _measure_only(path, "해석")
    if element not in data["elements"]:
        raise RecordError(f"없는 요소: {element}")
    if pending:
        entry = {"value": value, "status": "확인필요", "by": "main"}
    elif by in ("user", "main"):
        entry = {"value": value, "status": "사용자확정" if by == "user" else "확정", "by": by}
    else:
        raise RecordError("--by user|main 또는 --pending")
    data["interpretations"].setdefault(element, {})[attr] = entry
    save(path, data)
    return {"status": "ok", "interpretation": f"{element}.{attr}", "entry": entry}


def _values(data):
    """머리말·요소 안의 출처 있는 값: (위치 이름, 값 dict)"""
    for name, fr in data["frames"].items():
        for k in ("x", "y", "deg"):
            yield f"frames.{name}.{k}", fr[k]
    for frame, axes in data["grids"].items():
        for axis, items in axes.items():
            for name, v in items.items():
                yield f"grids.{frame}.{axis}.{name}", v
    for eid, el in data["elements"].items():
        for k, v in el.get("values", {}).items():
            yield f"elements.{eid}.values.{k}", v


def _same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 0.01
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


REVERIFY_FIELDS = {
    "line": ("axis", "coord", "angle_local_deg", "angle_wcs_deg", "point_wcs", "width_px", "tol_mm", "flags",
             "edge", "image_sha256", "calib_sha256"),
    "point": ("kind", "tool", "frame", "point_local", "point_wcs", "bbox_local", "tol_mm", "flags", "image_sha256",
              "calib_sha256")}


def validate(path, lock_path=None, reverify=False):
    data = load(path)
    if data["kind"] != "measure":
        raise RecordError("validate는 measure.yaml에")
    errors, estimates = [], []
    if lock_path and lock_hash(data) != _read_lock(lock_path):
        errors.append("measure.yaml 머리말이 잠금과 다름(잠근 뒤 바뀜)")
    obs = data["observations"]
    for where, v in _values(data):
        if v["source"] == "추정":
            estimates.append(f"{where}: {v['value']} ({v['reason']})")
        refs = [v["ref"]] if v["source"] == "실측" else (v.get("refs") or [])
        for ref in refs:
            name, _, field = ref.partition(".")
            if name not in obs or (field and field not in obs[name]):
                errors.append(f"{where}: 참조한 관측이 없음({ref})")
            elif v["source"] == "실측" and field and not _same(v["value"], obs[name][field]):
                errors.append(f"{where}: 값 {v['value']}이 관측 {ref}({obs[name][field]})과 다름")
    for eid, el in data["elements"].items():
        for role, name in el.get("refs", {}).items():
            if name not in obs:
                errors.append(f"요소 {eid}.{role}: 없는 관측 {name}")
        for role, target in el.get("links", {}).items():
            if target == eid:
                errors.append(f"요소 {eid}.{role}: 자기 자신을 가리킴")
            elif target not in data["elements"]:
                errors.append(f"요소 {eid}.{role}: 없는 요소 {target}")
    for eid in data["interpretations"]:
        if eid not in data["elements"]:
            errors.append(f"해석이 없는 요소를 가리킴: {eid}")
    reverified = 0
    if reverify:
        from tools.raster import measure
        for name, o in obs.items():
            try:
                again = measure.rerun_observation(o["command"], o.get("pick"), o.get("edge"))
            except (ValueError, OSError, SystemExit) as e:
                errors.append(f"관측 {name}: 다시 실행하지 못함({e})")
                continue
            diff = [f"{k} 기록 {o.get(k)} / 재실행 {again.get(k)}" for k in REVERIFY_FIELDS[o.get("kind", "line")]
                    if not _same(o.get(k), again.get(k))]
            if diff:
                errors.append(f"관측 {name}: 재실행 결과와 다름(손으로 고침?): {'; '.join(diff)}")
            reverified += 1
    unresolved = [q for q in data["questions"] if not q.get("answer")]
    pending = [f"{eid}.{attr}" for eid, attrs in data["interpretations"].items()
               for attr, it in attrs.items() if it.get("status") == "확인필요"]
    result = {"status": "error" if errors else "ok", "errors": errors, "estimates": estimates,
              "unresolved_questions": unresolved, "pending_interpretations": pending,
              "ready": not errors and not unresolved and not pending}
    if reverify:
        result["reverified"] = reverified
    return result


def _pairs(items, what):
    out = {}
    for item in items:
        key, sep, val = item.partition("=")
        if not sep or not key or not val:
            raise RecordError(f"--{what}은 이름=값 형식: {item}")
        out[key] = val
    return out


def _element_args(args):
    if args.json:
        if args.id or args.type or args.ref or args.link or args.candidate or args.note or args.frame:
            raise RecordError("--json과 --id·--type·--ref·--link·--candidate·--note를 함께 쓰지 않음")
        try:
            return json.loads(args.json)
        except ValueError as err:
            raise RecordError(f"--json이 JSON이 아님: {err}") from err
    if not args.id or not args.type:
        raise RecordError("--json 또는 --id·--type이 필요함")
    element = {"id": args.id, "type": args.type}
    if args.frame:
        element["frame"] = args.frame
    if args.ref:
        element["refs"] = _pairs(args.ref, "ref")
    if args.link:
        element["links"] = _pairs(args.link, "link")
    if args.candidate:
        element["candidates"] = {k: [c.strip() for c in v.split(",") if c.strip()]
                                 for k, v in _pairs(args.candidate, "candidate").items()}
    if args.note:
        element["note"] = args.note
    return element


def main(argv=None):
    parser = argparse.ArgumentParser(description="평면 이미지 측정 기록 도구")
    sub = parser.add_subparsers(dest="command", required=True)
    i = sub.add_parser("init", help="이미지·보정 파일로 measure.yaml 만들기")
    i.add_argument("--image", required=True)
    i.add_argument("--calib", required=True)
    i.add_argument("--standard", required=True)
    i.add_argument("--k", type=int, required=True)
    i.add_argument("-o", "--out", required=True)
    lk = sub.add_parser("lock", help="머리말 잠금 해시")
    lk.add_argument("measure")
    lk.add_argument("-o", "--out", required=True)
    r = sub.add_parser("region-init", help="판독 에이전트용 영역 파일")
    r.add_argument("measure")
    r.add_argument("--lock", required=True)
    r.add_argument("--region", required=True)
    r.add_argument("-o", "--out", required=True)
    e = sub.add_parser("add-element", help="요소 넣기(관측 참조·출처 있는 값·후보)")
    e.add_argument("file")
    e.add_argument("--json", help='{"id": ..., "type": ..., "refs": {...}, ...}')
    # PowerShell 5.1은 외부 프로그램 인자의 큰따옴표를 지우므로 JSON 없이 쓰는 형식도 받는다
    e.add_argument("--id")
    e.add_argument("--type")
    e.add_argument("--ref", action="append", default=[], help="역할=관측이름(여러 번)")
    e.add_argument("--link", action="append", default=[], help="역할=요소id(여러 번, 예: host=EW1)")
    e.add_argument("--candidate", action="append", default=[], help="속성=후보1,후보2(여러 번)")
    e.add_argument("--note")
    e.add_argument("--frame", help="요소 frame(영역 파일은 영역 frame을 물려받음)")
    e.add_argument("--replace", action="store_true", help="이미 있는 요소를 통째로 바꾼다(재측정: refs·links·후보 모두 다시 줌)")
    sv = sub.add_parser("set-value", help="요소에 원본 치수·추정 값 적기(measure.yaml에만)")
    sv.add_argument("file")
    sv.add_argument("--element", required=True)
    sv.add_argument("--key", required=True, help="역할(f1·s·p 등) 또는 값 이름(content 등)")
    sv.add_argument("--u", type=float, help="지역 u 좌표(세로선 u=값, 점이면 --v와 함께)")
    sv.add_argument("--v", type=float, help="지역 v 좌표(가로선 v=값)")
    sv.add_argument("--value", help="문자 값(그대로 문자열, 예: 실명·단면 번호)")
    sv.add_argument("--number", type=float, help="수 값(간격·길이 등)")
    sv.add_argument("--source", required=True, choices=("원본표기", "추정"))
    sv.add_argument("--text", help="원본표기: 근거가 된 치수 문자")
    sv.add_argument("--reason", help="추정: 이유")
    dr = sub.add_parser("drop-observation", help="참조하지 않는 관측 지우기(재측정으로 버린 관측)")
    dr.add_argument("file")
    dr.add_argument("--name", required=True)
    q = sub.add_parser("question", help="질문")
    qs = q.add_subparsers(dest="action", required=True)
    qa = qs.add_parser("add")
    qa.add_argument("file")
    qa.add_argument("--id", required=True)
    qa.add_argument("--text", required=True)
    qa.add_argument("--element")
    qn = qs.add_parser("answer")
    qn.add_argument("file")
    qn.add_argument("--id", required=True)
    qn.add_argument("--answer", required=True)
    qn.add_argument("--by", choices=("user", "main"), required=True)
    it = sub.add_parser("interpret", help="요소 해석(measure.yaml에만)")
    it.add_argument("file")
    it.add_argument("--element", required=True)
    it.add_argument("--attr", required=True)
    it.add_argument("--value", required=True)
    it.add_argument("--by", choices=("user", "main"))
    it.add_argument("--pending", action="store_true", help="확인 필요로 둔다")
    va = sub.add_parser("validate", help="참조·출처·잠금 검사, 미해결 목록")
    va.add_argument("file")
    va.add_argument("--lock")
    va.add_argument("--reverify", action="store_true", help="관측마다 기록된 명령을 다시 실행해 값 대조")
    mg = sub.add_parser("merge", help="영역 파일을 measure.yaml로 합치기")
    mg.add_argument("measure")
    mg.add_argument("--lock", required=True)
    mg.add_argument("regions", nargs="+")
    mg.add_argument("-o", "--out", required=True)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.command == "init":
            result = init_measure(args.image, args.calib, args.standard, args.k, args.out)
        elif args.command == "lock":
            result = write_lock(args.measure, args.out)
        elif args.command == "region-init":
            result = region_init(args.measure, args.lock, args.region, args.out)
        elif args.command == "add-element":
            result = add_element(args.file, _element_args(args), args.replace)
        elif args.command == "set-value":
            coords = {k: getattr(args, k) for k in ("u", "v") if getattr(args, k) is not None}
            given = [x for x in (coords or None, args.value, args.number) if x is not None]
            if len(given) != 1:
                raise RecordError("값은 --u·--v, --value, --number 중 하나로")
            value = given[0]
            result = set_value(args.file, args.element, args.key, value, args.source, args.text, args.reason)
        elif args.command == "drop-observation":
            result = drop_observation(args.file, args.name)
        elif args.command == "question" and args.action == "add":
            result = add_question(args.file, args.id, args.text, args.element)
        elif args.command == "question":
            result = answer_question(args.file, args.id, args.answer, args.by)
        elif args.command == "interpret":
            result = set_interpretation(args.file, args.element, args.attr, args.value, args.by, args.pending)
        elif args.command == "validate":
            result = validate(args.file, args.lock, args.reverify)
        else:
            result = merge(args.measure, args.lock, args.regions, args.out)
    except (RecordError, OSError) as e:
        result = {"status": "error", "message": str(e)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
