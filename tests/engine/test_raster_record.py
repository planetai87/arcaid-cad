"""측정 기록(tools/raster/record.py)과 측정 도구의 --record를 합성 이미지로 확인한다.

기대값은 합성기의 형상 정의(면선 위치)와 손으로 정한 머리말에서 온다. 1 px = 16.7 mm.
"""
import json

import pytest
import yaml

from tests.engine import raster_synth as rs
from tools.raster import measure as m
from tools.raster import record as rec

PX_MM = 1 / rs.S


# ---------- 시험 도구 ----------

def _calibrate(tmp_path, img, sc, pts, check):
    image = tmp_path / "plan.png"
    img.save(image)
    calib = tmp_path / "calib.json"
    args = ["calib", str(image), "-o", str(calib)]
    for x, y in pts:
        u, v = sc.px(x, y)
        args += ["--point", f"{u},{v}={x},{y}"]
    u, v = sc.px(*check)
    args += ["--check", f"{u},{v}={check[0]},{check[1]}"]
    assert m.main(args) == 0
    return image, calib


def _project(tmp_path, capsys, img, sc, pts, check, regions, frames=None):
    """주 대화가 하는 준비: 보정 → init → 머리말(frame·영역) 작성 → 잠금 → 영역 파일"""
    image, calib = _calibrate(tmp_path, img, sc, pts, check)
    measure = tmp_path / "measure.yaml"
    assert rec.main(["init", "--image", str(image), "--calib", str(calib), "--standard", "heo-2026@2026.1",
                     "--k", "200", "-o", str(measure)]) == 0
    data = yaml.safe_load(measure.read_text(encoding="utf-8"))
    data["frames"] = frames or {}
    data["regions"] = regions
    measure.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    lock = tmp_path / "lock.json"
    assert rec.main(["lock", str(measure), "-o", str(lock)]) == 0
    files = {}
    for name in regions:
        files[name] = tmp_path / f"region-{name}.yaml"
        assert rec.main(["region-init", str(measure), "--lock", str(lock), "--region", name,
                         "-o", str(files[name])]) == 0
    capsys.readouterr()
    return {"image": image, "calib": calib, "measure": measure, "lock": lock, "files": files}


def _est(v):
    return {"value": v, "source": "추정", "reason": "시험"}


WING_FRAME = {"wing": {"x": _est(2200.0), "y": _est(6540.0), "deg": _est(23.76)}}
TWO_REGIONS = {"A": {"frame": None, "window": [-500, -500, 5000, 6000], "elements": ["EA"]},
               "B": {"frame": None, "window": [5000, -500, 10500, 6000], "elements": ["EB"]}}


@pytest.fixture
def plan_project(tmp_path, capsys):
    img, sc = rs.plan(beta=0.8)
    return _project(tmp_path, capsys, img, sc, [(0, 0), (9850, 0), (0, 5700)], (9850, 5700), TWO_REGIONS,
                    WING_FRAME)


