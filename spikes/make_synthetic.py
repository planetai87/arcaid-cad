"""합성 시험 도면 만들기: 회사 템플릿 사본에 표준대로(1/100) 여러 객체 종류를 그린다

결과: WORK/synthetic_base.dwg, WORK/results/synthetic_manifest.json
알려진 위반 2개(레이어 0 선, 색 직접 지정 선)를 일부러 넣는다.
"""
import json

from _harness import RESULTS, TEMPLATE, WORK, Spike
from _inspect import attribs, counts, live_summary, texts

S = 1  # s = k/100, 1/100

with Spike("synthetic") as s:
    s.copy_and_open(TEMPLATE, "synthetic_base.dwg")
    ctl = s.ctl
    doc = s.guard()
    s.record("cannoscale", ctl._call_attr(doc, "GetVariable", "CANNOSCALE"))
    s.record("insunits", ctl._call_attr(doc, "GetVariable", "INSUNITS"))
    s.cmd("_.ATTDIA\n0\n")
    s.cmd("_.ATTREQ\n1\n")

    # 1) 축선(CEN): X1~X3, Y1~Y2
    xs, ys = [0, 6000, 12000], [0, 6000]
    for x in xs:
        ctl.draw_line((x, -450), (x, 6450), layer="CEN")
    for y in ys:
        ctl.draw_line((-450, y), (12450, y), layer="CEN")

    # 2) 벽: CON 외벽(중심선 ±100 두 겹), WAL1 칸막이(두께 100)
    ctl.draw_polyline([(-100, -100), (12100, -100), (12100, 6100), (-100, 6100)], closed=True, layer="CON")
    ctl.draw_polyline([(100, 100), (11900, 100), (11900, 5900), (100, 5900)], closed=True, layer="CON")
    ctl.draw_polyline([(5950, 100), (6050, 100), (6050, 5900), (5950, 5900)], closed=True, layer="WAL1")

    # 3) 문(DOR): 문짝 선 + 90° 호, 창(WIN): 두 줄
    ctl.draw_line((6050, 1000), (6950, 1000), layer="DOR")
    ctl.draw_arc((6050, 1000), 900, 0, 90, layer="DOR")
    ctl.draw_line((2000, -100), (4000, -100), layer="WIN")
    ctl.draw_line((2000, 100), (4000, 100), layer="WIN")

    # 4) 해치(HAT): 패턴 해치, GstarCAD는 -HATCH로 만들어진다
    h = ctl.draw_hatch([(500, 4500), (1500, 4500), (1500, 5500), (500, 5500)], "ANSI31", 50, layer="HAT")
    s.record("hatch_created", h is not None)

    # 5) 실명(ROM): 한글, 가운데 맞춤(entmake MC)
    for x, name in [(3000, "객실 101"), (9000, "객실 102")]:
        s.lisp(f'(entmake \'((0 . "TEXT") (8 . "ROM") (7 . "돋움") (10 {x} 3000 0.0) (11 {x} 3000 0.0) '
               f'(40 . {135 * S}) (1 . "{name}") (72 . 1) (73 . 2)))')

    # 6) 일반 문자(TXT), 여러 줄 문자(MTEXT)
    ctl.draw_text((500, -1500), "일반 문자 TXT 한글", height=135 * S, layer="TXT")
    ms = ctl._ms()
    mt = ctl._call_attr(ms, "AddMText", ctl._pt((6500, -1300)), 3000, "여러 줄 문자\\P둘째 줄")
    ctl._call(setattr, mt, "Layer", "TXT")
    ctl._call(setattr, mt, "Height", 135 * S)

    # 7) 대지경계선 흉내(LIM + 객체 선종류 PHANTOM)
    lim = ctl.draw_line((-2000, -2500), (14000, -2500), layer="LIM")
    ctl._call(setattr, lim, "Linetype", "PHANTOM")

    # 8) 치수(DIM330, DIMSCALE 50): 주열과 전체
    s.cmd("_.-DIMSTYLE\n_R\nDIM330\n")
    s.cmd(f"_.DIMSCALE\n{50 * S}\n")
    ctl._call_attr(doc, "SetVariable", "CLAYER", "DIM")
    s.lisp('(progn (setvar "OSMODE" 0) '
           '(command "_.DIMLINEAR" (list 0 6450) (list 6000 6450) "_H" (list 3000 7350)) '
           '(command "_.DIMLINEAR" (list 6000 6450) (list 12000 6450) "_H" (list 9000 7350)) '
           '(command "_.DIMLINEAR" (list 0 6450) (list 12000 6450) "_H" (list 6000 7750)) '
           '(command "_.DIMLINEAR" (list -450 0) (list -450 6000) "_V" (list -1350 3000)) T)')

    # 9) 축열 버블(DIM, 속성): 상부 X1~X3, 좌측 Y1~Y2
    for i, x in enumerate(xs, 1):
        s.cmd(f"_.-INSERT\n축열버블-상\n{x},7750\n{S}\n{S}\n0\nX{i}\n")
    for i, y in enumerate(ys, 1):
        s.cmd(f"_.-INSERT\n축열버블-좌\n-1750,{y}\n{S}\n{S}\n0\nY{i}\n")
    # 계단 화살표(DIM)
    s.cmd(f"_.-INSERT\n계단화살표\n9000,1500\n{S}\n{S}\n0\n")

    # 10) 레벨 기호(LEV) + 문자
    ctl._call_attr(doc, "SetVariable", "CLAYER", "LEV")
    s.cmd(f"_.-INSERT\nLEV\n3000,2400\n{0.5 * S}\n{0.5 * S}\n0\n")
    ctl.draw_text((3250, 2562), "FL%%P0", height=135 * S, layer="LEV")
    ctl._call_attr(doc, "SetVariable", "CLAYER", "0")

    # 11) 알려진 위반 2개
    ctl.draw_line((-2000, -3000), (0, -3000), layer="0")
    ctl.draw_line((-2000, -3200), (0, -3200), layer="TXT", color=1)

    s.record("cmdactive", ctl._read_cmdactive())
    ctl._call_attr(doc, "Save")
    live = live_summary(ctl, doc)
    manifest = {
        "path": str(WORK / "synthetic_base.dwg"),
        "counts": {f"{t}@{l}": n for (t, l), n in sorted(counts(live).items())},
        "texts": texts(live),
        "attribs": attribs(live),
        "entities": live["entities"],
        "insunits": live["insunits"],
    }
    (RESULTS / "synthetic_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                                     encoding="utf-8")
    s.record("counts", manifest["counts"])
    s.record("texts", manifest["texts"])
    s.record("attribs", manifest["attribs"])
