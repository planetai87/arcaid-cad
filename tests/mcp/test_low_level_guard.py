"""B3 저수준 보호: CAD 없이 같은 문서·객체와 원시 XDATA를 확인한다."""
import copy
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from src.cad_controller import CADController


EDITS = [("erase_entity", []), ("move_entity", [[10, 20]]),
         ("rotate_entity", [[0, 0], 90]), ("scale_entity", [[0, 0], 2])]
CREATES = [("copy_entity", [[10, 20]]), ("mirror_entity", [[0, 0], [0, 1]]),
           ("offset_entity", [10]), ("array_linear_entity", [4, [10, 20]]),
           ("array_polar_entity", [4, [0, 0]])]
TAG = ([1001, 1000, 1000], ["ARCAID", "op=seed", "oid=seed:1"])
MODEL_ID = -1781105568
ATTRIBUTE_ERROR = "속성은 블록 참조 핸들로 편집한다(속성 단독 편집은 지원하지 않음)"


class Document(SimpleNamespace):
    def ObjectIDToObject(self, object_id):
        raise AssertionError("GstarCAD에서 ObjectIDToObject를 호출하면 안 됨")


class Layouts:
    def __init__(self, items):
        self.items = items
        self.Count = len(items)

    def Item(self, index):
        item = self.items[index]
        if isinstance(item, Exception):
            raise item
        return item


class Unreadable:
    def __getattr__(self, name):
        raise RuntimeError(f"속성 읽기 실패: {name}")


class Entity:
    def __init__(self, doc, handle, kind="AcDbLine", owner=MODEL_ID, tag=None):
        self.doc, self.Handle, self.ObjectName, self.OwnerID = doc, handle, kind, owner
        self.ObjectID = int(handle, 16) + 100
        self.attrs, self.constants = [], []
        self.xdata = {"TESTAPP": ([1001, 1000], ["TESTAPP", "보존"])}
        if tag is not None:
            self.xdata["ARCAID"] = tag
        self.actions, self.sets = [], []
        self.read_error = self.clear_error = self.sticky = False
        doc.entities[handle] = self

    @property
    def HasAttributes(self):
        return bool(self.attrs or self.constants)

    def GetAttributes(self):
        return self.attrs

    def GetConstantAttributes(self):
        return self.constants

    def GetXData(self, app):
        if self.read_error:
            raise RuntimeError("태그 읽기 실패")
        return self.xdata.get(app, (None, None))

    def SetXData(self, types, values):
        assert self.doc.depth == 1
        self.sets.append((list(types.value), list(values.value)))
        if self.clear_error:
            raise RuntimeError("태그 쓰기 실패")
        if not self.sticky:
            self.xdata.pop(values.value[0], None)

    def Delete(self):
        self.actions.append("delete")

    def Move(self, *args):
        self.actions.append("move")
        if self.doc.move_error:
            raise RuntimeError("생성 후 이동 실패")

    def Rotate(self, *args):
        self.actions.append("rotate")
        if self.doc.move_error:
            raise RuntimeError("생성 후 회전 실패")

    def ScaleEntity(self, *args):
        self.actions.append("scale")

    def Copy(self):
        self.doc.copies += 1
        if self.doc.fail_copy == self.doc.copies:
            raise RuntimeError("n번째 복사 실패")
        return self.clone()

    def clone(self):
        obj = Entity(self.doc, format(len(self.doc.entities) + 32, "X"), self.ObjectName, self.OwnerID)
        obj.xdata = copy.deepcopy(self.xdata)
        obj.sticky, obj.clear_error, obj.read_error = self.sticky, self.clear_error, self.read_error
        for source, target in ((self.attrs, obj.attrs), (self.constants, obj.constants)):
            for attr in source:
                # 정의나 다른 INSERT가 소유한 속성 참조는 그대로 반환한다.
                new_attr = attr
                if attr.OwnerID == self.ObjectID:
                    new_attr = attr.clone()
                    new_attr.OwnerID = obj.ObjectID
                target.append(new_attr)
        return obj

    def Mirror(self, *args):
        return self.clone()

    def Offset(self, *args):
        return [self.clone(), self.clone()]


