"""ops 폴더(op별 명세 JSON + hashes.json) 검사·묶기 도구(CAD 없음).

ops 폴더 규약: `<op>.json` 명세 파일들과, 적용 순서대로 [{"op", "spec_hash", "bytes"?}]를 적은 `hashes.json`.

사용(저장소 루트):
  python tools/ops/ops.py verify OPS_DIR
  python tools/ops/ops.py bind OPS_DIR --out RUN_DIR --drawing-id ID --snapshot-id SID

verify: CAD에 넣기 전에 모든 op를 끝까지 확인한다. 목록·파일 대응(중복·누락·목록 밖 파일·이름 불일치),
  해시, 표준 검증, 배치 전개(SPEC_ID 등), op 사이 standard·units·scale·target 일치.
bind: verify를 통과한 원본은 그대로 두고, 새 실행 사본에만 target을 넣어 hashes.json을 다시 쓴다.
  target은 spec_hash에 들어가므로 한 번 묶은 사본은 다시 쓰지 않는다(재전송·재개는 그 사본을 그대로 쓴다).
표준 출력은 UTF-8 JSON 하나이고, 통과하면 종료 코드 0, 아니면 1이다.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.layout import expand  # noqa: E402
from src.engine.spec import SpecError, parse_spec, spec_hash, validate_spec  # noqa: E402
from src.engine.standards import load_standard  # noqa: E402

HASHES = "hashes.json"
BIND = "bind.json"
OP_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _codes(violations):
    return [f"{v.code}: {v.message}" for v in violations[:5]]


def _target(spec):
    t = spec.target
    return None if t is None or (t.drawing_id is None and t.snapshot_id is None) else t.model_dump()


def verify(ops_dir, std=None):
    ops_dir = Path(ops_dir)
    std = std or load_standard()
    problems, rows = [], []
    try:
        listed = json.loads((ops_dir / HASHES).read_text(encoding="utf-8-sig"))
        if not isinstance(listed, list) or not all(isinstance(r, dict) and "op" in r and "spec_hash" in r for r in listed):
            raise ValueError("hashes.json은 [{op, spec_hash}, ...] 목록이어야 함")
    except (OSError, ValueError) as e:
        return {"ok": False, "dir": str(ops_dir), "ops": [], "problems": [f"{HASHES}: {e}"]}

    names = [r["op"] for r in listed]
    for name in sorted({n for n in names if names.count(n) > 1}):
        problems.append(f"op 중복: {name}")
    files = {p.stem for p in ops_dir.glob("*.json") if p.name not in (HASHES, BIND)}
    for name in sorted(files - set(names)):
        problems.append(f"목록에 없는 명세 파일: {name}.json")

    shared = {}
    for entry in listed:
        name = entry["op"]
        row = {"op": name, "hash_ok": False, "valid": False, "expanded": None, "errors": [], "warnings": []}
        rows.append(row)
        if not isinstance(name, str) or not OP_NAME.match(name):
            row["errors"].append("op 이름이 파일 이름으로 쓸 수 없음(폴더 밖 경로 금지)")
            continue
        path = ops_dir / f"{name}.json"
        try:
            spec = parse_spec(path.read_text(encoding="utf-8-sig"))
        except OSError as e:
            row["errors"].append(f"파일 읽기 실패: {e}")
            continue
        except SpecError as e:
            row["errors"] += _codes(e.violations)
            continue
        if spec.op != name:
            row["errors"].append(f"파일 안의 op가 다름: {spec.op}")
        row["spec_hash"] = spec_hash(spec)
        row["hash_ok"] = row["spec_hash"] == entry["spec_hash"]
        if not row["hash_ok"]:
            row["errors"].append(f"해시 불일치: 목록 {entry['spec_hash']}, 파일 {row['spec_hash']}")
        problems_v = validate_spec(spec, std)
        row["valid"] = not problems_v
        row["errors"] += _codes(problems_v)
        if row["valid"]:
            k = spec.scale if spec.scale is not None else std.profile.scale.default_k
            try:
                row["expanded"] = len(expand(spec, std, k, warnings=row["warnings"]))
            except SpecError as e:
                row["errors"] += _codes(e.violations)
        for key, value in (("standard", spec.standard), ("units", spec.units), ("scale", spec.scale),
                           ("target", json.dumps(_target(spec), sort_keys=True))):
            shared.setdefault(key, set()).add(value)

    for key, values in shared.items():
        if len(values) > 1:
            problems.append(f"op마다 {key} 값이 다름: {sorted(map(str, values))}")
    problems += [f"{r['op']}: {msg}" for r in rows for msg in r["errors"]]
    target = json.loads(next(iter(shared["target"]))) if shared.get("target") and len(shared["target"]) == 1 else None
    return {"ok": not problems, "dir": str(ops_dir), "count": len(rows), "target": target,
            "objects": sum(r["expanded"] or 0 for r in rows), "ops": rows, "problems": problems}


def bind(ops_dir, out_dir, drawing_id, snapshot_id, std=None):
    ops_dir, out_dir = Path(ops_dir), Path(out_dir)
    std = std or load_standard()
    if out_dir.exists() and any(out_dir.iterdir()):
        return {"ok": False, "problems": [f"실행 사본 폴더가 비어 있지 않음(이미 묶인 사본은 다시 쓰지 않음): {out_dir}"]}
    if out_dir.resolve() == ops_dir.resolve():
        return {"ok": False, "problems": ["원본 폴더에 묶지 않는다(--out은 새 폴더)"]}
    checked = verify(ops_dir, std)
    if not checked["ok"]:
        return {"ok": False, "problems": ["원본 verify 실패"] + checked["problems"]}
    target = {"drawing_id": drawing_id, "snapshot_id": snapshot_id}
    out_dir.mkdir(parents=True, exist_ok=True)
    listed = []
    for row in checked["ops"]:
        data = json.loads((ops_dir / f"{row['op']}.json").read_text(encoding="utf-8-sig"))
        data["target"] = target
        text = json.dumps(data, ensure_ascii=False, indent=1)
        (out_dir / f"{row['op']}.json").write_text(text, encoding="utf-8")
        listed.append({"op": row["op"], "bytes": len(text.encode("utf-8")), "spec_hash": spec_hash(parse_spec(data))})
    (out_dir / HASHES).write_text(json.dumps(listed, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / BIND).write_text(json.dumps({
        "source": str(ops_dir.resolve()), "source_hashes": [{"op": r["op"], "spec_hash": r["spec_hash"]} for r in checked["ops"]],
        "target": target, "bound_at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    result = verify(out_dir, std)
    result["bound_from"] = str(ops_dir)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="ops 폴더 검사·묶기(CAD 없음)")
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("verify", help="CAD에 넣기 전 모든 op 확인")
    v.add_argument("ops_dir")
    b = sub.add_parser("bind", help="실행 사본에 target을 넣는다(원본은 그대로)")
    b.add_argument("ops_dir")
    b.add_argument("--out", required=True, help="새(빈) 실행 사본 폴더")
    b.add_argument("--drawing-id", required=True)
    b.add_argument("--snapshot-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "verify":
        result = verify(args.ops_dir)
    else:
        result = bind(args.ops_dir, args.out, args.drawing_id, args.snapshot_id)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
