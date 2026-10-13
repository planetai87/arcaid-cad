"""표준매뉴얼 DWG → 표준 레이어/블록만 남긴 회사 템플릿 DWG 생성 (원본은 읽기 전용으로 열고 다른 이름으로 저장).

템플릿을 처음부터 다시 만들 때의 순서(GstarCAD 실행 중이어야 함):
  1. python build_template.py <표준매뉴얼.dwg> <리포트.json>
  2. python add_standard_blocks.py
  3. python fix_block_origins.py
--out을 주지 않으면 스킬 템플릿(skills/cad-drawing-standard/assets/회사표준_템플릿.dwg)을 덮어쓴다.
"""
import argparse
import json
import os
import time
from pathlib import Path

DEFAULT_TPL = Path(__file__).resolve().parents[2] / "skills" / "cad-drawing-standard" / "assets" / "회사표준_템플릿.dwg"

parser = argparse.ArgumentParser(description="표준매뉴얼 DWG에서 회사 템플릿 DWG를 만든다")
parser.add_argument("manual", help="표준매뉴얼 DWG 경로(읽기 전용으로 연다)")
parser.add_argument("report", help="결과 리포트 JSON 경로")
parser.add_argument("--out", default=str(DEFAULT_TPL), help="만들 템플릿 경로(기본: 스킬 템플릿)")
args = parser.parse_args()

import pythoncom  # noqa: E402 (--help는 COM 없이 끝나게 인자를 먼저 읽는다)
import win32com.client  # noqa: E402

SRC = os.path.abspath(args.manual)
DST = os.path.abspath(args.out)
REPORT = args.report

# 매뉴얼 'LAYER 체계' 표 기준 (레이어명: ACI 색상). 선종류는 매뉴얼 DWG의 레이어 설정을 유지.
STANDARD = {
    "0": 7, "Defpoints": 8,
    "CEN": 1, "CON": 2, "CIL": 6, "CIL1": 7, "CIR": 4, "LIM": 2, "DIM": 7, "DIM1": 7,
    "DOR": 4, "ELE": 7, "FIN": 6, "FUR": 5, "HAT": 8, "HAT1": 9, "HID": 8, "INS": 8,
    "LAN": 9, "LEV": 3, "LGT": 3,
    "INT": 8, "PAK": 9, "PAK1": 7, "PAT": 9, "REM": 3, "ROM": 3, "SHT": 7, "STL": 3,
    "STL1": 4, "SPR": 2, "SYM": 3, "SYM1": 7, "TIT": 7, "TIT1": 3, "TXT": 3, "TXT1": 7,
    "TOL": 4, "WAL": 3, "WAL1": 7, "WIN": 4,
}

KEEP_BLOCKS = {
    "HEOarchitects_sheet", "HEOlogo1", "Block_0", "방위", "LEV", "레벨", "입단면층레벨", "축열",
    "실명", "단면부호", "단면부호횡", "M-SD-편개", "M-AD 양개", "xi_parking_3", "xi_parking_wide_3",
    "xi_parking_small_3", "xi_parking_hc_33_mark", "TAG-LEVEL", "ARR", "ARR2",
    "dots",  # DIM270/DIM330 치수 스타일의 화살표 블록
}

# 남기는 블록 내부에서 쓰는 비표준 레이어 → 표준 레이어로 이전
REMAP = {
    "G-BORD-FORM": "TIT", "A-TEXT1": "TIT1", "A-NPLT": "Defpoints", "SYMBOL": "SYM1",
    "RB": "ROM", "SM": "SYM", "door": "DOR", "A-ST-AXIS-BUB": "DIM",
}


def call(fn, *a, tries=60):
    for i in range(tries):
        try:
            return fn(*a)
        except pythoncom.com_error as e:
            if e.hresult in (-2147418111, -2147417846) and i < tries - 1:
                time.sleep(0.5)
                continue
            raise


def g(obj, name, default=None):
    try:
        return call(getattr, obj, name)
    except Exception:
        return default


def key(name):
    return name.casefold()


report = {"erased": 0, "blocks_deleted": [], "blocks_kept": [], "layers_deleted": [],
          "layers_not_deleted": {}, "layer_fixes": [], "remapped_in_blocks": 0, "dimstyles": {}}

os.makedirs(os.path.dirname(DST), exist_ok=True)
app = win32com.client.Dispatch("GCAD.Application")
doc = call(call(getattr, app, "Documents").Open, SRC, True)
time.sleep(2)
call(doc.SaveAs, DST)  # 이후 변경은 모두 새 파일에만 적용
time.sleep(1)

