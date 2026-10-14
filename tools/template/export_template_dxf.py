"""회사 템플릿 DWG를 R2013 DXF(standards/heo-2026/template.dxf)로 만들고 두 파일의 해시를 profile.yaml에 적는다.

엔진은 CAD 없이 이 template.dxf로 표준 정의를 읽고 비교한다. 템플릿 DWG를 고치면 반드시 다시 실행한다
(그러지 않으면 tests/engine의 해시 테스트가 실패한다).

사용법:
  python export_template_dxf.py           GstarCAD로 변환하고 해시를 고쳐 쓴다(작업 폴더의 사본에서 변환)
  python export_template_dxf.py --check   CAD 없이 해시만 비교한다. 다르면 종료 코드 1
"""
import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROFILE_DIR = REPO / "standards" / "heo-2026"
PROFILE = PROFILE_DIR / "profile.yaml"

parser = argparse.ArgumentParser(description="템플릿 DWG → template.dxf(R2013), profile.yaml 해시 갱신")
parser.add_argument("--check", action="store_true", help="CAD 없이 해시만 비교한다")
parser.add_argument("--work", default=r"C:\arcaid\work\t2", help="작업 폴더(한글·공백 없는 경로 권장)")
args = parser.parse_args()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def template_paths():
    text = PROFILE.read_text(encoding="utf-8")
    dwg = re.search(r"^\s*dwg:\s*(.+?)\s*$", text, re.M).group(1)
    dxf = re.search(r"^\s*dxf:\s*(.+?)\s*$", text, re.M).group(1)
    return (PROFILE_DIR / dwg).resolve(), (PROFILE_DIR / dxf).resolve(), text


def recorded(text: str, key: str) -> str:
    return re.search(rf"^\s*{key}:\s*(\S+)\s*$", text, re.M).group(1).strip('"')


def check() -> int:
    dwg, dxf, text = template_paths()
    bad = []
    for key, path in (("dwg_sha256", dwg), ("dxf_sha256", dxf)):
        actual = sha256(path) if path.exists() else "(파일 없음)"
        if actual != recorded(text, key):
            bad.append(f"{key}: 기록 {recorded(text, key)} / 실제 {actual}")
    for line in bad:
        print("불일치", line)
    print("해시 일치" if not bad else "템플릿 DWG를 고쳤으면 이 도구를 CAD로 다시 실행하세요")
    return 1 if bad else 0


def export() -> None:
    from _cadutil import close_work_docs, connect, restore_user

    dwg, dxf, text = template_paths()
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    src, out = work / "template_export.dwg", work / "template_export.dxf"
    ctl, user = connect()
    close_work_docs(ctl, work)
    for p in (src, out):
        if p.exists():
            p.unlink()
    shutil.copyfile(dwg, src)
    try:
        if not ctl.open_drawing(str(src)):
            raise RuntimeError(f"열기 실패: {src}")
        doc = ctl.doc
        ctl._call_attr(doc, "SaveAs", str(out), 61)  # 61 = R2013 DXF(버릴 사본이라 문서 이름이 바뀌어도 된다)
        ctl._call_attr(doc, "Close", False)
    finally:
        close_work_docs(ctl, work)
        restore_user(ctl, user)
    shutil.copyfile(out, dxf)
    for key, path in (("dwg_sha256", dwg), ("dxf_sha256", dxf)):
        # 따옴표로 감싼다: 숫자로만 된 해시를 YAML이 정수로 읽지 않게
        text = re.sub(rf"^(\s*{key}:\s*)\S+(\s*)$", rf'\g<1>"{sha256(path)}"\g<2>', text, flags=re.M)
    PROFILE.write_text(text, encoding="utf-8", newline="\n")
    print("만듦:", dxf, "| DWG", sha256(dwg)[:12], "| DXF", sha256(dxf)[:12])


if __name__ == "__main__":
    if args.check:
        sys.exit(check())
    export()