@pytest.fixture
def ctl():
    # COM 스레드도 시작하지 않고 실제 메서드를 가짜 문서에서 실행한다.
    c = CADController.__new__(CADController)
    doc = Document(entities={}, ModelSpace=SimpleNamespace(ObjectID=MODEL_ID),
                   PaperSpace=SimpleNamespace(ObjectID=3),
                   Layouts=Layouts([SimpleNamespace(Block=SimpleNamespace(ObjectID=3)),
                                    SimpleNamespace(Block=SimpleNamespace(ObjectID=4))]),
                   depth=0, copies=0, fail_copy=None, move_error=False)
    doc.HandleToObject = lambda h: doc.entities[h]
    c.doc, c.app = doc, object()

    @contextmanager
    def group(target_doc=None):
        assert target_doc is doc
        doc.depth += 1
        try:
            yield
        finally:
            doc.depth -= 1

    c._undo_group = group
    Entity(doc, "A")
    return c


@pytest.mark.parametrize("method,args", EDITS)
@pytest.mark.parametrize("raw", [TAG, ([1001], ["ARCAID"]), ([1001, 1000], ["ARCAID"]),
                                  ([1001, 1000], ["ARCAID", "파손"]), ([], ["ARCAID"]),
                                  None, (), (None, ["ARCAID"]), ([1001], None)])
def test_raw_tags_always_refuse(ctl, method, args, raw):
    obj = ctl.doc.entities["A"]
    obj.xdata["ARCAID"] = raw
    result = getattr(ctl, method)("A", *args)
    assert not result.ok and not obj.actions and "엔진 객체" in result.error


@pytest.mark.parametrize("method,args", EDITS)
def test_read_failure_refuses_and_human_edit_succeeds(ctl, method, args):
    obj = ctl.doc.entities["A"]
    obj.read_error = True
    r = getattr(ctl, method)("A", *args)
    assert not r.ok and not obj.actions and "태그" in r.error
    obj.read_error = False
    assert getattr(ctl, method)("A", *args).ok and obj.actions


def block(ctl):
    root = ctl.doc.entities["A"]
    root.ObjectName = "AcDbBlockReference"
    attr = Entity(ctl.doc, "B", "AcDbAttribute", owner=root.ObjectID)
    root.attrs = [attr]
    return root, attr


@pytest.mark.parametrize("method,args", EDITS)
@pytest.mark.parametrize("tagged", ["root", "attr"])
@pytest.mark.parametrize("kind", ["AcDbBlockReference", "AcDbMInsertBlock"])
@pytest.mark.parametrize("attribute_list", ["attrs", "constants"])
def test_insert_and_owned_attrib_tags_guard_insert(ctl, method, args, tagged, kind, attribute_list):
    root, attr = block(ctl)
    root.ObjectName = kind
    root.attrs = []
    getattr(root, attribute_list).append(attr)
    (root if tagged == "root" else attr).xdata["ARCAID"] = TAG
    r = getattr(ctl, method)("A", *args)
    assert not r.ok and "엔진 객체" in r.error and not root.actions and not attr.actions


@pytest.mark.parametrize("method,args", EDITS + CREATES)
@pytest.mark.parametrize("kind", ["AcDbAttribute", "AcDbAttributeReference"])
@pytest.mark.parametrize("tagged", [False, True])
def test_direct_attribute_handles_are_refused(ctl, method, args, kind, tagged):
    root, attr = block(ctl)
    attr.ObjectName = kind
    if tagged:
        attr.xdata["ARCAID"] = TAG
    r = getattr(ctl, method)("B", *args)
    assert not r.ok and r.error == ATTRIBUTE_ERROR and not r.new_handles
    assert len(ctl.doc.entities) == 2 and not root.actions and not attr.actions and not attr.sets
    assert ("ARCAID" in attr.xdata) == tagged and ctl.doc.copies == 0


@pytest.mark.parametrize("method,args", EDITS + CREATES)
@pytest.mark.parametrize("kind", ["AcDbBlockReference", "AcDbLine", "AcDbAttributeDefinition"])
def test_block_definition_handles_are_refused(ctl, method, args, kind):
    root, attr = block(ctl)
    root.ObjectName = kind
    root.OwnerID = 2
    r = getattr(ctl, method)("A", *args)
    assert not r.ok and not r.new_handles
    assert r.error == "모형·종이 공간의 객체만 편집할 수 있음(블록 정의 내부·속성은 거절)"
    assert len(ctl.doc.entities) == 2 and not root.actions and not attr.actions