def _read(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _profile(p, region, names, at=5000, rng=(-400, 400), extra=()):
    return m.main(["profile", str(p["image"]), "--calib", str(p["calib"]), "--axis", "v", "--at", str(at),
                   "--range", str(rng[0]), str(rng[1]), "--band", "300", *extra,
                   "--record", str(p["files"][region]), f"--names={names}"])


# ---------- 초기화·잠금·기록 ----------

def test_init_copies_image_and_calib_without_retyping(plan_project):
    data = _read(plan_project["measure"])
    calib = json.loads(plan_project["calib"].read_text(encoding="utf-8"))
    assert data["image"]["sha256"] == calib["sha256"]
    assert data["calib"]["px_per_mm"] == calib["px_per_mm"]
    assert data["calib"]["beta_deg"] == calib["beta_deg"]
    assert data["output"] == {"standard": "heo-2026@2026.1", "k": 200}
    assert data["units"] == "mm" and data["kind"] == "measure"


def test_region_file_carries_the_lock(plan_project):
    lock = json.loads(plan_project["lock"].read_text(encoding="utf-8"))["lock"]
    assert _read(plan_project["files"]["A"])["lock"] == lock
    assert _read(plan_project["files"]["A"])["region"] == "A"


def test_lock_changes_when_a_locked_header_field_changes(plan_project):
    before = rec.lock_hash(_read(plan_project["measure"]))
    data = _read(plan_project["measure"])
    data["frames"]["wing"]["deg"]["value"] = 23.80
    assert rec.lock_hash(data) != before
    data = _read(plan_project["measure"])
    data["regions"]["A"]["elements"].append("EX")
    assert rec.lock_hash(data) != before


def test_lock_refuses_a_legend_rule_not_approved_by_the_user(plan_project, capsys):
    data = _read(plan_project["measure"])
    data["legend"] = [{"id": "L1", "pattern": "굵은선 두 줄", "meaning": "RC 벽", "approved_by": "main"}]
    plan_project["measure"].write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert rec.main(["lock", str(plan_project["measure"]), "-o", str(plan_project["lock"])]) == 1
    data["legend"][0]["approved_by"] = "user"
    plan_project["measure"].write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert rec.main(["lock", str(plan_project["measure"]), "-o", str(plan_project["lock"])]) == 0


def test_region_init_refuses_a_stale_lock(plan_project, capsys):
    data = _read(plan_project["measure"])
    data["frames"]["wing"]["deg"]["value"] = 23.80
    plan_project["measure"].write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    out = plan_project["measure"].parent / "late.yaml"
    assert rec.main(["region-init", str(plan_project["measure"]), "--lock", str(plan_project["lock"]),
                     "--region", "A", "-o", str(out)]) == 1
    assert "잠금" in json.loads(capsys.readouterr().out)["message"] and not out.exists()


def test_profile_record_stores_runs_by_name_with_command(plan_project, capsys):
    assert _profile(plan_project, "A", "s-out,-,s-in") == 0
    obs = _read(plan_project["files"]["A"])["observations"]
    assert set(obs) == {"s-out", "s-in"}
    assert abs(obs["s-out"]["coord"] - (-100)) <= PX_MM and abs(obs["s-in"]["coord"] - 100) <= PX_MM
    assert obs["s-out"]["axis"] == "u"                 # v 방향 단면이 자른 선은 u 방향으로 뻗는다
    cmd = obs["s-out"]["command"]
    assert cmd[0] == "profile" and "--record" not in cmd and "--names" not in cmd
    assert obs["s-out"]["pick"] == 0 and obs["s-in"]["pick"] == 2
    assert not list(plan_project["files"]["A"].parent.glob("*.tmp"))


def test_profile_record_refuses_wrong_name_count_and_unreadable_runs(plan_project, capsys):
    assert _profile(plan_project, "A", "only-one") == 1
    assert "3개" in json.loads(capsys.readouterr().out)["message"]
    assert _profile(plan_project, "A", "a,b,c", extra=("--max-tol", "1")) == 1   # 오차 예산 초과 = 판독 불가
    assert "판독 불가" in json.loads(capsys.readouterr().out)["message"]
    assert _read(plan_project["files"]["A"])["observations"] == {}


def test_fitline_record_stores_line_in_wcs(plan_project):
    assert m.main(["fitline", str(plan_project["image"]), "--calib", str(plan_project["calib"]),
                   "--frame", "2200,6540,23.76", "--from", "1000,0", "--to", "8000,0", "--band", "100",
                   "--record", str(plan_project["files"]["B"]), "--name", "wing-s"]) == 0
    o = _read(plan_project["files"]["B"])["observations"]["wing-s"]
    assert o["axis"] == "u" and abs(o["coord"]) <= PX_MM
    assert abs(o["angle_wcs_deg"] - 23.76) <= 0.05 and o["pick"] is None


def _blob(p, region, name, window, frame=None):
    args = ["blob", str(p["image"]), "--calib", str(p["calib"]), "--window", *map(str, window)]
    if frame:
        args += ["--frame", frame]
    return m.main(args + ["--record", str(p["files"][region]), "--name", name])


def _around(rect, pad):
    return rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad


def test_blob_record_stores_a_point_with_its_box(plan_project, capsys):
    assert _blob(plan_project, "A", "box-p", _around(rs.BOX, 300)) == 0
    o = _read(plan_project["files"]["A"])["observations"]["box-p"]
    assert o["kind"] == "point" and o["tool"] == "blob" and o["pick"] is None
    x0, y0, x1, y1 = rs.BOX
    assert abs(o["point_wcs"][0] - (x0 + x1) / 2) <= PX_MM + 2 and abs(o["point_wcs"][1] - (y0 + y1) / 2) <= PX_MM + 2
    assert len(o["bbox_local"]) == 4 and "axis" not in o
    assert o["command"][0] == "blob" and "--record" not in o["command"] and "--name" not in o["command"]


def test_blob_record_refuses_a_truncated_box(plan_project, capsys):
    assert _blob(plan_project, "A", "cut", (14800, 10800, 15200, 11800)) == 1
    assert "truncated" in json.loads(capsys.readouterr().out)["message"]
    assert _read(plan_project["files"]["A"])["observations"] == {}


def test_record_refuses_another_calib_file(plan_project, tmp_path, capsys):
    other = tmp_path / "calib2.json"
    data = json.loads(plan_project["calib"].read_text(encoding="utf-8"))
    data["err_mm"] = 0.0
    other.write_text(json.dumps(data), encoding="utf-8")
    rc = m.main(["profile", str(plan_project["image"]), "--calib", str(other), "--axis", "v", "--at", "5000",
                 "--range", "-400", "400", "--band", "300", "--record", str(plan_project["files"]["A"]),
                 "--names", "a,b,c"])
    assert rc == 1 and "보정 파일" in json.loads(capsys.readouterr().out)["message"]


def test_record_refuses_a_duplicate_name(plan_project, capsys):
    assert _profile(plan_project, "A", "s-out,-,-") == 0
    capsys.readouterr()
    assert _profile(plan_project, "A", "s-out,-,-", at=6000) == 1
    assert "이미 있음" in json.loads(capsys.readouterr().out)["message"]


# ---------- 요소·질문·합치기 ----------

def _element(path, data):
    return rec.main(["add-element", str(path), "--json", json.dumps(data, ensure_ascii=False)])


def _merge(p, out=None):
    out = out or p["measure"].parent / "merged.yaml"
    rc = rec.main(["merge", str(p["measure"]), "--lock", str(p["lock"]), *map(str, p["files"].values()),
                   "-o", str(out)])
    return rc, out


def test_add_element_only_for_assigned_ids_and_known_observations(plan_project, capsys):
    assert _profile(plan_project, "A", "s-out,-,s-in") == 0
    capsys.readouterr()
    assert _element(plan_project["files"]["A"], {"id": "EB", "type": "wall"}) == 1
    assert "맡지 않은" in json.loads(capsys.readouterr().out)["message"]
    assert _element(plan_project["files"]["A"], {"id": "EA", "type": "wall", "refs": {"f1": "nope"}}) == 1
    assert "nope" in json.loads(capsys.readouterr().out)["message"]
    assert _element(plan_project["files"]["A"], {"id": "EA", "type": "wall",
                                                  "refs": {"f1": "s-out", "f2": "s-in"},
                                                  "candidates": {"material": ["RC", "조적"]}}) == 0
    assert _read(plan_project["files"]["A"])["elements"]["EA"]["refs"] == {"f1": "s-out", "f2": "s-in"}


def test_add_element_takes_plain_flags_without_json(plan_project, capsys):
    # PowerShell 5.1은 외부 프로그램에 넘기는 인자의 큰따옴표를 지워 --json을 깨뜨린다
    assert _profile(plan_project, "A", "s-out,_,s-in") == 0
    assert rec.main(["add-element", str(plan_project["files"]["A"]), "--id", "EA", "--type", "wall",
                     "--ref", "f1=s-out", "--ref", "f2=s-in", "--candidate", "material=RC,조적",
                     "--note", "남측 벽"]) == 0
    el = _read(plan_project["files"]["A"])["elements"]["EA"]
    assert el == {"type": "wall", "refs": {"f1": "s-out", "f2": "s-in"},
                  "candidates": {"material": ["RC", "조적"]}, "note": "남측 벽"}


def test_add_element_records_links_to_other_elements(plan_project, capsys):
    # 창·문이 어느 벽에 붙는지(host)는 요소 사이 관계다. 대상은 다른 영역 요소일 수 있어 validate가 확인한다
    assert _profile(plan_project, "A", "s-out,_,s-in") == 0
    assert rec.main(["add-element", str(plan_project["files"]["A"]), "--id", "EA", "--type", "window",
                     "--ref", "s=s-out", "--link", "host=EB"]) == 0
    assert _read(plan_project["files"]["A"])["elements"]["EA"]["links"] == {"host": "EB"}
    assert rec.main(["add-element", str(plan_project["files"]["A"]), "--json", "{}",
                     "--link", "host=EB"]) == 1


def test_add_element_refuses_confirmed_state_in_a_region_file(plan_project, capsys):
    assert _element(plan_project["files"]["A"], {"id": "EA", "type": "wall", "status": "확정"}) == 1


def _drop(path, name):
    return rec.main(["drop-observation", str(path), "--name", name])


def test_replace_swaps_an_assigned_element_for_a_remeasure(plan_project, capsys):
    a = plan_project["files"]["A"]
    assert _profile(plan_project, "A", "s-out,-,s-in") == 0
    assert _element(a, {"id": "EA", "type": "wall", "refs": {"f1": "s-out", "f2": "s-in"}}) == 0
    assert _profile(plan_project, "A", "s-out2,-,s-in2", at=6000) == 0
    capsys.readouterr()
    assert _element(a, {"id": "EA", "type": "wall", "refs": {"f1": "s-out2", "f2": "s-in2"}}) == 1
    assert "이미 있음" in json.loads(capsys.readouterr().out)["message"]
    assert rec.main(["add-element", str(a), "--id", "EA", "--type", "wall", "--ref", "f1=s-out2", "--ref", "f2=s-in2",
                     "--replace"]) == 0
    assert _read(a)["elements"]["EA"]["refs"] == {"f1": "s-out2", "f2": "s-in2"}
    capsys.readouterr()
    assert rec.main(["add-element", str(plan_project["files"]["B"]), "--id", "EB", "--type", "wall",
                     "--replace"]) == 1
    assert "없음" in json.loads(capsys.readouterr().out)["message"]
    assert rec.main(["add-element", str(a), "--id", "EB", "--type", "wall", "--replace"]) == 1
    assert "맡지 않은" in json.loads(capsys.readouterr().out)["message"]


def test_replace_keeps_the_owner_region_in_the_merged_measure(merged, capsys):
    assert rec.main(["add-element", str(merged["merged"]), "--id", "EA", "--type", "wall", "--ref", "f1=s-out",
                     "--ref", "f2=s-in", "--link", "next=EA2", "--replace"]) == 0
    el = _read(merged["merged"])["elements"]["EA"]
    assert el["region"] == "A" and el["links"] == {"next": "EA2"} and "candidates" not in el     # 요소 전체를 바꿈
    assert rec.lock_hash(_read(merged["merged"])) == rec.lock_hash(_read(merged["measure"]))


def test_elements_in_a_rotated_region_take_the_region_frame(tmp_path, capsys):
    img, sc = rs.plan(beta=0.8)
    regions = {"W": {"frame": "wing", "window": [0, -500, 9000, 5500], "elements": ["EW", "EX"]}}
    p = _project(tmp_path, capsys, img, sc, [(0, 0), (9850, 0), (0, 5700)], (9850, 5700), regions, WING_FRAME)
    w = p["files"]["W"]
    assert rec.main(["add-element", str(w), "--id", "EW", "--type", "wall"]) == 0
    assert rec.main(["add-element", str(w), "--id", "EX", "--type", "wall", "--frame", "wing"]) == 0
    els = _read(w)["elements"]
    assert els["EW"]["frame"] == "wing" and els["EX"]["frame"] == "wing"
    capsys.readouterr()
    assert rec.main(["add-element", str(w), "--id", "EW", "--type", "wall", "--frame", "nope", "--replace"]) == 1
    assert "frame" in json.loads(capsys.readouterr().out)["message"]


def test_drop_observation_only_when_nothing_refers_to_it(plan_project, capsys):
    a = plan_project["files"]["A"]
    assert _profile(plan_project, "A", "s-out,-,s-in") == 0
    assert _element(a, {"id": "EA", "type": "wall", "refs": {"f1": "s-out", "f2": "s-in"}}) == 0
    assert _profile(plan_project, "A", "spare,-,-", at=6000) == 0
    capsys.readouterr()
    assert _drop(a, "s-out") == 1
    assert "EA" in json.loads(capsys.readouterr().out)["message"]
    assert _drop(a, "nope") == 1
    capsys.readouterr()
    assert _drop(a, "spare") == 0
    assert set(_read(a)["observations"]) == {"s-out", "s-in"}


def test_drop_observation_refuses_one_a_header_value_measured_from(merged, capsys):
    data = _read(merged["merged"])
    data["grids"] = {"wcs": {"Y": {"Y0": {"value": data["observations"]["s-out"]["coord"], "source": "실측",
                                          "ref": "s-out.coord"}}}}
    data["elements"]["EA"]["refs"] = {"f2": "s-in"}
    merged["merged"].write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert _drop(merged["merged"], "s-out") == 1
    assert "grids.wcs.Y.Y0" in json.loads(capsys.readouterr().out)["message"]


def test_dropping_a_superseded_observation_clears_a_merge_conflict(tmp_path, capsys):
    p = _kink_project(tmp_path, capsys, 0.8)
    for region, at, name in (("A", 2500, "fa"), ("B", 7500, "fb")):
        assert m.main(["profile", str(p["image"]), "--calib", str(p["calib"]), "--axis", "v", "--at", str(at),
                       "--range", "0", "200", "--band", "300", "--record", str(p["files"][region]),
                       "--names", name]) == 0
    capsys.readouterr()
    assert _merge(p)[0] == 1
    capsys.readouterr()
    assert _drop(p["files"]["B"], "fb") == 0
    capsys.readouterr()
    assert _merge(p)[0] == 0


def test_merge_combines_regions_and_carries_questions(plan_project, capsys):
    assert _profile(plan_project, "A", "s-out,-,s-in") == 0
    assert _element(plan_project["files"]["A"], {"id": "EA", "type": "wall",
                                                  "refs": {"f1": "s-out", "f2": "s-in"}}) == 0
    assert rec.main(["question", "add", str(plan_project["files"]["A"]), "--id", "Q1", "--text", "RC인가 조적인가",
                     "--element", "EA"]) == 0
    assert _profile(plan_project, "B", "-,-,e-out", at=9000, rng=(-400, 400)) == 0
    capsys.readouterr()
    rc, out = _merge(plan_project)
    report = json.loads(capsys.readouterr().out)
    assert rc == 0, report
    merged = _read(out)
    assert set(merged["observations"]) == {"s-out", "s-in", "e-out"}
    assert merged["elements"]["EA"]["region"] == "A"
    assert merged["questions"] == [{"id": "Q1", "text": "RC인가 조적인가", "element": "EA", "region": "A"}]
    assert rec.lock_hash(merged) == rec.lock_hash(_read(plan_project["measure"]))


def test_merge_keeps_points_out_of_the_same_line_check(plan_project, capsys):
    assert _blob(plan_project, "A", "ring-a", _around((15300, 8300, 16700, 9700), 0)) == 0
    assert _blob(plan_project, "B", "ring-b", _around((15300, 8300, 16700, 9700), 50)) == 0
    capsys.readouterr()
    rc, out = _merge(plan_project)
    report = json.loads(capsys.readouterr().out)
    assert rc == 0, report
    assert report["same_lines"] == []
    assert _read(out)["observations"]["ring-b"]["kind"] == "point"


def test_merge_refuses_header_changed_after_lock(plan_project, capsys):
    data = _read(plan_project["measure"])
    data["frames"]["wing"]["deg"]["value"] = 23.80
    plan_project["measure"].write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    rc, out = _merge(plan_project)
    assert rc == 1 and "잠금" in json.loads(capsys.readouterr().out)["message"] and not out.exists()


def test_merge_refuses_a_region_file_from_another_lock(plan_project, capsys):
    data = _read(plan_project["files"]["B"])
    data["lock"] = "0" * 64
    plan_project["files"]["B"].write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    rc, _ = _merge(plan_project)
    assert rc == 1 and "region-B" in json.loads(capsys.readouterr().out)["message"]


def test_merge_refuses_the_same_name_in_two_regions(plan_project, capsys):
    assert _profile(plan_project, "A", "face,-,-") == 0
    assert _profile(plan_project, "B", "face,-,-", at=8000) == 0
    capsys.readouterr()
    rc, _ = _merge(plan_project)
    report = json.loads(capsys.readouterr().out)
    assert rc == 1 and any("face" in e for e in report["errors"])


def test_merge_refuses_hand_edited_ownership_and_answers(plan_project, capsys):
    data = _read(plan_project["files"]["A"])
    data["elements"]["EB"] = {"type": "wall"}
    data["questions"] = [{"id": "Q9", "text": "?", "answer": "RC", "by": "user"}]
    plan_project["files"]["A"].write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    rc, _ = _merge(plan_project)
    errors = json.loads(capsys.readouterr().out)["errors"]
    assert rc == 1
    assert any("EB" in e and "맡지 않은" in e for e in errors)
    assert any("Q9" in e and "답" in e for e in errors)


def test_merge_matches_the_same_line_across_regions_and_frames(plan_project, capsys):
    # A: 회전동 남측 면선을 회전 좌표 단면으로, B: 같은 면선을 정방향 좌표의 직선 맞춤으로
    assert m.main(["profile", str(plan_project["image"]), "--calib", str(plan_project["calib"]),
                   "--frame", "2200,6540,23.76", "--axis", "v", "--at", "4500", "--range", "-300", "100",
                   "--band", "300", "--record", str(plan_project["files"]["A"]), "--names", "wing-s-a"]) == 0
    p1, p2 = rs.Scene.local(rs.WING, 1000, 0), rs.Scene.local(rs.WING, 8000, 0)
    assert m.main(["fitline", str(plan_project["image"]), "--calib", str(plan_project["calib"]),
                   "--from", f"{p1[0]},{p1[1]}", "--to", f"{p2[0]},{p2[1]}", "--band", "100",
                   "--record", str(plan_project["files"]["B"]), "--name", "wing-s-b"]) == 0
    capsys.readouterr()
    rc, _ = _merge(plan_project)
    report = json.loads(capsys.readouterr().out)
    assert rc == 0, report
    assert ["wing-s-a", "wing-s-b"] in report["same_lines"]


def _kink_project(tmp_path, capsys, shift_px):
    """면선 y 100이 x 5000에서 shift_px만큼 어긋난 그림(A·B 영역이 같은 면을 잰 경우), 200 위에 다른 면"""
    sc = rs.Scene((760, 300), rs.S, origin=(60.0, 250.0))
    sc.line((0, 100), (5000, 100), rs.THICK)
    sc.line((5000, 100 + shift_px * PX_MM), (10000, 100 + shift_px * PX_MM), rs.THICK)
    sc.line((0, 300), (10000, 300), rs.THICK)
    regions = {"A": {"frame": None, "window": [0, -200, 5000, 600], "elements": []},
               "B": {"frame": None, "window": [5000, -200, 10000, 600], "elements": []}}
    return _project(tmp_path, capsys, sc.image(noise=0.02), sc, [(0, 0), (10000, 0), (0, 3000)], (10000, 3000),
                    regions)


@pytest.mark.parametrize("shift_px, conflict", [(0.0, False), (0.8, True)])
def test_merge_flags_a_face_measured_differently_in_two_regions(tmp_path, capsys, shift_px, conflict):
    p = _kink_project(tmp_path, capsys, shift_px)
    for region, at, rng, name in (("A", 2500, (0, 200), "fa"), ("B", 7500, (0, 200), "fb"),
                                  ("B", 7000, (200, 400), "other")):
        assert m.main(["profile", str(p["image"]), "--calib", str(p["calib"]), "--axis", "v", "--at", str(at),
                       "--range", str(rng[0]), str(rng[1]), "--band", "300", "--record", str(p["files"][region]),
                       "--names", name]) == 0
    capsys.readouterr()
    rc, _ = _merge(p)
    report = json.loads(capsys.readouterr().out)
    if conflict:
        assert rc == 1 and any("fa" in e and "fb" in e and "불일치" in e for e in report["errors"])
    else:
        assert rc == 0 and ["fa", "fb"] in report["same_lines"]
    assert not any("other" in e for e in report.get("errors", []))      # 200 떨어진 다른 면은 짝 짓지 않음
    assert not any("other" in pair for pair in report.get("same_lines", []))


# ---------- 확정·검사·재검증 ----------

@pytest.fixture
def merged(plan_project, capsys):
    assert _profile(plan_project, "A", "s-out,_,s-in") == 0
    assert _element(plan_project["files"]["A"], {"id": "EA", "type": "wall",
                                                  "refs": {"f1": "s-out", "f2": "s-in"},
                                                  "candidates": {"material": ["RC", "조적"]}}) == 0
    assert rec.main(["question", "add", str(plan_project["files"]["A"]), "--id", "Q1", "--text", "RC인가",
                     "--element", "EA"]) == 0
    capsys.readouterr()
    rc, out = _merge(plan_project, plan_project["measure"])
    assert rc == 0
    capsys.readouterr()
    return {**plan_project, "merged": out}


def _validate(p, *extra):
    return rec.main(["validate", str(p["merged"]), "--lock", str(p["lock"]), *extra])


def test_validate_is_not_ready_until_questions_and_interpretations_are_settled(merged, capsys):
    assert _validate(merged) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ready"] is False
    assert [q["id"] for q in report["unresolved_questions"]] == ["Q1"]
    assert any("wing" in e for e in report["estimates"])                  # 추정 값은 목록으로 남긴다
    assert rec.main(["interpret", str(merged["merged"]), "--element", "EA", "--attr", "material",
                     "--value", "RC", "--pending"]) == 0
    assert rec.main(["question", "answer", str(merged["merged"]), "--id", "Q1", "--answer", "RC", "--by", "user"]) == 0
    capsys.readouterr()
    assert _validate(merged) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ready"] is False and report["pending_interpretations"] == ["EA.material"]
    assert rec.main(["interpret", str(merged["merged"]), "--element", "EA", "--attr", "material",
                     "--value", "RC", "--by", "user"]) == 0
    capsys.readouterr()
    assert _validate(merged) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ready"] is True
    assert _read(merged["merged"])["interpretations"]["EA"]["material"] == {"value": "RC", "status": "사용자확정",
                                                                            "by": "user"}


def test_validate_catches_links_to_missing_or_same_elements(merged, capsys):
    data = _read(merged["merged"])
    data["elements"]["EA"]["links"] = {"host": "NOPE", "next": "EA"}
    merged["merged"].write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert _validate(merged) == 1
    errors = json.loads(capsys.readouterr().out)["errors"]
    assert any("EA.host" in e and "NOPE" in e for e in errors)
    assert any("EA.next" in e and "자기 자신" in e for e in errors)


def test_answers_and_interpretations_only_go_into_measure(merged, capsys):
    region = merged["files"]["A"]
    assert rec.main(["question", "answer", str(region), "--id", "Q1", "--answer", "RC", "--by", "user"]) == 1
    assert rec.main(["interpret", str(region), "--element", "EA", "--attr", "material", "--value", "RC",
                     "--by", "user"]) == 1
    assert rec.main(["interpret", str(merged["merged"]), "--element", "NOPE", "--attr", "material",
                     "--value", "RC", "--by", "user"]) == 1


def test_validate_catches_a_measured_value_that_differs_from_its_observation(merged, capsys):
    data = _read(merged["merged"])
    coord = data["observations"]["s-out"]["coord"]
    data["grids"] = {"main": {"Y": {
        "Y0": {"value": coord, "source": "실측", "ref": "s-out.coord"},          # 기록값 그대로
        "Y1": {"value": coord + 5.0, "source": "실측", "ref": "s-out.coord"},    # 옮겨 적다 틀림
        "Y9": {"value": 1.0, "source": "실측", "ref": "gone.coord"}}}}
    merged["merged"].write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert rec.main(["validate", str(merged["merged"])]) == 1
    errors = json.loads(capsys.readouterr().out)["errors"]
    assert any("Y1" in e and "다름" in e for e in errors)
    assert not any("Y0" in e for e in errors)
    assert any("Y9" in e and "gone" in e for e in errors)


def test_validate_refuses_a_header_changed_after_lock(merged, capsys):
    data = _read(merged["merged"])
    data["regions"]["A"]["window"] = [0, 0, 1, 1]
    merged["merged"].write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _validate(merged) == 1
    assert any("잠금" in e for e in json.loads(capsys.readouterr().out)["errors"])


def test_reverify_passes_untouched_records_and_catches_hand_edits(merged, capsys):
    assert _validate(merged, "--reverify") == 0
    assert json.loads(capsys.readouterr().out)["reverified"] == 2
    data = _read(merged["merged"])
    data["observations"]["s-out"]["coord"] += 40.0                      # 손으로 고친 숫자
    data["observations"]["s-in"]["command"][data["observations"]["s-in"]["command"].index("--at") + 1] = "5200.0"
    data["observations"]["s-in"]["point_wcs"][0] = 1.0                  # 명령을 바꾸고 값도 바꿈
    merged["merged"].write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _validate(merged, "--reverify") == 1
    errors = json.loads(capsys.readouterr().out)["errors"]
    assert any("s-out" in e and "coord" in e for e in errors)
    assert any("s-in" in e for e in errors)


def test_reverify_reruns_points_and_catches_an_edited_box(merged, capsys):
    data = _read(merged["merged"])
    assert _blob(merged, "A", "box-p", _around(rs.BOX, 300)) == 0       # 영역 파일에 기록한 뒤
    data["observations"]["box-p"] = _read(merged["files"]["A"])["observations"]["box-p"]
    merged["merged"].write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    capsys.readouterr()
    assert _validate(merged, "--reverify") == 0
    assert json.loads(capsys.readouterr().out)["reverified"] == 3
    data["observations"]["box-p"]["bbox_local"][2] += 30.0
    merged["merged"].write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _validate(merged, "--reverify") == 1
    assert any("box-p" in e and "bbox_local" in e for e in json.loads(capsys.readouterr().out)["errors"])
