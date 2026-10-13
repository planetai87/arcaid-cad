"""표준매뉴얼 DWG에서 레이어/스타일/문자 내용을 읽기 전용으로 추출해 JSON으로 저장."""
import argparse
import json
import os
import time

parser = argparse.ArgumentParser(description="표준매뉴얼 DWG의 레이어·스타일·문자를 JSON으로 추출한다")
parser.add_argument("manual", help="표준매뉴얼 DWG 경로(읽기 전용으로 연다)")
parser.add_argument("out", help="출력 JSON 경로")
args = parser.parse_args()

import pythoncom  # noqa: E402 (--help는 COM 없이 끝나게 인자를 먼저 읽는다)
import win32com.client  # noqa: E402

SRC = os.path.abspath(args.manual)
OUT = args.out


def call(fn, *a, tries=40):
    for i in range(tries):
        try:
            return fn(*a)
        except pythoncom.com_error as e:
            # RPC_E_CALL_REJECTED / 서버 바쁨 → 재시도
            if e.hresult in (-2147418111, -2147417846) and i < tries - 1:
                time.sleep(0.5)
                continue
            raise


def g(obj, name, default=None):
    try:
        return call(getattr, obj, name)
    except Exception:
        return default


def color_of(obj):
    info = {"aci": g(obj, "Color")}
    tc = g(obj, "TrueColor")
    if tc is not None:
        method = g(tc, "ColorMethod")
        info["method"] = method
        # 194 = acColorMethodByRGB
        if method == 194:
            info["rgb"] = [g(tc, "Red"), g(tc, "Green"), g(tc, "Blue")]
    return info


def pt(p):
    try:
        return [round(float(v), 2) for v in p][:2]
    except Exception:
        return None


def dump_entity(e, space):
    name = g(e, "ObjectName", "")
    rec = {"space": space, "type": name, "layer": g(e, "Layer")}
    if name in ("AcDbText", "AcDbMText"):
        rec["text"] = g(e, "TextString")
        rec["pos"] = pt(g(e, "InsertionPoint"))
        rec["height"] = g(e, "Height")
        rec["style"] = g(e, "StyleName")
    elif name == "AcDbTable":
        rows, cols = g(e, "Rows", 0), g(e, "Columns", 0)
        cells = []
        for r in range(rows):
            row = []
            for c in range(cols):
                try:
                    row.append(call(e.GetText, r, c))
                except Exception:
                    row.append(None)
            cells.append(row)
        rec["cells"] = cells
        rec["pos"] = pt(g(e, "InsertionPoint"))
    elif name == "AcDbBlockReference":
        rec["block"] = g(e, "EffectiveName") or g(e, "Name")
        rec["pos"] = pt(g(e, "InsertionPoint"))
        if g(e, "HasAttributes"):
            try:
                rec["attrs"] = {g(a, "TagString"): g(a, "TextString") for a in call(e.GetAttributes)}
            except Exception:
                pass
    else:
        return None
    return rec


def main():
    app = win32com.client.Dispatch("GCAD.Application")
    for _ in range(60):
        try:
            app.Visible = True
            break
        except Exception:
            time.sleep(1)
    docs = call(getattr, app, "Documents")
    doc = call(docs.Open, SRC, True)  # ReadOnly
    time.sleep(2)

    data = {"source": SRC, "layers": [], "linetypes": [], "textstyles": [], "dimstyles": [],
            "blocks": [], "layouts": [], "entities": [], "entity_type_counts": {}}

    for lay in call(getattr, doc, "Layers"):
        data["layers"].append({
            "name": g(lay, "Name"), "color": color_of(lay), "linetype": g(lay, "Linetype"),
            "lineweight": g(lay, "Lineweight"), "plottable": g(lay, "Plottable"),
            "description": g(lay, "Description"), "on": g(lay, "LayerOn"),
            "frozen": g(lay, "Freeze"), "locked": g(lay, "Lock"),
        })
    for lt in call(getattr, doc, "Linetypes"):
        data["linetypes"].append({"name": g(lt, "Name"), "description": g(lt, "Description")})
    for ts in call(getattr, doc, "TextStyles"):
        data["textstyles"].append({"name": g(ts, "Name"), "font": g(ts, "fontFile"),
                                   "bigfont": g(ts, "BigFontFile"), "height": g(ts, "Height"),
                                   "width": g(ts, "Width")})
    for ds in call(getattr, doc, "DimStyles"):
        data["dimstyles"].append({"name": g(ds, "Name")})
    for b in call(getattr, doc, "Blocks"):
        nm = g(b, "Name", "")
        if not nm.startswith("*"):
            data["blocks"].append({"name": nm, "count": g(b, "Count")})

    for lo in call(getattr, doc, "Layouts"):
        lname = g(lo, "Name")
        data["layouts"].append(lname)
        blk = g(lo, "Block")
        if blk is None:
            continue
        for e in blk:
            t = g(e, "ObjectName", "?")
            key = f"{lname}:{t}"
            data["entity_type_counts"][key] = data["entity_type_counts"].get(key, 0) + 1
            rec = dump_entity(e, lname)
            if rec:
                data["entities"].append(rec)

    call(doc.Close, False)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("layers", len(data["layers"]), "entities(text/table/block)", len(data["entities"]))
    print(json.dumps(data["entity_type_counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
