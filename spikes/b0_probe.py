"""B0 사전 검증(제품 코드 아님): E10~E17과 Export의 saved 관찰.

실행 중인 CAD에서만 사용한다. 매번 b0-시각 폴더의 합성 사본을 고치고 저장 없이 닫는다.
예: python spikes/b0_probe.py --only E10,E13 --sizes 100,1000,5000
CAD 없는 검사: python spikes/b0_probe.py --selftest
원시 상태는 반올림하지 않는다. engine.state는 태그의 state 갱신에만 사용한다.
"""
import argparse
import copy
import json
import math
import os
import shutil
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import ezdxf
from ezdxf.addons import Importer

APP = "ARCAID"
RECORD_SCHEMA = "arcaid-change-record/1"
SOURCE = Path(r"C:\arcaid\work\spike\synthetic_base.dwg")
REPO = Path(__file__).resolve().parents[1]
DELTA = (1234.567, -89.123, 0.0)
POINT_KEYS = {"point", "start", "end", "insert", "align_point", "center", "base_point",
              "defpoint", "defpoint2", "defpoint3", "defpoint4", "text_midpoint"}


def plain(value):
    """벡터·태그도 JSON 값으로 바꾸되 실수 정밀도를 그대로 둔다."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    return [plain(v) for v in value]


def raw_text(entity):
    d = entity.dxf
    return {k: plain(d.get(k, default)) for k, default in (
        ("insert", None), ("align_point", None), ("height", 0), ("rotation", 0),
        ("width", 1), ("oblique", 0), ("halign", 0), ("valign", 0),
        ("style", "Standard"), ("text", ""), ("extrusion", (0, 0, 1)),
        ("text_generation_flag", 0), ("thickness", 0))}


def raw_state(entity):
    """DXF 원시 비교 상태. 속성 위치·해치 경계·다른 앱의 XDATA도 보존한다."""
    d, kind = entity.dxf, entity.dxftype()
    state = {"type": kind, "layer": d.get("layer", "0"), "color": d.get("color", 256),
             "true_color": d.get("true_color"), "linetype": d.get("linetype", "BYLAYER"),
             "lineweight": d.get("lineweight", -1),
             "xdata": {app: plain(tags) for app, tags in sorted(entity.xdata.data.items())}
             if entity.xdata else {}}
    if kind == "LINE":
        state.update(start=plain(d.start), end=plain(d.end),
                     extrusion=plain(d.get("extrusion", (0, 0, 1))), thickness=d.get("thickness", 0))
    elif kind == "LWPOLYLINE":
        elevation = d.get("elevation", 0)
        state.update(points=[{"point": [x, y, elevation], "start_width": sw, "end_width": ew, "bulge": b}
                             for x, y, sw, ew, b in entity.get_points("xyseb")],
                     closed=bool(entity.closed), elevation=elevation, const_width=d.get("const_width", 0),
                     extrusion=plain(d.get("extrusion", (0, 0, 1))), thickness=d.get("thickness", 0))
    elif kind in ("TEXT", "ATTRIB", "ATTDEF"):
        state.update(raw_text(entity))
        if kind != "TEXT":
            state.update(tag=d.tag, flags=d.get("flags", 0))
    elif kind == "INSERT":
        state.update(name=d.name, insert=plain(d.insert), xscale=d.get("xscale", 1),
                     yscale=d.get("yscale", 1), zscale=d.get("zscale", 1), rotation=d.get("rotation", 0),
                     extrusion=plain(d.get("extrusion", (0, 0, 1))),
                     attribs=[{"handle": a.dxf.handle, **raw_state(a)}
                              for a in sorted(entity.attribs, key=lambda a: (a.dxf.tag, a.dxf.handle))])
    elif kind == "HATCH":
        paths = []
        for path in entity.paths:
            p = {"flags": path.path_type_flags,
                 "source_boundary_handles": list(path.source_boundary_objects)}
            if hasattr(path, "vertices"):
                p.update(closed=bool(path.is_closed),
                         vertices=[{"point": [v[0], v[1]], "bulge": v[2]} for v in path.vertices])
            else:
                p["edges"] = [{"type": type(edge).__name__,
                               **{k: plain(v) for k, v in vars(edge).items() if not k.startswith("_")}}
                              for edge in path.edges]
            paths.append(p)
        state.update(paths=paths, pattern=d.pattern_name, solid=d.solid_fill,
                     pattern_scale=d.get("pattern_scale", 1), pattern_angle=d.get("pattern_angle", 0),
                     pattern_double=d.get("pattern_double", 0), associative=d.associative,
                     pattern_definition=plain(entity.pattern.as_list()) if entity.pattern else None,
                     elevation=plain(d.elevation), extrusion=plain(d.get("extrusion", (0, 0, 1))))
    else:
        # 치수 등 관계 실험의 관찰 대상도 정의점·문자·스타일을 빠뜨리지 않는다.
        state["dxf"] = {k: plain(v) for k, v in d.all_existing_dxf_attribs().items()
                        if k not in ("handle", "owner")}
        if kind == "MTEXT":
            state["text"] = entity.text
    return state


def compare_raw(before, after):
    """허용오차 없이 비교한다. 좌표 거리(mm)와 일반 값의 차이를 따로 집계한다."""
    result = {"same": True, "max_dev_mm": 0.0, "max_value_diff": 0.0,
              "difference_count": 0, "differences": []}

    def diff(path, a, b):
        result["same"] = False
        result["difference_count"] += 1
        if len(result["differences"]) < 80:
            result["differences"].append({"path": path, "before": a, "after": b})

    def walk(a, b, path, key=""):
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(a.keys() | b.keys()):
                if k not in a or k not in b:
                    diff(f"{path}/{k}", a.get(k), b.get(k))
                else:
                    walk(a[k], b[k], f"{path}/{k}", k)
        elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            if key in POINT_KEYS and len(a) == len(b) and all(
                    isinstance(v, (int, float)) for v in list(a) + list(b)):
                result["max_dev_mm"] = max(result["max_dev_mm"], math.dist(a, b))
            if len(a) != len(b):
                diff(path + "/length", len(a), len(b))
            for i, (av, bv) in enumerate(zip(a, b)):
                walk(av, bv, f"{path}/{i}")
        elif a != b:
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                delta = abs(a - b)
                result["max_value_diff"] = max(result["max_value_diff"], delta)
                if key in {"elevation", "height", "start_width", "end_width", "const_width", "thickness"}:
                    result["max_dev_mm"] = max(result["max_dev_mm"], delta)
            diff(path, a, b)

    walk(before, after, "")
    return result


def states(doc):
    return {e.dxf.handle: raw_state(e) for e in doc.modelspace()}


def tags(entity):
    return plain(entity.get_xdata(APP)) if entity.has_xdata(APP) else None


def record_chunks(payload):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return [RECORD_SCHEMA] + [text[i:i + 250] for i in range(0, len(text), 250)]


def join_record(values):
    if not values or values[0] != RECORD_SCHEMA:
        raise ValueError("적용 기록의 버전 표식이 다름")
    if any(not isinstance(v, str) or len(v) > 250 for v in values[1:]):
        raise ValueError("적용 기록 조각은 250자 이하 문자열이어야 함")
    return json.loads("".join(values[1:]))


def read_records(doc):
    if APP not in doc.rootdict:
        return {}
    return {name: join_record([tag.value for tag in record.tags if tag.code == 1])
            for name, record in doc.rootdict[APP].items()}


def payload(op, handles, action="move", after_hash=None):
    return {"op": op, "in": "0123456789abcdef", "targets": {
        h: {"action": action, "after_hash": after_hash} for h in handles}, "result": "applied"}


class Probe:
    """하네스 위에 활성 도면 확인·호출별 guard·즉시 닫기를 더한다."""

    def __init__(self, s, work, std):
        self.s, self.ctl, self.work, self.std = s, s.ctl, work, std
        self.expected = None
        self.in_mark = False
        self.info = {}
        self.serial = 0
        self.marker = None
        self.changed = 0

    def guard(self):
        self.s.guard()
        active = self.ctl._call(getattr, self.ctl.app, "ActiveDocument")
        self.s.guard(active)
        current = self.ctl._call(getattr, self.ctl.doc, "FullName")
        active_path = self.ctl._call(getattr, active, "FullName")
        if norm(current) != norm(self.expected) or norm(active_path) != norm(self.expected):
            raise RuntimeError(f"활성 문서가 이 시나리오의 시험 도면이 아님: {active_path}")

    def write(self, fn, *args, **kwargs):
        self.guard()
        return fn(*args, **kwargs)

    def obj(self, handle):
        return self.ctl._call_attr(self.ctl.doc, "HandleToObject", str(handle))

    def attr(self, obj, name, value, changed=True):
        self.write(self.ctl._call, setattr, obj, name, value)
        if changed:
            self.changed += 1

    def move(self, handle, delta=DELTA):
        self.write(self.ctl._call_attr, self.obj(handle), "Move", self.ctl._pt((0, 0, 0)), self.ctl._pt(delta))
        self.changed += 1

    def delete(self, handles):
        count, failed = self.write(self.ctl.delete_handles, list(handles))
        self.changed += count
        self.info["deleted_count"] = self.info.get("deleted_count", 0) + count
        self.info.setdefault("delete_failed", []).extend(failed)
        if failed:
            raise RuntimeError(f"COM Delete 실패 핸들: {failed}")

    def begin(self):
        if self.in_mark:
            raise RuntimeError("중첩 묶음은 허용하지 않음")
        self.write(self.ctl.start_undo)
        self.in_mark = True
        self.changed = 0

    def end(self, key="end_undo_ok"):
        ok = self.write(self.ctl.end_undo)
        self.info[key] = ok
        self.in_mark = not ok
        if not ok:
            self.info["end_undo_failed"] = True
        return ok

    def command(self, command):
        if self.in_mark:
            raise RuntimeError("묶음 안 명령·LISP는 금지")
        result = self.write(self.s.cmd, command)
        if not result.get("ok") or result.get("pending") or result.get("cmdactive", 0):
            raise RuntimeError(f"준비 명령 미완료: {result}")
        return result

    def snap(self, name):
        self.serial += 1
        out = self.work / f"{self.label}_{self.serial}_{name}"
        result = self.write(self.ctl.export_dxf, str(out))
        self.info.setdefault("exports", []).append(result)
        if not result["ok"]:
            raise RuntimeError(f"Export 실패: {result['error']}")
        return ezdxf.readfile(result["path"])

    def mark(self):
        obj = self.write(self.ctl.draw_line, (-5000, -5000), (-4000, -5000), layer="TXT")
        self.marker = self.handle(obj)
        self.info["marker"] = self.marker

    def handle(self, obj):
        if obj is None:
            raise RuntimeError("COM 객체 준비 실패(None)")
        return str(self.ctl._call(getattr, obj, "Handle"))

    def close(self, key="close_ok"):
        try:
            ok = self.write(self.ctl.close_drawing, False)
            self.info[key] = ok
            if ok:
                self.expected = None
            else:
                self.info.setdefault("close_failed", []).append(str(self.expected))
            return ok
        except Exception as e:  # noqa: BLE001
            self.info[key] = False
            self.info.setdefault("close_failed", []).append(repr(e))
            return False

    @contextmanager
    def scenario(self, label):
        self.label, self.info = label, {"raised": None, "delete_failed": [], "timeout_reproduced": False}
        self.marker, self.changed, self.in_mark = None, 0, False
        self.expected = self.work / f"{label}.dwg"
        opened = False
        try:
            self.s.copy_and_open(self.work / "synthetic_base.dwg", self.expected.name)
            opened = True
            self.guard()
            yield self.info
        except Exception as e:  # noqa: BLE001
            self.failure(e)
        finally:
            if not opened:
                # Documents.Open은 성공했으나 뒤의 COM 읽기가 실패했을 가능성도 확인한다.
                try:
                    active = self.ctl._call(getattr, self.ctl.app, "ActiveDocument")
                    if norm(self.ctl._call(getattr, active, "FullName")) == norm(self.expected):
                        self.ctl.doc = active
                        opened = True
                except Exception as e:  # noqa: BLE001
                    self.info["open_failure_cleanup_check"] = repr(e)
            if opened:
                if self.in_mark:
                    try:
                        self.end("cleanup_end_undo_ok")
                    except Exception as e:  # noqa: BLE001
                        self.info["cleanup_end_undo_raised"] = repr(e)
                self.close()
            self.s.record(label, self.info)

    def failure(self, error):
        self.info["raised"] = repr(error)
        self.info["failure_kind"] = "기록 쓰기 실패" if self.info.get(
            "phase") == "record_write" else ("일부 성공 후 실패" if self.changed else "변경 전 실패")
        self.info["successful_writes"] = self.changed

    def verdict(self, before, after, targets):
        a, b = states(before), states(after)
        targets = set(targets)
        target = compare_raw({h: a[h] for h in targets if h in a}, {h: b[h] for h in targets if h in b})
        other = compare_raw({h: v for h, v in a.items() if h not in targets},
                            {h: v for h, v in b.items() if h not in targets})
        tag_same = all(h in b and a[h]["xdata"].get(APP) == b[h]["xdata"].get(APP)
                       for h in targets if h in a)
        marker_kept = self.marker in b and self.marker in a and a[self.marker] == b[self.marker]
        same_handles = a.keys() == b.keys()
        return {"restored": same_handles and target["same"] and other["same"] and marker_kept,
                "same_handles": same_handles, "missing": sorted(a.keys() - b.keys()),
                "extra": sorted(b.keys() - a.keys()), "targets": target, "non_targets": other,
                "max_dev_mm": max(target["max_dev_mm"], other["max_dev_mm"]),
                "marker_kept": marker_kept, "same_arcaid_tags": tag_same}

    def undo(self, before, targets, allow_open=False):
        if self.in_mark and not allow_open:
            self.info["undo_skipped"] = "EndUndoMark 실패: 닫힌 묶음인지 확인 불가"
            return None
        self.info["cmdactive_before_undo"] = self.ctl._read_cmdactive()
        # E12(d)만 미종료 묶음에서 UNDO를 보내는 의도적인 예외다. 다른 명령은 준비 단계에만 보낸다.
        self.info["undo"] = self.write(self.ctl.undo_once)
        self.info["cmdactive_after_undo"] = self.ctl._read_cmdactive()
        after = self.snap("restored")
        self.info.update(self.verdict(before, after, targets))
        return after

    def grouped(self, before, targets, body):
        self.begin()
        try:
            body()
        except Exception as e:  # noqa: BLE001
            self.failure(e)
        finally:
            self.end()
        return self.undo(before, targets)

    def set_states(self, doc, handles):
        from engine.state import state_hash

        for h in handles:
            entity = doc.entitydb[h]
            old = [v for code, v in entity.get_xdata(APP) if code == 1000]
            strings = [v for v in old if not v.startswith("state=")] + [f"state={state_hash(entity)}"]
            self.write(self.ctl.set_xdata, h, strings)
            self.changed += 1

    def put_record(self, value):
        import pythoncom
        import win32com.client

        self.info["phase"] = "record_write"
        dictionaries = self.ctl._call(getattr, self.ctl.doc, "Dictionaries")
        try:
            dictionary = self.ctl._call_attr(dictionaries, "Item", APP)
        except Exception:  # noqa: BLE001
            dictionary = self.write(self.ctl._call_attr, dictionaries, "Add", APP)
        record = self.write(self.ctl._call_attr, dictionary, "AddXRecord", value["op"])
        strings = record_chunks(value)
        types = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I2, [1] * len(strings))
        values = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_VARIANT, strings)
        self.write(self.ctl._call_attr, record, "SetXRecordData", types, values)
        self.changed += 1
        self.info["phase"] = "after_record_write"


def norm(path):
    return os.path.normcase(os.path.abspath(str(path)))


def test_items():
    return [
        {"kind": "line", "id": "line", "layer": "WAL", "start": [0.123456789, 0], "end": [1000, 0]},
        {"kind": "polyline", "id": "poly", "layer": "WAL", "points": [[0, 500], [1000, 500], [1000, 1500]],
         "bulges": [0.25, -0.125, 0], "closed": False},
        {"kind": "text", "id": "mc", "layer": "ROM", "role": "general", "content": "가", "at": [500, 2000], "align": "MC"},
        {"kind": "text", "id": "left", "layer": "TXT", "role": "general", "content": "왼쪽", "at": [0, 2500], "align": "L"},
        {"kind": "block", "id": "block", "name": "축열버블-상", "at": [1000, 3000], "attribs": {"축열": "X1"}},
    ]


def build(q, name, items):
    from engine.write import build_dxf

    out = q.work / f"{name}.dxf"
    q.guard()
    result = build_dxf({"schema_version": 1, "standard": q.std.ref, "op": name,
                        "units": "mm", "scale": 100, "items": items}, q.std, out)
    if result.status.value != "success":
        raise RuntimeError(f"시험 DXF 생성 실패: {result.model_dump(mode='json')}")
    return out


def prepare(q):
    generated = build(q, q.label, test_items())
    q.write(q.ctl.import_dxf, str(generated))
    human = [q.handle(q.write(q.ctl.draw_line, (5000, 0), (6000, 0), layer="WAL")),
             q.handle(q.write(q.ctl.draw_text, (5000, 500), "사람 문자", height=135, layer="TXT"))]
    q.mark()
    before = q.snap("before")
    engine = {}
    for entity in before.modelspace():
        if entity.has_xdata(APP):
            values = dict(v.split("=", 1) for code, v in entity.get_xdata(APP) if code == 1000 and "=" in v)
            if values.get("op") == q.label:
                engine[values["oid"].split(":", 1)[1]] = entity.dxf.handle
    if set(engine) != {"line", "poly", "mc", "left", "block"}:
        raise RuntimeError(f"Import 뒤 엔진 객체 5개를 찾지 못함: {engine}")
    if any(before.entitydb[h].has_xdata(APP) for h in human):
        raise RuntimeError("사람 객체에 ARCAID 태그가 붙어 있음")
    q.info.update(engine=engine, human=human)
    return before, engine, human


def e10(q):
    with q.scenario("E10_delete_restore") as info:
        before, engine, human = prepare(q)
        targets = list(engine.values()) + human
        deleted_old_handles = set(targets) | {a.dxf.handle for h in targets
                                             if before.entitydb[h].dxftype() == "INSERT" for a in before.entitydb[h].attribs}
        info["deleted_old_handles_including_attribs"] = sorted(deleted_old_handles)

        def apply():
            q.delete(targets)
            deleted = q.snap("deleted_inside_mark")
            info["all_targets_deleted"] = not (set(targets) & states(deleted).keys())
            info["inside"] = q.verdict(before, deleted, targets)

        after = q.grouped(before, targets, apply)
        if after is not None:
            new = q.write(q.ctl.draw_line, (9000, 0), (10000, 0), layer="WAL")
            h = q.handle(new)
            info.update(new_handle=h, new_handle_reuses_deleted=h in deleted_old_handles)
            info["new_line_seen_in_export"] = h in states(q.snap("new_handle"))


def positions(doc, handles):
    out = {}
    for h in handles:
        state = raw_state(doc.entitydb[h])
        out[h] = {k: state[k] for k in ("insert", "align_point", "attribs") if k in state}
    return out


def move_precision(before, after, handles, delta=DELTA):
    """OCS=WCS로 준비한 객체의 실제 이동을 관찰한다(정렬점도 각각 기록)."""
    errors = []

    def inspect(a, b, prefix):
        for key in ("start", "end", "insert", "align_point"):
            if a.get(key) is not None and b.get(key) is not None:
                expected = [v + delta[i] for i, v in enumerate(a[key])]
                errors.append({"path": prefix + "/" + key, "before": a[key], "after": b[key],
                               "expected": expected, "dev_mm": math.dist(expected, b[key])})
        for i, (av, bv) in enumerate(zip(a.get("points", []), b.get("points", []))):
            inspect({"insert": av["point"]}, {"insert": bv["point"]}, f"{prefix}/points/{i}")
        for i, (av, bv) in enumerate(zip(a.get("attribs", []), b.get("attribs", []))):
            inspect(av, bv, f"{prefix}/attribs/{i}")

    for h in handles:
        inspect(raw_state(before.entitydb[h]), raw_state(after.entitydb[h]), h)
    return {"max_dev_mm": max((r["dev_mm"] for r in errors), default=0), "coordinates": errors}


def e11(q):
    with q.scenario("E11_move_properties") as info:
        before, engine, human = prepare(q)
        targets = list(engine.values())

        def apply():
            for h in targets:
                q.move(h)
            moved = q.snap("moved_inside_mark")
            info["move_precision"] = move_precision(before, moved, targets)
            info["positions_before"] = positions(before, targets)
            info["positions_after_move"] = positions(moved, targets)
            for key in ("mc", "left"):
                q.attr(q.obj(engine[key]), "TextString", "길이가 아주 달라진 정렬 문자 ABCDEFG")
            block = q.obj(engine["block"])
            attribs = q.ctl._call_attr(block, "GetAttributes") or []
            info["attribute_tags"] = [str(q.ctl._call(getattr, a, "TagString")) for a in attribs]
            if not attribs:
                raise RuntimeError("속성 블록의 GetAttributes가 비어 있음")
            for attrib in attribs:
                q.attr(attrib, "TextString", "X123456789-길이변경")
            text_changed = q.snap("text_changed_inside_mark")
            info["positions_after_text"] = positions(text_changed, targets)
            info["text_recalculation"] = compare_raw(positions(moved, targets), positions(text_changed, targets))
            obj = q.obj(engine["line"])
            q.attr(obj, "Layer", "DIM")
            q.attr(obj, "Color", 256)
            aci = q.snap("aci256_inside_mark")
            info["color_256"] = raw_state(aci.entitydb[engine["line"]])
            color = q.ctl._call(getattr, obj, "TrueColor")
            q.write(q.ctl._call_attr, color, "SetRGB", 17, 83, 201)
            q.attr(obj, "TrueColor", color)
            q.attr(obj, "Linetype", "Continuous")
            q.attr(obj, "Lineweight", 50)
            applied = q.snap("properties_inside_mark")
            info["applied"] = {h: raw_state(applied.entitydb[h]) for h in targets}
            info["untouched_inside"] = q.verdict(before, applied, targets)["non_targets"]

        q.grouped(before, targets, apply)


def e12(q):
    for variant in ("a_first_failure", "b_partial", "c_postcheck", "d_unclosed"):
        with q.scenario(f"E12_{variant}") as info:
            before, engine, human = prepare(q)
            targets = list(engine.values())
            info["timeout_note"] = "진짜 타임아웃은 재현하지 않음"
            q.begin()
            try:
                if variant.startswith("a_"):
                    # StartUndoMark 다음 첫 COM 호출이 잘못된 핸들 조회다.
                    q.ctl._call_attr(q.ctl.doc, "HandleToObject", "FFFFFFFFFFFFFFF")
                    raise RuntimeError("없는 핸들 조회가 예외 없이 끝남")
                for h in targets[:3]:
                    q.move(h)
                if variant.startswith("b_"):
                    raise RuntimeError("3개 변경 뒤 의도한 실패")
                info["inside"] = q.verdict(before, q.snap("applied_inside_mark"), targets)
            except Exception as e:  # noqa: BLE001
                q.failure(e)
            if variant.startswith("d_"):
                info["intentional_open_mark_undo"] = True
                q.undo(before, targets, allow_open=True)
                q.end("end_undo_after_open_undo_ok")
            else:
                q.end()
                if variant.startswith("c_"):
                    info["phase"] = "postcheck"
                    q.failure(RuntimeError("정상 적용·묶음 닫은 뒤 사후 확인 실패를 가정"))
                q.undo(before, targets)


def save_reopen(q, name):
    path = q.work / f"{name}.dwg"
    q.write(q.ctl._call_attr, q.ctl.doc, "SaveAs", str(path))
    q.expected = path
    q.s.opened.append(norm(path))
    q.info["saved_copy"] = str(path)
    if not q.close("close_before_reopen_ok"):
        raise RuntimeError("재열기 전 사본 닫기 실패")
    q.expected = path
    q.s.open(path)
    q.guard()


def e13(q):
    for variant in ("new_dictionary", "existing", "korean_boundary", "handles5000"):
        with q.scenario(f"E13_{variant}") as info:
            before, engine, human = prepare(q)
            old = payload("previous-op", [engine["line"]])
            if variant == "existing":
                q.put_record(old)
            value = payload("new-op", list(engine.values()))
            if variant == "korean_boundary":
                # 입력 해시 자리의 긴 합성 문자열로 여러 조각 경계의 양옆에 한글이 오게 한다.
                value["in"] = "한글경계검증" * 173
            if variant == "handles5000":
                value = payload("new-op", [f"{i + 0x10000:X}" for i in range(5000)])
            chunks = record_chunks(value)
            info.update(chunk_count=len(chunks) - 1, max_chunk_chars=max(map(len, chunks[1:])),
                        korean_at_boundary=any(any(ord(c) > 127 for c in a[-1:] + b[:1])
                                               for a, b in zip(chunks[1:], chunks[2:])))
            q.put_record(value)
            exported = q.snap("record")
            expected = {"previous-op": old, "new-op": value} if variant == "existing" else {"new-op": value}
            info["export_record_same"] = read_records(exported) == expected
            info["objects_after_record"] = q.verdict(before, exported, engine.values())
            save_reopen(q, f"{q.label}_saved")
            reopened = q.snap("reopened")
            info["reopen_record_same"] = read_records(reopened) == expected
            info["objects_after_reopen"] = q.verdict(before, reopened, engine.values())
            # 손으로 DXF를 재저장하지 않고, 마지막 COM Export 사본을 fixture 후보로 보존한다.
            q.guard()
            shutil.copyfile(info["exports"][-1]["path"], q.work / "b0_record.dxf")
            info["fixture"] = str(q.work / "b0_record.dxf")

    for previous in (False, True):
        with q.scenario(f"E13_atomic_{'existing' if previous else 'new_dictionary'}") as info:
            unused, engine, human = prepare(q)
            old = payload("previous-op", [engine["left"]])
            if previous:
                q.put_record(old)
            q.mark()
            before = q.snap("before_atomic")
            info["dictionary_existed_before"] = APP in before.rootdict
            targets = list(engine.values())

            def apply():
                from engine.state import state_hash

                q.move(engine["line"])
                q.delete([engine["poly"]])
                moved = q.snap("moved_for_state")
                q.set_states(moved, [engine["line"]])
                record = payload("atomic-op", [engine["line"]], after_hash=state_hash(moved.entitydb[engine["line"]]))
                record["targets"][engine["poly"]] = {"action": "delete", "after_hash": None}
                q.put_record(record)
                applied = q.snap("atomic_inside_mark")
                info["new_record_applied"] = "atomic-op" in read_records(applied)
                info["state_tag_changed"] = tags(before.entitydb[engine["line"]]) != tags(applied.entitydb[engine["line"]])

            after = q.grouped(before, targets, apply)
            if after is not None:
                records = read_records(after)
                info.update(previous_record_same=records.get("previous-op") == (old if previous else None),
                            new_record_gone="atomic-op" not in records, records_restored=records == read_records(before),
                            dictionary_present_after_undo=APP in after.rootdict)

    # 기록 쓰기 실패를 객체 변경 실패와 구별하고, 이전 기록까지 되돌리는지 관찰한다.
    with q.scenario("E13_record_write_failure") as info:
        unused, engine, human = prepare(q)
        old = payload("duplicate-op", [engine["line"]])
        q.put_record(old)
        q.mark()
        before = q.snap("before_record_failure")

        def apply_fail():
            q.move(engine["line"])
            q.put_record(old)  # 같은 이름의 AddXRecord가 오류를 내는지 실제 COM에 묻는다.
            info["duplicate_record_accepted"] = True
            info["phase"] = "record_write"
            raise RuntimeError("기록 쓰기 실패를 가정한 예외(중복 이름이 허용된 경우)")

        after = q.grouped(before, [engine["line"]], apply_fail)
        if after is not None:
            info["previous_record_same"] = read_records(after) == {"duplicate-op": old}


def matrix_layers(q):
    layers = q.ctl._call(getattr, q.ctl.doc, "Layers")
    normal = q.ctl._call_attr(layers, "Item", "0")
    q.attr(q.ctl.doc, "ActiveLayer", normal, changed=False)
    objects = {}
    for name, prop in (("B0_LOCK", "Lock"), ("B0_FROZEN", "Freeze"), ("B0_OFF", "LayerOn")):
        layer = q.write(q.ctl._call_attr, layers, "Add", name)
        line = q.write(q.ctl.draw_line, (20000, 0), (21000, 0), layer=name)
        text = q.write(q.ctl.draw_text, (20000, 500), "레이어 시험", height=135, layer=name)
        objects[name] = {"line": q.handle(line), "text": q.handle(text)}
        q.attr(layer, prop, prop != "LayerOn", changed=False)
    return objects


def e14(q):
    cases = []
    for layer in ("B0_LOCK", "B0_FROZEN", "B0_OFF"):
        for op in ("move_line", "move_text", "layer_line", "layer_text", "text"):
            cases.append(("source", layer, op))
    for layer in ("B0_LOCK", "B0_FROZEN", "B0_OFF", "B0_NO_SUCH_LAYER"):
        for kind in ("line", "text"):
            cases.append(("destination", layer, f"layer_{kind}"))
    cases.append(("linetype", "B0_NO_SUCH_LINETYPE", "line"))
    for side, layer, op in cases:
        with q.scenario(f"E14_{side}_{layer}_{op}") as info:
            unused, engine, human = prepare(q)
            objects = matrix_layers(q)
            kind = "text" if op.endswith("text") else "line"
            h = objects[layer][kind] if side == "source" else (engine["left"] if kind == "text" else engine["line"])
            q.mark()
            before = q.snap("matrix_before")
            info.update(side=side, condition=layer, operation=op, target=h,
                        layer_flags={e.dxf.name: {"locked": e.is_locked(), "frozen": e.is_frozen(), "off": e.is_off()}
                                     for e in before.layers if e.dxf.name.startswith("B0_")})

            def apply():
                raised = None
                try:
                    if side == "linetype":
                        q.attr(q.obj(h), "Linetype", layer)
                    elif op.startswith("move"):
                        q.move(h)
                    elif op.startswith("layer"):
                        q.attr(q.obj(h), "Layer", "WAL" if side == "source" else layer)
                    else:
                        q.attr(q.obj(h), "TextString", "바뀐 레이어 문자 길이")
                except Exception as e:  # noqa: BLE001
                    raised = repr(e)
                    q.failure(e)
                applied = q.snap("matrix_applied")
                observation = compare_raw(raw_state(before.entitydb[h]), raw_state(applied.entitydb[h]))
                info.update(operation_raised=raised, observation=observation,
                            outcome=("raised_after_change" if raised and not observation["same"] else
                                     "raised" if raised else "ignored" if observation["same"] else "applied"),
                            applied_state=raw_state(applied.entitydb[h]))

            q.grouped(before, [h], apply)

    with q.scenario("E14_constant_attributes") as info:
        unused, engine, human = prepare(q)
        doc = ezdxf.new("R2013")
        block = doc.blocks.new("B0_CONST")
        block.add_attdef("CONST", (0, 0), text="고정값", height=135, dxfattribs={"flags": 2})
        block.add_attdef("NORMAL", (0, 200), text="일반값", height=135)
        doc.modelspace().add_blockref("B0_CONST", (25000, 0)).add_auto_attribs({"CONST": "고정값", "NORMAL": "일반값"})
        path = q.work / "constant_attributes.dxf"
        q.guard()
        doc.saveas(path)
        q.write(q.ctl.import_dxf, str(path))
        q.mark()
        before = q.snap("constant_before")
        handles = [e.dxf.handle for e in before.modelspace() if e.dxftype() == "INSERT" and e.dxf.name == "B0_CONST"]
        if len(handles) != 1:
            raise RuntimeError(f"상수 속성 시험 INSERT를 찾지 못함: {handles}")
        obj = q.obj(handles[0])
        arrays = {}
        for method in ("GetAttributes", "GetConstantAttributes"):
            try:
                arrays[method] = list(q.ctl._call_attr(obj, method) or [])
                info[method] = [{"tag": str(q.ctl._call(getattr, a, "TagString")),
                                 "text": str(q.ctl._call(getattr, a, "TextString")),
                                 "handle": q.handle(a)} for a in arrays[method]]
            except Exception as e:  # noqa: BLE001
                arrays[method] = []
                info[method] = {"raised": repr(e)}
        info["missing_tag_absent"] = all(q.ctl._call(getattr, a, "TagString") != "MISSING" for a in arrays["GetAttributes"])
        info["missing_tag_write"] = {"attempted": False, "reason": "MISSING 태그가 GetAttributes 목록에 없으면 쓸 COM 속성 객체도 없음"}

        def apply_constant():
            attempts = []
            for a in arrays["GetAttributes"] + arrays["GetConstantAttributes"]:
                if q.ctl._call(getattr, a, "TagString") != "CONST":
                    continue
                attempt = {"handle": q.handle(a), "raised": None}
                try:
                    q.attr(a, "TextString", "상수쓰기시도-길이변경")
                    attempt["read_back"] = str(q.ctl._call(getattr, a, "TextString"))
                except Exception as e:  # noqa: BLE001
                    attempt["raised"] = repr(e)
                attempts.append(attempt)
            info["constant_write_attempts"] = attempts
            info["constant_unavailable"] = not attempts
            normal_attempts = []
            for a in arrays["GetAttributes"]:
                if q.ctl._call(getattr, a, "TagString") != "NORMAL":
                    continue
                attempt = {"handle": q.handle(a), "raised": None}
                try:
                    q.attr(a, "TextString", "일반속성-대조군변경")
                    attempt["read_back"] = str(q.ctl._call(getattr, a, "TextString"))
                except Exception as e:  # noqa: BLE001
                    attempt["raised"] = repr(e)
                normal_attempts.append(attempt)
            info["normal_write_control"] = normal_attempts
            applied = q.snap("constant_applied")
            info["insert_after"] = raw_state(applied.entitydb[handles[0]])
            info["attdefs_before"] = [raw_state(e) for e in before.blocks["B0_CONST"] if e.dxftype() == "ATTDEF"]
            info["attdefs_after"] = [raw_state(e) for e in applied.blocks["B0_CONST"] if e.dxftype() == "ATTDEF"]

        after = q.grouped(before, handles, apply_constant)
        if after is not None:
            info["attdefs_restored"] = compare_raw(info["attdefs_before"], [raw_state(e) for e in after.blocks["B0_CONST"]
                                                                                   if e.dxftype() == "ATTDEF"])


def relations(doc):
    return {"entities": {e.dxf.handle: {"type": e.dxftype(), "reactors": list(e.get_reactors()),
                                        "has_extension_dict": bool(e.has_extension_dict),
                                        "extension_entries": {name: {"type": value.dxftype(), "handle": value.dxf.handle}
                                                              for name, value in e.extension_dict.dictionary.items()}
                                        if e.has_extension_dict else {}} for e in doc.modelspace()},
            "groups": {name: [e.dxf.handle for e in group] for name, group in doc.groups},
            "hatches": {e.dxf.handle: {"associative": e.dxf.associative,
                                       "source_boundaries": [list(p.source_boundary_objects) for p in e.paths]}
                        for e in doc.modelspace() if e.dxftype() == "HATCH"}}


def relation_setup(q, relation, engine, human):
    if relation == "group":
        import pythoncom
        import win32com.client

        members = [engine["line"], human[0]]
        groups = q.ctl._call(getattr, q.ctl.doc, "Groups")
        group = q.write(q.ctl._call_attr, groups, "Add", "B0_G")
        values = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [q.obj(h) for h in members])
        q.write(q.ctl._call_attr, group, "AppendItems", values)
        return members[:1], {"members": members}
    if relation == "hatch":
        q.write(q.ctl._call_attr, q.ctl.doc, "SetVariable", "HPASSOC", 1)
        before = q.snap("before_hatch")
        hatch = q.write(q.ctl.draw_hatch, [(30000, 0), (31000, 0), (31000, 1000), (30000, 1000)],
                        pattern_name="SOLID", layer="WAL")
        h = q.handle(hatch)
        prepared = q.snap("hatch_prepared")
        boundaries = [e.dxf.handle for e in prepared.modelspace() if e.dxftype() == "LWPOLYLINE"
                      and e.dxf.handle not in states(before)]
        associative = bool(q.ctl._call(getattr, hatch, "AssociativeHatch"))
        if len(boundaries) != 1:
            raise RuntimeError(f"해치 경계 폴리라인 식별 실패: {boundaries}")
        available = associative and bool(prepared.entitydb[h].dxf.associative)
        return boundaries if available else [], {"hatch": h, "AssociativeHatch": associative,
                                                  "availability": "available" if available else "unavailable"}
    # 객체 스냅의 _END + _NON을 명시해 기존 선 끝점과 연관된 치수를 준비한다.
    q.write(q.ctl._call_attr, q.ctl.doc, "SetVariable", "DIMASSOC", 2)
    h = engine["line"]
    # 바탕 도면의 선에 잘못 스냅되지 않도록 준비 단계에서 독립된 위치로 옮긴다.
    q.move(h, (60000, 60000, 0))
    before = q.snap("before_dimension")
    line = before.entitydb[h]
    p1, p2 = line.dxf.start, line.dxf.end
    command = (f"_.DIMLINEAR\n_END\n{p1.x},{p1.y}\n_END\n{p2.x},{p2.y}\n"
               f"_NON\n{(p1.x + p2.x) / 2},{p1.y - 700}\n")
    try:
        q.command("_.ZOOM\n_C\n60500,60000\n5000\n")
        q.command(command)
    except Exception as e:  # noqa: BLE001
        q.write(q.ctl.cancel_command)
        return [], {"availability": "unavailable", "raised": repr(e)}
    prepared = q.snap("dimension_prepared")
    dims = [e.dxf.handle for e in prepared.modelspace() if e.dxftype() == "DIMENSION"
            and e.dxf.handle not in states(before)]
    observed = relations(prepared)
    associated = bool(dims) and any(
        "ACAD_DIMASSOC" in observed["entities"][dh]["extension_entries"] or
        any("DIMASSOC" in value["type"] for value in observed["entities"][dh]["extension_entries"].values()) for dh in dims)
    return [h] if associated else [], {"dimensions": dims, "availability": "available" if associated else "unavailable",
                                       "note": "DIMASSOC=2 생성 뒤 확장 사전의 DIMASSOC 항목으로 연관 후보를 확인함",
                                       "detected": observed}


def e15(q):
    for relation in ("group", "hatch", "dimension"):
        for action in ("move", "delete"):
            with q.scenario(f"E15_{relation}_{action}") as info:
                unused, engine, human = prepare(q)
                targets, setup = relation_setup(q, relation, engine, human)
                info["setup"] = setup
                q.mark()
                before = q.snap("relation_before")
                info["relations_before"] = relations(before)
                control = engine["left"]
                info["unrelated_control"] = {"handle": control, **info["relations_before"]["entities"][control]}
                if not targets:
                    info["availability"] = "unavailable"
                    continue

                def apply():
                    if action == "move":
                        for h in targets:
                            q.move(h)
                    else:
                        q.delete(targets)
                    # Regen도 COM이다. 명령 REGEN을 묶음 안에 보내지 않는다.
                    q.write(q.ctl._call_attr, q.ctl.doc, "Regen", 1)
                    applied = q.snap("relation_inside_mark")
                    info["relations_applied"] = relations(applied)
                    info["unrelated_control_after"] = info["relations_applied"]["entities"].get(control)
                    info["effects"] = q.verdict(before, applied, targets)
                    if relation == "hatch":
                        hh = setup["hatch"]
                        info["hatch_change"] = compare_raw(raw_state(before.entitydb[hh]),
                                                            raw_state(applied.entitydb[hh])) if hh in states(applied) else {"deleted": True}
                        if action == "move" and hh in states(applied):
                            expected = before.entitydb[hh].copy()
                            expected.translate(*DELTA)
                            info["hatch_follows_boundary"] = compare_raw(raw_state(expected), raw_state(applied.entitydb[hh]))

                after = q.grouped(before, targets, apply)
                if after is not None:
                    info["relations_restored"] = compare_raw(info["relations_before"], relations(after))


def measured(info, name, fn):
    start = time.perf_counter()
    try:
        return fn()
    finally:
        info.setdefault("ms", {})[name] = (time.perf_counter() - start) * 1000


def bulk_items(action, size):
    template = test_items()[{"move": 0, "text": 2, "attrib": 4, "delete": 0}[action]]
    items = []
    for i in range(size):
        item = copy.deepcopy(template)
        item["id"] = f"bulk{i}"
        if action in ("move", "delete"):
            item["start"] = [i * 1500.0, 40000.123456]
            item["end"] = [i * 1500.0 + 1000, 40000.123456]
        else:
            item["at"] = [i * 1500.0, 40000.123456]
        items.append(item)
    return items


def e16(q, sizes):
    from engine.state import state_hash

    rows = []
    for size in sizes:
        for action in ("move", "text", "attrib", "delete"):
            label = f"E16_{action}_{size}"
            with q.scenario(label) as info:
                info.update(size=size, action=action)
                record = None
                applied = None
                generated = build(q, label, bulk_items(action, size))
                q.write(q.ctl.import_dxf, str(generated))
                q.mark()
                before = q.snap("bulk_before")
                targets = []
                expected = {}
                for e in before.modelspace():
                    if not e.has_xdata(APP) or (1000, f"op={label}") not in e.get_xdata(APP):
                        continue
                    h = e.dxf.handle
                    targets.append(h)
                    if action == "delete":
                        continue
                    expected[h] = e.copy()
                    if action == "move":
                        expected[h].translate(*DELTA)
                    elif action == "text":
                        expected[h].dxf.text = "규모시험-길이가 달라진 문자"
                    else:
                        for attrib in expected[h].attribs:
                            attrib.dxf.text = "X9999-규모시험"
                if len(targets) != size:
                    raise RuntimeError(f"규모 시험 Import 개수가 다름: {len(targets)} != {size}")
                target_set = set(targets)
                q.begin()
                started = time.perf_counter()
                try:
                    def change():
                        if action == "delete":
                            q.delete(targets)
                            return
                        for h in targets:
                            if action == "move":
                                q.move(h)
                            elif action == "text":
                                q.attr(q.obj(h), "TextString", "규모시험-길이가 달라진 문자")
                            else:
                                attrs = q.ctl._call_attr(q.obj(h), "GetAttributes") or []
                                if not attrs:
                                    raise RuntimeError(f"속성 목록이 비어 있음: {h}")
                                for attrib in attrs:
                                    q.attr(attrib, "TextString", "X9999-규모시험")

                    measured(info, "change", change)

                    def update_tags():
                        for h, entity in expected.items():
                            strings = [v for code, v in before.entitydb[h].get_xdata(APP)
                                       if code == 1000 and not v.startswith("state=")]
                            strings.append(f"state={state_hash(entity)}")
                            q.write(q.ctl.set_xdata, h, strings)
                            q.changed += 1

                    measured(info, "setxdata", update_tags)
                    info["setxdata_count"] = len(expected)
                    if action == "delete":
                        info["setxdata_note"] = "삭제 객체에는 SetXData를 부를 수 없어 0회, 삭제 결과는 적용 기록에 남김"
                    record = payload(f"bulk-{action}-{size}", targets, action)
                    for h, entity in expected.items():
                        record["targets"][h]["after_hash"] = state_hash(entity)
                    measured(info, "record_write", lambda: q.put_record(record))
                    applied = measured(info, "export_inside_mark", lambda: q.snap("bulk_inside_mark"))
                    info["record_applied"] = read_records(applied).get(record["op"]) == record
                    inside_states, before_states = states(applied), states(before)
                    info["inside_non_targets"] = compare_raw({h: v for h, v in before_states.items() if h not in target_set},
                                                             {h: v for h, v in inside_states.items() if h not in target_set})
                    if action == "delete":
                        info["all_targets_deleted"] = not (set(targets) & states(applied).keys())
                        info["applied_objects_match"] = info["all_targets_deleted"]
                    else:
                        info["state_tags_match_expected"] = all(
                            (1000, f"state={state_hash(expected[h])}") in applied.entitydb[h].get_xdata(APP) for h in targets)
                        if action == "move":
                            actual = {h: {"start": plain(applied.entitydb[h].dxf.start), "end": plain(applied.entitydb[h].dxf.end)}
                                      for h in targets}
                            wanted = {h: {"start": plain(expected[h].dxf.start), "end": plain(expected[h].dxf.end)} for h in targets}
                        elif action == "text":
                            actual = {h: applied.entitydb[h].dxf.text for h in targets}
                            wanted = {h: expected[h].dxf.text for h in targets}
                        else:
                            actual = {h: sorted((a.dxf.tag, a.dxf.text) for a in applied.entitydb[h].attribs) for h in targets}
                            wanted = {h: sorted((a.dxf.tag, a.dxf.text) for a in expected[h].attribs) for h in targets}
                        info["applied_raw_comparison"] = compare_raw(wanted, actual)
                        info["applied_objects_match"] = info["applied_raw_comparison"]["same"]
                except Exception as e:  # noqa: BLE001
                    q.failure(e)
                finally:
                    measured(info, "end_undo", q.end)
                if not q.in_mark:
                    info["cmdactive_before_undo"] = q.ctl._read_cmdactive()
                    info["undo"] = measured(info, "undo", lambda: q.write(q.ctl.undo_once))
                    info["cmdactive_after_undo"] = q.ctl._read_cmdactive()
                    after = measured(info, "export_restored", lambda: q.snap("bulk_restored"))
                    measured(info, "verify_restored", lambda: info.update(q.verdict(before, after, targets)))
                    info["record_gone"] = record["op"] not in read_records(after) if record is not None else None
                else:
                    info["undo_skipped"] = "EndUndoMark 실패"
                info["ms"]["total_path"] = (time.perf_counter() - started) * 1000
                info["within_120s"] = info["ms"]["total_path"] <= 120000
            result = q.s.data["steps"][label]
            passed = (result.get("raised") is None and result.get("restored") and result.get("record_gone")
                      and result.get("record_applied") and result.get("applied_objects_match")
                      and result.get("inside_non_targets", {}).get("same")
                      and result.get("undo", {}).get("ok") and result.get("end_undo_ok")
                      and result.get("close_ok") and result.get("within_120s")
                      and (action == "delete" or result.get("state_tags_match_expected")))
            rows.append({"size": size, "action": action, "verified_within_120s": bool(passed),
                         "ms": result.get("ms", {}).get("total_path")})
    maxima = {action: max((r["size"] for r in rows if r["action"] == action and r["verified_within_120s"]), default=None)
              for action in ("move", "text", "attrib", "delete")}
    all_ok = [n for n in sizes if all(any(r["size"] == n and r["action"] == action and r["verified_within_120s"]
                                        for r in rows) for action in maxima)]
    q.s.record("E16_summary", {"rows": rows, "max_size_within_120s": maxima,
                               "max_size_all_actions_within_120s": max(all_ok, default=None),
                               "total_path_includes": "변경·SetXData·기록 쓰기·묶음 안 Export·EndUndoMark·UNDO 1·복귀 Export·원시 상태 확인"})


def e17(q):
    with q.scenario("E17_inverse_move") as info:
        before, engine, human = prepare(q)
        targets = list(engine.values()) + human
        q.begin()
        try:
            for h in targets:
                q.move(h)
        finally:
            q.end()
        if q.in_mark:
            raise RuntimeError("묶음을 닫지 못해 역이동 실험을 진행하지 않음")
        applied = q.snap("normal_move_applied")
        subset = [engine["line"], engine["mc"], engine["block"], human[0]]
        for h in subset:
            q.move(h, tuple(-v for v in DELTA))
        after = q.snap("partial_inverse")
        info.update(inverse_targets=subset, returned_before=compare_raw(
            {h: raw_state(before.entitydb[h]) for h in subset}, {h: raw_state(after.entitydb[h]) for h in subset}),
                    rest_kept_after=compare_raw({h: v for h, v in states(applied).items() if h not in subset},
                                               {h: v for h, v in states(after).items() if h not in subset}),
                    common=q.verdict(before, after, subset))

    with q.scenario("E17_reimport_deleted") as info:
        before, engine, human = prepare(q)
        targets = list(engine.values()) + human
        detached = ezdxf.new("R2013")
        importer = Importer(before, detached)
        importer.import_entities([before.entitydb[h] for h in targets])
        importer.finalize()
        # Importer 기본 동작의 태그 손실도 관찰 대상이므로 태그를 되붙이지 않는다.
        imported = list(detached.modelspace())
        if len(imported) != len(targets):
            raise RuntimeError("Importer 추출 객체 개수가 다름")
        info["importer_xdata_policy"] = "기본 Importer, 태그 재부착 없음"
        info["extracted_tags"] = [{"old_handle": h, "before": tags(before.entitydb[h]), "extracted": tags(e)}
                                  for h, e in zip(targets, imported)]
        path = q.work / "detached_deleted.dxf"
        q.guard()
        detached.saveas(path)
        extracted = ezdxf.readfile(path)
        info["extraction_tags_preserved"] = all(tags(before.entitydb[h]) == tags(e)
                                                 for h, e in zip(targets, extracted.modelspace()))
        q.begin()
        try:
            q.delete(targets)
        finally:
            q.end()
        if q.in_mark:
            raise RuntimeError("삭제 묶음을 닫지 못함")
        deleted = q.snap("deleted")
        q.write(q.ctl.import_dxf, str(path))
        after = q.snap("reimported")
        new_handles = sorted(states(after).keys() - states(deleted).keys())
        info.update(old_handles=targets, new_handles=new_handles,
                    same_handles=set(targets) == set(new_handles),
                    reused_old_handles=sorted(set(targets) & set(new_handles)),
                    old_tags=[tags(before.entitydb[h]) for h in targets],
                    new_tags=[tags(after.entitydb[h]) for h in new_handles])
        old_tag_set = sorted(json.dumps(tags(before.entitydb[h]), ensure_ascii=False) for h in targets)
        new_tag_set = sorted(json.dumps(tags(after.entitydb[h]), ensure_ascii=False) for h in new_handles)
        info["tags_preserved"] = old_tag_set == new_tag_set
        info["comparison_by_old_handle"] = q.verdict(before, after, targets)


def split_export(q, name):
    """export_dxf와 같은 빈 선택 세트 경로를 단계별로 실행한다."""
    result = {"raised": None, "selection_delete_ok": None, "saved": {"before": q.ctl.doc_state()["saved"]}}
    selection = None
    path = q.work / f"{q.label}_{name}_{uuid.uuid4().hex[:8]}"
    try:
        sets = q.ctl._call(getattr, q.ctl.doc, "SelectionSets")
        selection = q.write(q.ctl._call_attr, sets, "Add", f"B0_{uuid.uuid4().hex[:12]}")
        result["saved"]["after_selection_add"] = q.ctl.doc_state()["saved"]
        q.write(q.ctl._call_attr, q.ctl.doc, "Export", str(path), "DXF", selection)
        result["saved"]["after_export"] = q.ctl.doc_state()["saved"]
        result["path"] = str(path) + ".dxf"
        result["exists"] = Path(result["path"]).is_file()
    except Exception as e:  # noqa: BLE001
        result["raised"] = repr(e)
    finally:
        if selection is not None:
            try:
                q.write(q.ctl._call_attr, selection, "Delete")
                result["selection_delete_ok"] = True
            except Exception as e:  # noqa: BLE001
                result.update(selection_delete_ok=False, selection_delete_raised=repr(e))
            result["saved"]["after_selection_delete"] = q.ctl.doc_state()["saved"]
    return result


def saved_observation(q):
    # 각 단계만 수행한 사본도 E12(a)처럼 빈 묶음 뒤 UNDO에 끼는지 독립적으로 비교한다.
    for mode in ("idle_control", "staged_export", "empty_mark", "selection_add", "export", "selection_delete"):
        with q.scenario(f"E12_saved_{mode}") as info:
            before, engine, human = prepare(q)
            path = q.work / f"{q.label}_saved.dwg"
            q.write(q.ctl._call_attr, q.ctl.doc, "SaveAs", str(path))
            q.expected = path
            q.s.opened.append(norm(path))
            info["saved_initial"] = q.ctl.doc_state()["saved"]
            info["saved_fixture_ok"] = info["saved_initial"] is True
            if mode == "idle_control":
                time.sleep(0.5)
                info["saved_after_idle"] = q.ctl.doc_state()["saved"]
                info.update(q.verdict(before, q.snap("after_idle_observation"), engine.values()))
                continue
            if mode == "staged_export":
                info["stages"] = split_export(q, "stages")
                if info["stages"].get("exists"):
                    observed = ezdxf.readfile(info["stages"]["path"])
                    info.update(q.verdict(before, observed, engine.values()))
                continue
            selection = None
            sets = q.ctl._call(getattr, q.ctl.doc, "SelectionSets")
            try:
                if mode != "empty_mark":
                    selection = q.write(q.ctl._call_attr, sets, "Add", f"B0_SAVED_{uuid.uuid4().hex[:8]}")
                    info["saved_after_selection_add"] = q.ctl.doc_state()["saved"]
                if mode in ("export", "selection_delete"):
                    out = q.work / f"{q.label}_direct_export"
                    q.write(q.ctl._call_attr, q.ctl.doc, "Export", str(out), "DXF", selection)
                    info["saved_after_export"] = q.ctl.doc_state()["saved"]
                    info["direct_export_exists"] = out.with_suffix(".dxf").is_file()
                if mode == "selection_delete":
                    q.write(q.ctl._call_attr, selection, "Delete")
                    selection = None
                    info["selection_delete_ok"] = True
                    info["saved_after_selection_delete"] = q.ctl.doc_state()["saved"]
                q.begin()
                try:
                    q.ctl._call_attr(q.ctl.doc, "HandleToObject", "FFFFFFFFFFFFFFF")
                    info["missing_handle_unexpectedly_accepted"] = True
                except Exception as e:  # noqa: BLE001
                    q.failure(e)
                q.end()
                q.undo(before, engine.values())
                info["saved_after_undo_and_export"] = q.ctl.doc_state()["saved"]
            finally:
                if selection is not None:
                    try:
                        q.write(q.ctl._call_attr, selection, "Delete")
                        info["cleanup_selection_delete_ok"] = True
                    except Exception as e:  # noqa: BLE001
                        info["cleanup_selection_delete_ok"] = False
                        info["cleanup_selection_delete_raised"] = repr(e)


def selftest():
    """메모리의 합성 DXF만 쓴다. 하네스·COM import와 작업 폴더 생성도 하지 않는다."""
    doc = ezdxf.new("R2013")
    doc.appids.new(APP)
    doc.appids.new("OTHER")
    line = doc.modelspace().add_line((0.123456789, 0, 0), (1000, 0, 0), dxfattribs={"layer": "WAL"})
    line.set_xdata(APP, [(1000, "state=original")])
    line.set_xdata("OTHER", [(1000, "사람이 붙인 정보"), (1040, 0.123456789)])
    base = raw_state(line)
    line.dxf.start = (0.123457789, 0, 0)
    small = compare_raw(base, raw_state(line))
    assert not small["same"] and 0 < small["max_dev_mm"] < 0.00001, "미세 좌표 차이를 반올림하면 안 됨"
    line.dxf.start = (0.123456789, 0, 0)
    assert compare_raw(base, raw_state(line))["same"]
    line.set_xdata("OTHER", [(1000, "다른 앱 변경")])
    assert not compare_raw(base, raw_state(line))["same"], "모든 앱의 XDATA를 비교해야 함"
    line.dxf.true_color = 0x1153C9
    line.dxf.lineweight = 50
    assert raw_state(line)["true_color"] == 0x1153C9 and raw_state(line)["lineweight"] == 50
    poly = doc.modelspace().add_lwpolyline([(0, 0, 2, 3, 0.25), (100, 0, 4, 5, -0.125)], format="xyseb",
                                         close=True, dxfattribs={"elevation": 1.23456789})
    poly_state = raw_state(poly)
    assert poly_state["points"][0]["bulge"] == 0.25 and poly_state["points"][0]["start_width"] == 2
    assert poly_state["closed"] and poly_state["elevation"] == 1.23456789
    poly.dxf.extrusion = (0, 1, 0)
    assert not compare_raw(poly_state, raw_state(poly))["same"]
    text = doc.modelspace().add_text("가", dxfattribs={"height": 135, "insert": (100, 200),
                                                      "align_point": (500, 600), "halign": 1, "valign": 2})
    text_state = raw_state(text)
    text.dxf.insert = (100.001, 200, 0)
    assert not compare_raw(text_state, raw_state(text))["same"], "정렬 문자도 insert를 비교해야 함"
    block = doc.blocks.new("B0_SELFTEST")
    block.add_attdef("TAG", (10, 20), text="원래", height=135, dxfattribs={"halign": 1, "align_point": (10, 20)})
    insert = doc.modelspace().add_blockref("B0_SELFTEST", (1000, 2000)).add_auto_attribs({"TAG": "값"})
    insert_state = raw_state(insert)
    insert.attribs[0].dxf.align_point = (1010.0001, 2020, 0)
    assert not compare_raw(insert_state, raw_state(insert))["same"], "속성 정렬점 변경을 놓치면 안 됨"
    hatch = doc.modelspace().add_hatch(color=256)
    hatch.paths.add_polyline_path([(0, 0, 0.25), (100, 0, 0), (100, 100, 0)], is_closed=True)
    hatch.dxf.associative = 1
    hatch.paths[0].source_boundary_objects = [poly.dxf.handle]
    hatch_state = raw_state(hatch)
    hatch.paths[0].vertices[0] = (0.000001, 0, 0.25)
    assert not compare_raw(hatch_state, raw_state(hatch))["same"], "해치 경계 미세 차이를 비교해야 함"
    edge_hatch = doc.modelspace().add_hatch()
    edges = edge_hatch.paths.add_edge_path()
    edges.add_line((0, 0), (100, 0))
    edges.add_arc((100, 100), 100, -90, 90)
    json.dumps(raw_state(edge_hatch), ensure_ascii=False)
    assert not compare_raw({"h": base}, {})["same"], "사라진 핸들을 놓치면 안 됨"
    assert not compare_raw({"attribs": [1]}, {"attribs": []})["same"], "목록 길이도 비교해야 함"
    assert compare_raw({"insert": [0, 0, 0]}, {"insert": [3, 4, 0]})["max_dev_mm"] == 5
    records = [payload("small", ["ABC"]), payload("large", [f"{i:X}" for i in range(5000)])]
    korean = payload("korean", ["한글핸들"])
    korean["in"] = "한글경계" * 251
    records.append(korean)
    for record in records:
        chunks = record_chunks(record)
        assert chunks[0] == RECORD_SCHEMA and all(len(v) <= 250 for v in chunks[1:])
        assert join_record(chunks) == record
    assert any(ord(a[-1]) > 127 and ord(b[0]) > 127 for a, b in zip(record_chunks(korean)[1:], record_chunks(korean)[2:]))
    try:
        join_record(["unknown", "{}"])
    except ValueError:
        pass
    else:
        raise AssertionError("모르는 버전을 거부해야 함")
    dictionary = doc.rootdict.add_new_dict(APP)
    xrecord = doc.objects.add_xrecord()
    xrecord.reset([(1, v) for v in record_chunks(korean)])
    dictionary["korean"] = xrecord
    assert read_records(doc) == {"korean": korean}
    assert relations(doc)["entities"][line.dxf.handle]["reactors"] == []
    assert not any(name in sys.modules for name in ("_harness", "cad_controller", "pythoncom", "win32com.client"))
    print(json.dumps({"selftest": "통과", "checks": ["원시 좌표·속성 위치·해치 경계·모든 XDATA",
                                                    "한글 경계·5000 핸들 기록 조각·XRECORD 읽기", "COM 미사용"]}, ensure_ascii=False))


def parse_args():
    parser = argparse.ArgumentParser(description="B0 합성 사본 사전 검증(E10~E17, saved 관찰)")
    parser.add_argument("--selftest", action="store_true", help="CAD 없이 순수 함수만 검사")
    parser.add_argument("--only", default="E10,E11,E12,E13,E14,E15,E16,E17,SAVED", help="쉼표로 고를 항목(E10~E17,SAVED)")
    parser.add_argument("--sizes", default="100,1000,5000", help="E16 객체 수(쉼표 구분)")
    args = parser.parse_args()
    args.only = {v.strip().upper() for v in args.only.split(",") if v.strip()}
    allowed = {f"E{i}" for i in range(10, 18)} | {"SAVED"}
    if not args.only or args.only - allowed:
        parser.error(f"--only는 {','.join(sorted(allowed))} 중에서 고른다")
    try:
        args.sizes = sorted({int(v.strip()) for v in args.sizes.split(",")})
    except ValueError:
        parser.error("--sizes에는 정수를 쉼표로 구분해 쓴다")
    if not args.sizes or any(n < 1 or n > 20000 for n in args.sizes):
        parser.error("--sizes는 1~20000 범위여야 한다")
    return args


def main():
    args = parse_args()
    if args.selftest:
        selftest()
        return
    # WORK가 import 시 결정된다. 하네스를 불러오기 전에 실행별 새 폴더로 반드시 한정한다.
    work = SOURCE.parent / time.strftime("b0-%Y%m%d-%H%M%S")
    work.mkdir(parents=True, exist_ok=False)
    os.environ["ARCAID_SPIKE_DIR"] = str(work)
    if "_harness" in sys.modules:
        raise RuntimeError("하네스가 이미 import되어 안전한 WORK 범위를 보장할 수 없음")
    from _harness import Spike, under_work
    from engine.standards import load_standard
    import pythoncom
    import win32com.client

    class B0Spike(Spike):
        def __enter__(self):
            import csv
            import subprocess

            # 하네스의 start_cad는 새 CAD도 띄운다. B0는 실행 중인 CAD에만 연결한다.
            pythoncom.CoInitialize()
            app_ids, app_name = self.ctl._get_app_info()
            process_names = {"gcad": "gcad.exe", "gstarcad": "gcad.exe",
                             "bricscadapp": "bricscad.exe", "autocad": "acad.exe",
                             "zwcad": "zwcad.exe"}

            def process_count(process_name):
                result = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, check=True, timeout=10)
                # 지역화된 안내 문구와 비슷한 이름은 제외하고 실행 파일 이름만 정확히 센다.
                return sum(1 for row in csv.reader(result.stdout.splitlines())
                           if row and row[0].lower() == process_name)

            # ROT 조회를 모두 마친 뒤에만 프로세스 존재를 확인하고 Dispatch로 다시 시도한다.
            for method in ("active_object", "dispatch"):
                for app_id in app_ids:
                    process_name = process_names[app_id.split(".", 1)[0].lower()]
                    before = process_count(process_name)
                    attach = {"method": method, "app_id": app_id, "process_name": process_name,
                              "process_count_before": before, "process_count_after": before}
                    self.data["attach"] = attach
                    if method == "dispatch" and before == 0:
                        continue
                    app = None
                    try:
                        if method == "active_object":
                            app = win32com.client.GetActiveObject(app_id)
                        else:
                            app = win32com.client.Dispatch(app_id)
                    except Exception as e:  # noqa: BLE001
                        attach["error"] = repr(e)
                    finally:
                        # 연결 실패로 예외가 나도 새 프로세스가 생겼는지 먼저 확인한다.
                        attach["process_count_after"] = process_count(process_name)
                    if attach["process_count_after"] > before:
                        # __enter__에서 중단하여 새 CAD의 문서나 종료 메서드에는 접근하지 않는다.
                        raise RuntimeError(
                            f"{app_name} 프로세스 수가 늘어 중단함"
                            f"({before} -> {attach['process_count_after']}). "
                            "새로 뜬 CAD는 건드리지 않고 그대로 둠")
                    if app is not None:
                        self.ctl.app = app
                        break
                if self.ctl.app is not None:
                    break
            if self.ctl.app is None:
                if self.data["attach"]["process_count_after"] == 0:
                    raise RuntimeError(f"실행 중인 {app_name}가 없어 중단함(CAD를 새로 실행하지 않음)")
                raise RuntimeError(f"실행 중인 {app_name}에 연결하지 못해 중단함(후보 ProgID: {app_ids})")
            self.user_docs = self.ctl.list_drawings()
            collision = [d for d in self.user_docs if d["full_name"] and under_work(d["full_name"])]
            if collision:
                # __enter__에서 중단하므로 하네스 __exit__가 기존 문서를 닫지 않는다.
                raise RuntimeError(f"새 작업 폴더 아래 이미 열린 도면이 있어 중단함: {collision}")
            self.user_active = next((d for d in self.user_docs if d["is_active"]), None)
            self.data["user_docs_before"] = self.user_docs
            if self.user_active:
                self.ctl.doc = self.ctl._call(getattr, self.ctl.app, "ActiveDocument")
            self.ctl._hwnd = self.ctl._safe_attr(self.ctl.app, "HWND")
            print(f"[B0_probe] 실행 중인 CAD 연결, 작업 폴더: {work}")
            return self

        def __exit__(self, exc_type, exc, tb):
            cancel = self.ctl.cancel_command

            def cancel_owned_only():
                # 시나리오 닫기 뒤 ctl.doc는 사용자 문서일 수 있다. 그 문서에는 ESC도 보내지 않는다.
                active = self.ctl._call(getattr, self.ctl.app, "ActiveDocument")
                full = str(self.ctl._call(getattr, active, "FullName"))
                if not under_work(full) or norm(full) not in self.opened:
                    return {"skipped": "이번 실행에서 연 활성 시험 사본이 없어 명령 취소 생략"}
                self.guard(active)
                self.ctl.doc = active
                return cancel()

            self.ctl.cancel_command = cancel_owned_only
            try:
                return super().__exit__(exc_type, exc, tb)
            finally:
                self.ctl.cancel_command = cancel

    shutil.copyfile(SOURCE, work / "synthetic_base.dwg")
    with B0Spike("B0_probe") as s:
        q = Probe(s, work, load_standard())
        s.data.update(work=str(work), only=sorted(args.only), sizes=args.sizes,
                      timeout_note="진짜 타임아웃은 재현하지 않음")
        for label, fn in (("E10", e10), ("E11", e11), ("E12", e12), ("E13", e13),
                          ("E14", e14), ("E15", e15), ("E16", lambda p: e16(p, args.sizes)),
                          ("E17", e17), ("SAVED", saved_observation)):
            if label not in args.only:
                continue
            try:
                fn(q)
            except Exception as e:  # noqa: BLE001
                # 시나리오 바깥 준비·집계 실패도 다음 항목을 막지 않는다.
                s.record(f"{label}_dispatch_error", {"raised": repr(e)})


if __name__ == "__main__":
    main()
