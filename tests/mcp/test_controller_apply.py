"""보호된 적용 경로의 COM 메서드(CADController) 단위 테스트: 가짜 COM 객체로 확인한다(Windows, CAD 불필요)"""
import sys
from pathlib import Path

import pythoncom
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from cad_controller import CADController  # noqa: E402


class FakeEntity:
    def __init__(self, handle):
        self.handle = handle
        self.xdata = None
        self.deleted = False

    def SetXData(self, types, values):
        self.xdata = (types, values)

    def Delete(self):
        self.deleted = True


class FakeDoc:
    def __init__(self, app, name):
        self.app, self.Name = app, name
        self.entities = {}
        self.activations = 0

    def Activate(self):
        self.activations += 1
        if self.app.allow_activate:
            self.app.ActiveDocument = self

    def HandleToObject(self, handle):
        if handle not in self.entities:
            raise KeyError(handle)
        return self.entities[handle]


class FakeApp:
    def __init__(self):
        self.allow_activate = True
        self.ActiveDocument = None


@pytest.fixture
def ctl():
    c = CADController()
    c.app = FakeApp()
    c.doc = FakeDoc(c.app, "target.dwg")
    c.app.ActiveDocument = FakeDoc(c.app, "other.dwg")
    return c


def test_xdata_variants_are_typed_arrays():
    types, values = CADController.xdata_variants("ARCAID", ["op=a", "state=b"])
    assert types.varianttype == pythoncom.VT_ARRAY | pythoncom.VT_I2 and list(types.value) == [1001, 1000, 1000]
    assert values.varianttype == pythoncom.VT_ARRAY | pythoncom.VT_VARIANT
    assert list(values.value) == ["ARCAID", "op=a", "state=b"]


def test_ensure_active_activates_target(ctl):
    ctl.ensure_active(timeout=2)
    assert ctl.app.ActiveDocument is ctl.doc and ctl.doc.activations == 1


def test_ensure_active_times_out(ctl):
    ctl.app.allow_activate = False
    with pytest.raises(RuntimeError, match="활성화되지 않음"):
        ctl.ensure_active(timeout=0.5)


def test_send_command_refuses_when_target_cannot_be_activated(ctl):
    ctl.app.allow_activate = False
    original = ctl.ensure_active
    ctl.ensure_active = lambda *a, **k: original(timeout=0.3)
    r = ctl.send_command("_.LINE")
    assert r["ok"] is False and "활성화되지 않음" in r["error"]
    r = ctl.run_lisp("(+ 1 2)")
    assert r["ok"] is False and "활성화되지 않음" in r["error"]


def test_set_xdata_props_and_delete(ctl):
    a, b = FakeEntity("1A"), FakeEntity("1B")
    ctl.doc.entities = {"1A": a, "1B": b}
    ctl.set_xdata("1A", ["op=x", "oid=x:1"])
    assert list(a.xdata[1].value) == ["ARCAID", "op=x", "oid=x:1"]
    ctl.set_props("1B", layer="DIM", color=256, linetype="ByLayer", lineweight=-1)
    assert (b.Layer, b.color, b.Linetype, b.Lineweight) == ("DIM", 256, "ByLayer", -1)
    deleted, failed = ctl.delete_handles(["1A", "ZZ"])
    assert (deleted, failed, a.deleted) == (1, ["ZZ"], True)


def test_change_props_uses_truecolor_object_and_case_insensitive_attributes(ctl):
    from types import SimpleNamespace

    class Color:
        def SetRGB(self, r, g, b):
            self.rgb = (r, g, b)

    class Entity(FakeEntity):
        def __init__(self):
            super().__init__("1A")
            self.TrueColor = Color()
            self.attrs = [SimpleNamespace(TagString="NAME", TextString="옛 값", Constant=False)]

        def GetAttributes(self):
            return self.attrs

        def Move(self, start, end):
            self.delta = tuple(end.value)

    obj = Entity()
    ctl.doc.entities = {"1A": obj}
    ctl.set_change_props("1A", layer="WAL", color={"rgb": [17, 83, 201]}, text="새 문자", attribs={"name": "새 값"})
    assert obj.TrueColor.rgb == (17, 83, 201) and obj.TextString == "새 문자"
    assert obj.attrs[0].TextString == "새 값" and obj.Layer == "WAL"
    ctl.move_handles(["1A"], [10, 20])
    assert obj.delta == (10, 20, 0)
    with pytest.raises(RuntimeError):
        ctl.set_change_props("1A", attribs={"MISSING": "x"})
    obj.attrs[0].Constant = True
    with pytest.raises(RuntimeError):
        ctl.set_change_props("1A", attribs={"NAME": "x"})


def test_record_rejects_duplicate_before_add_and_writes_typed_strings(ctl):
    class Record:
        def SetXRecordData(self, types, values):
            self.types, self.values = types, values

    class Dictionary:
        def __init__(self):
            self.entries = {}
            self.adds = 0

        @property
        def Count(self):
            return len(self.entries)

        def Item(self, key):
            return list(self.entries.values())[key] if isinstance(key, int) else self.entries[key]

        def GetName(self, obj):
            return next(k for k, v in self.entries.items() if v is obj)

        def AddXRecord(self, name):
            self.adds += 1
            obj = Record()
            self.entries[name] = obj
            return obj

        def Add(self, name):
            obj = Dictionary()
            self.entries[name] = obj
            return obj

    ctl.doc.Dictionaries = Dictionary()
    record = ctl.create_change_record("change-1", root_exists=False)
    strings = ["arcaid-change-record/1", '{"op":"변경"}']
    ctl.write_change_record(record, strings)
    assert record.types.varianttype == pythoncom.VT_ARRAY | pythoncom.VT_I2
    assert list(record.types.value) == [1, 1]
    assert record.values.varianttype == pythoncom.VT_ARRAY | pythoncom.VT_VARIANT
    assert list(record.values.value) == strings
    with pytest.raises(RuntimeError):
        ctl.create_change_record("change-1", root_exists=True)
    assert ctl.doc.Dictionaries.Item("ARCAID").adds == 1


def test_delete_propagates_uncertain_failure_for_protected_changes(ctl):
    class TimedOut(FakeEntity):
        def Delete(self):
            raise TimeoutError("삭제 COM 시간 초과")

    ctl.doc.entities = {"1A": TimedOut("1A")}
    with pytest.raises(TimeoutError):
        ctl.delete_handles(["1A"], raise_on_error=True)


def test_protected_export_preserves_original_timeout(ctl, tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(ctl, "is_running", lambda: True)
    monkeypatch.setattr(ctl, "command_busy_reason", lambda: None)
    ctl.doc.SelectionSets = SimpleNamespace(Add=lambda _: SimpleNamespace(Delete=lambda: None))

    def timeout(*_):
        raise TimeoutError()

    ctl.doc.Export = timeout
    with pytest.raises(TimeoutError):
        ctl.export_dxf(str(tmp_path / "s1"), raise_on_error=True)