@pytest.mark.parametrize("method,args", EDITS + CREATES)
@pytest.mark.parametrize("owner", [MODEL_ID, 3, 4])
def test_layout_spaces_allow_operations_without_object_id_lookup(ctl, method, args, owner):
    obj = ctl.doc.entities["A"]
    obj.OwnerID = owner
    r = getattr(ctl, method)("A", *args)
    assert r.ok, r.error
    assert obj.actions or r.new_handles


@pytest.mark.parametrize("failure", ["paper", "layouts", "layout_count", "layout_items"])
def test_optional_space_read_failures_still_allow_model_space(ctl, failure):
    if failure == "paper":
        ctl.doc.PaperSpace = Unreadable()
    elif failure == "layouts":
        del ctl.doc.Layouts
    elif failure == "layout_count":
        ctl.doc.Layouts = Unreadable()
    else:
        ctl.doc.Layouts = Layouts([RuntimeError("레이아웃 읽기 실패"), Unreadable()])
    r = ctl.move_entity("A", [10, 20])
    assert r.ok and ctl.doc.entities["A"].actions == ["move"], r.error


def test_layout_item_failures_do_not_hide_later_layouts(ctl):
    ctl.doc.PaperSpace = Unreadable()
    ctl.doc.Layouts = Layouts([RuntimeError("레이아웃 읽기 실패"), Unreadable(),
                              SimpleNamespace(Block=Unreadable()),
                              SimpleNamespace(Block=SimpleNamespace(ObjectID=4))])
    ctl.doc.entities["A"].OwnerID = 4
    r = ctl.move_entity("A", [10, 20])
    assert r.ok and ctl.doc.entities["A"].actions == ["move"], r.error


@pytest.mark.parametrize("failure", ["model_space", "model_id"])
def test_model_space_id_is_required_even_for_paper_space(ctl, failure):
    if failure == "model_space":
        del ctl.doc.ModelSpace
    else:
        ctl.doc.ModelSpace = Unreadable()
    obj = ctl.doc.entities["A"]
    obj.OwnerID = 3
    r = ctl.move_entity("A", [10, 20])
    assert not r.ok and not obj.actions


@pytest.mark.parametrize("method,args", EDITS + CREATES)
@pytest.mark.parametrize("attribute_list", ["attrs", "constants"])
def test_other_owners_attributes_are_never_guarded_or_detagged(ctl, method, args, attribute_list):
    root, attr = block(ctl)
    foreign = Entity(ctl.doc, "C", "AcDbAttribute", owner=2, tag=TAG)
    getattr(root, attribute_list).append(foreign)
    r = getattr(ctl, method)("A", *args)
    assert r.ok, r.error
    assert not foreign.actions and not foreign.sets and foreign.xdata["ARCAID"] == TAG


@pytest.mark.parametrize("method,args", CREATES)
@pytest.mark.parametrize("tagged", [False, True])
@pytest.mark.parametrize("attribute_list", ["attrs", "constants"])
def test_new_objects_are_detagged_and_other_app_is_preserved(ctl, method, args, tagged, attribute_list):
    root, attr = block(ctl)
    root.attrs = []
    getattr(root, attribute_list).append(attr)
    if tagged:
        root.xdata["ARCAID"] = attr.xdata["ARCAID"] = TAG
    r = getattr(ctl, method)("A", *args)
    assert r.ok and r.new_handles and not r.tag_left
    for h in r.new_handles:
        new = ctl.doc.entities[h]
        for e in [new, *new.attrs, *new.constants]:
            assert "ARCAID" not in e.xdata and e.xdata["TESTAPP"][1][1] == "보존"
            assert e.sets == [([1001], ["ARCAID"])] if tagged else e.sets == []
    assert ("ARCAID" in root.xdata) == tagged and ("ARCAID" in attr.xdata) == tagged


@pytest.mark.parametrize("kind,owner", [("AcDbLine", 2), ("AcDbAttribute", 110),
                                       ("AcDbAttributeReference", 110)])
def test_created_objects_outside_layout_are_not_detagged(ctl, kind, owner):
    source = ctl.doc.entities["A"]
    source.clone = lambda: Entity(ctl.doc, "F", kind, owner=owner, tag=TAG)
    r = ctl.copy_entity("A", [10, 20])
    created = ctl.doc.entities["F"]
    assert not r.ok and r.new_handles == ["F"] and r.tag_left == ["F"]
    assert created.xdata["ARCAID"] == TAG and not created.sets and not created.actions


