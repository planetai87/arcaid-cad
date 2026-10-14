"""E8 보조: LISP open 실패 원인 분리(한글 경로 / 공백 경로 / 한글 내용)"""
from _harness import WORK, Spike

cases = {"ascii_path_korean_text": (WORK / "lisp_ascii.txt", "한글 쓰기"),
         "space_path_ascii_text": (WORK / "space dir" / "lisp.txt", "ascii"),
         "korean_path_ascii_text": (WORK / "한글폴더" / "lisp.txt", "ascii"),
         "korean_space_dir_ascii": (WORK / "한글 경로" / "lisp.txt", "ascii"),
         "korean_space_dir_and_name": (WORK / "한글 경로" / "lisp 쓰기.txt", "ascii"),
         "korean_name_nospace": (WORK / "한글폴더" / "쓰기.txt", "ascii")}
with Spike("E8_lisp") as s:
    s.copy_and_open(WORK / "synthetic_base.dwg", "e8_lisp.dwg")
    for label, (p, text) in cases.items():
        p.parent.mkdir(exist_ok=True)
        if p.exists():
            p.unlink()
        lp = str(p).replace("\\", "/")
        try:
            v = s.lisp(f'(progn (setq f (open "{lp}" "w")) (if f (progn (write-line "{text}" f) (close f) "opened") "nil-open"))')
        except Exception as e:  # noqa: BLE001
            v = repr(e)[:150]
        raw = p.read_bytes() if p.exists() else None
        s.record(label, {"lisp": v, "exists": p.exists(),
                         "utf8": raw.decode("utf-8", "replace").strip() if raw else None,
                         "cp949": raw.decode("cp949", "replace").strip() if raw else None})
