"""CAD 없이 DXF를 읽고·검사하고·만드는 파일 명령(구조 결정 D1·D8)"""
import argparse
import json
import sys
from itertools import combinations
from pathlib import Path

from . import egress
from .config import get_config
from .engine.apply import CheckFixResult
from .engine.ops import CHECK_LIMIT, check_file, limit_build
from .engine.read import ReadDrawingResult, read_drawing
from .engine.results import EngineResult, Scope, Status
from .engine.standards import StandardError, load_standard
from .engine.write import BuildResult, build_dxf

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = {"read": "read_drawing", "check": "check_standard", "build": "build"}
RESULTS = {"read": ReadDrawingResult, "check": CheckFixResult, "build": BuildResult}


def _scale(value):
    try:
        k = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("축척은 1~10000 정수여야 합니다") from None
    if not 1 <= k <= 10000:
        raise argparse.ArgumentTypeError("축척은 1~10000 정수여야 합니다")
    return k


def _limit(value):
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("상한은 1 이상 정수여야 합니다") from None
    if n < 1:
        raise argparse.ArgumentTypeError("상한은 1 이상 정수여야 합니다")
    return n


def _parser():
    parser = argparse.ArgumentParser(prog="arcaid", description="CAD 없이 도면 파일을 읽고·검사하고·만듭니다")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("read", "DXF 읽기"), ("check", "DXF 표준 검사"), ("build", "명세 → DXF 작성")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("source", help="객체 명세 JSON" if name == "build" else "DXF 파일")
        command.add_argument("--full", help="전체 결과를 저장할 JSON 파일")
        command.add_argument("--policy", help="외부 반환 정책 YAML(cwd 기준)")
        command.add_argument("--standard", help="표준 프로파일 폴더")
        if name == "check":
            command.add_argument("--scale", type=_scale, help="A3 축척 분모(1~10000)")
            command.add_argument("--limit", type=_limit, default=CHECK_LIMIT, help="코드별 위반 반환 상한(기본 20)")
            command.add_argument("--strict", action="store_true", help="위반·검사 불가 항목이 있으면 종료 코드 3")
        if name == "build":
            command.add_argument("-o", "--output", required=True, help="출력 DXF 파일")
    return parser


def _validate_paths(args):
    """입력·출력·전체 결과가 같은 파일이면 엔진 실행 전에 거부한다"""
    paths = [Path(p) for p in (args.source, getattr(args, "output", None), args.full) if p is not None]
    for left, right in combinations(paths, 2):
        same = left.samefile(right) if left.exists() and right.exists() else left.resolve() == right.resolve()
        if same:
            raise ValueError("입력·출력·--full 경로가 같은 파일입니다")


def _run(args):
    """엔진 결과와 명시적으로 요청한 전체 파일의 저장 실패 여부를 돌려준다"""
    _validate_paths(args)
    std = load_standard(args.standard)
    source = Path(args.source)
    full = Path(args.full) if args.full is not None else None
    if args.command in ("read", "check") and source.suffix.lower() == ".dwg":
        return RESULTS[args.command](status=Status.FAILED, scope=Scope(target=args.source),
                                     summary="DWG 읽기에는 CAD가 필요합니다(MCP export_snapshot으로 DXF 사본을 만드세요)"), False
    if args.command == "read":
        result = read_drawing(source, std, full_out=full)
    elif args.command == "check":
        result = check_file(source, std, k=args.scale, full_out=full, limit=args.limit)
    else:
        result = build_dxf(source.read_text(encoding="utf-8"), std, Path(args.output))
        failed = False
        if full is not None:
            try:
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(result.model_dump_json(), encoding="utf-8")
            except OSError as e:
                result.warnings.append(f"전체 작성 결과 파일을 쓰지 못함({full}): {e}")
                failed = True
        return limit_build(result), failed
    return result, full is not None and result.full_path is None


def _print_result(result, policy=None, tool=None):
    payload = result.model_dump(mode="json")
    if policy is not None:
        payload, _ = egress.filter_payload(tool, payload, policy)
    print(json.dumps(payload, ensure_ascii=False))


def main(argv=None) -> int:
    """표준 출력은 UTF-8 JSON 하나, argparse 사용 오류는 종료 코드 2로 돌려준다"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    try:
        policy = egress.load_policy(args.policy, get_config(), REPO_ROOT)
    except Exception:  # 정책 오류는 경로·원문을 외부에 내보내지 않는다
        _print_result(EngineResult(status=Status.FAILED, summary="외부 반환 정책을 읽지 못했습니다", scope=Scope(target="")))
        return 1
    try:
        result, full_failed = _run(args)
    except StandardError as e:
        result = RESULTS[args.command](status=Status.FAILED, summary=str(e), scope=Scope(target=args.source))
        full_failed = False
    except Exception as e:  # 실행·검증·저장 오류도 명령별 D2 모델로 돌려준다
        result = RESULTS[args.command](status=Status.FAILED, summary=f"실행 실패: {e}", scope=Scope(target=args.source))
        full_failed = False
    _print_result(result, policy, TOOLS[args.command])
    if result.status is not Status.SUCCESS or full_failed:
        return 1
    if args.command == "check" and args.strict and (sum(result.violation_counts.values()) > 0 or not result.is_complete()):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