@pytest.mark.parametrize("method,args", CREATES)
@pytest.mark.parametrize("failure", ["sticky", "clear_error", "read_error"])
def test_detag_failure_keeps_new_handles(ctl, method, args, failure):
    obj = ctl.doc.entities["A"]
    obj.xdata["ARCAID"] = TAG
    setattr(obj, failure, True)
    r = getattr(ctl, method)("A", *args)
    assert not r.ok and r.new_handles and set(r.tag_left) <= set(ctl.doc.entities)
    assert r.tag_left and "같은 oid 중복" in r.error


@pytest.mark.parametrize("method,args", CREATES[-2:])
def test_array_nth_copy_failure_keeps_partial_handles(ctl, method, args):
    ctl.doc.fail_copy = 2
    r = getattr(ctl, method)("A", *args)
    assert not r.ok and len(r.new_handles) == 1 and "n번째" in r.error


@pytest.mark.parametrize("method,args", [CREATES[0], *CREATES[-2:]])
def test_new_handle_is_recorded_before_move_failure(ctl, method, args):
    ctl.doc.move_error = True
    r = getattr(ctl, method)("A", *args)
    assert not r.ok and len(r.new_handles) == 1


def test_engine_tag_keeps_raw_presence_separate(ctl):
    assert ctl.engine_tag("A") == (False, {})
    ctl.doc.entities["A"].xdata["ARCAID"] = TAG
    assert ctl.engine_tag("A") == (True, {"op": "seed", "oid": "seed:1"})


@pytest.mark.parametrize("raw", [(None, None), ([], []), ((), ())])
def test_only_known_empty_xdata_is_absent(ctl, raw):
    ctl.doc.entities["A"].xdata["ARCAID"] = raw
    assert ctl.engine_tag("A") == (False, {})


def test_engine_tag_propagates_read_failure(ctl):
    ctl.doc.entities["A"].read_error = True
    with pytest.raises(RuntimeError, match="태그 읽기 실패"):
        ctl.engine_tag("A")


def test_same_document_and_entity_used_after_check(ctl):
    obj, old_doc = ctl.doc.entities["A"], ctl.doc
    original = obj.GetXData

    def switch(app):
        # 태그 검사 직후 다른 문서·같은 핸들을 주입한다. 검사했던 객체만 편집해야 한다.
        new_doc = SimpleNamespace(entities={})
        ctl.doc = new_doc
        return original(app)

    obj.GetXData = switch
    r = ctl.move_entity("A", [10, 20])
    assert r.ok and obj.actions == ["move"] and old_doc.depth == 0


def test_ui_tag_write_after_check_is_outside_guarantee(ctl):
    obj = ctl.doc.entities["A"]
    original = ctl._undo_group

    @contextmanager
    def group(doc):
        # CAD 화면의 변경은 MCP 직렬화로 잠글 수 없다. 검사 뒤 태그가 생기는 경쟁을 주입한다.
        obj.xdata["ARCAID"] = TAG
        with original(doc):
            yield

    ctl._undo_group = group
    assert ctl.move_entity("A", [10, 20]).ok
    assert obj.actions == ["move"] and obj.xdata["ARCAID"] == TAG


def test_definition_constant_attribute_is_never_detagged(ctl):
    root, attr = block(ctl)
    constant = Entity(ctl.doc, "C", "AcDbAttributeDefinition", owner=2, tag=TAG)
    root.constants = [constant]
    r = ctl.copy_entity("A", [10, 20])
    assert r.ok and not constant.sets and constant.xdata["ARCAID"] == TAG


def test_child_detag_failure_is_reported_with_its_handle(ctl):
    root, attr = block(ctl)
    attr.xdata["ARCAID"], attr.sticky = TAG, True
    r = ctl.copy_entity("A", [10, 20])
    assert not r.ok and len(r.new_handles) == 1
    clone = ctl.doc.entities[r.new_handles[0]]
    assert r.tag_left == [clone.attrs[0].Handle] and "ARCAID" not in clone.xdata


def test_offset_tracks_all_results_even_when_first_cannot_be_detagged(ctl):
    obj = ctl.doc.entities["A"]
    obj.xdata["ARCAID"], obj.sticky = TAG, True
    r = ctl.offset_entity("A", 10)
    assert not r.ok and len(r.new_handles) == 2 and r.tag_left == r.new_handles