# 1) 모든 레이아웃의 객체 삭제 (잠긴 레이어 해제 먼저)
for lay in call(getattr, doc, "Layers"):
    try:
        call(setattr, lay, "Lock", False)
    except Exception:
        pass
for lo in call(getattr, doc, "Layouts"):
    blk = g(lo, "Block")
    ents = [e for e in blk]
    for e in ents:
        try:
            call(e.Delete)
            report["erased"] += 1
        except Exception:
            pass

# 2) 다른 사무소 치수 스타일 삭제 → 불필요한 블록 정의 삭제 (중첩 참조가 풀릴 때까지 반복)
#    COM 컬렉션은 한 번 순회하면 재사용이 안 되므로 매번 새로 가져온다.
for ds in [d for d in call(getattr, doc, "DimStyles")]:
    if g(ds, "Name", "").upper().startswith("WISE"):
        try:
            call(ds.Delete)
        except Exception:
            pass
for _ in range(10):
    progress = False
    for b in [b for b in call(getattr, doc, "Blocks")]:
        nm = g(b, "Name", "")
        if nm.startswith("*") or nm in KEEP_BLOCKS or nm in report["blocks_deleted"]:
            continue
        try:
            call(b.Delete)
            report["blocks_deleted"].append(nm)
            progress = True
        except Exception:
            pass
    if not progress:
        break
report["blocks_kept"] = [g(b, "Name") for b in call(getattr, doc, "Blocks") if not g(b, "Name", "").startswith("*")]

# 3) 남긴 블록 내부 객체의 비표준 레이어를 표준 레이어로 이전
for b in [b for b in call(getattr, doc, "Blocks")]:
    if g(b, "Name", "").startswith("*"):
        continue
    for e in b:
        lname = g(e, "Layer")
        if lname in REMAP:
            target = REMAP[lname]
            if target == "Defpoints":
                target = next(l.Name for l in call(getattr, doc, "Layers") if key(l.Name) == "defpoints")
            call(setattr, e, "Layer", target)
            report["remapped_in_blocks"] += 1

# 4) 표준 레이어 색상 맞추기 / 없으면 생성
layers = call(getattr, doc, "Layers")
existing = {key(g(l, "Name")): l for l in layers}
for name, aci in STANDARD.items():
    lay = existing.get(key(name))
    if lay is None:
        lay = call(layers.Add, name)
        report["layer_fixes"].append(f"{name}: 생성")
    old = g(lay, "Color")
    if old != aci:
        call(setattr, lay, "Color", aci)
        report["layer_fixes"].append(f"{name}: {old} → {aci}")
    call(setattr, lay, "LayerOn", True)
    try:
        call(setattr, lay, "Freeze", False)  # 현재 레이어는 동결 해제 설정이 거부될 수 있음
    except Exception:
        pass

# 5) 현재 레이어를 0으로 두고 비표준 레이어 삭제
call(setattr, doc, "ActiveLayer", existing["0"])
std_keys = {key(n) for n in STANDARD}
for lay in [l for l in call(getattr, doc, "Layers")]:
    nm = g(lay, "Name")
    if key(nm) in std_keys:
        continue
    try:
        call(lay.Delete)
        report["layers_deleted"].append(nm)
    except Exception as ex:
        report["layers_not_deleted"][nm] = str(ex)[:80]

# 6) 기본 문자 스타일, 치수 스타일 정보
for ts in call(getattr, doc, "TextStyles"):
    if g(ts, "Name") == "돋움":
        call(setattr, doc, "ActiveTextStyle", ts)
for ds in call(getattr, doc, "DimStyles"):
    nm = g(ds, "Name")
    try:
        call(setattr, doc, "ActiveDimStyle", ds)
        report["dimstyles"][nm] = {v: call(doc.GetVariable, v) for v in
                                   ("DIMSCALE", "DIMTXT", "DIMASZ", "DIMTXSTY", "DIMBLK", "DIMDEC", "DIMEXO", "DIMEXE")}
    except Exception as ex:
        report["dimstyles"][nm] = str(ex)[:80]

call(doc.Save)
call(doc.Close, False)
json.dump(report, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps({k: (v if not isinstance(v, list) or len(v) < 60 else f"{len(v)}개") for k, v in report.items()},
                 ensure_ascii=False, indent=1))
