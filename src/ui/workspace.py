"""작업 폴더의 명세·DXF 탐색과 파일 변경 지문."""
import hashlib
import json
from pathlib import Path, PureWindowsPath
from threading import RLock

from src.engine.spec import SpecError, parse_spec


class Workspace:
    def __init__(self, root, base=None):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("작업 경로는 폴더여야 합니다")
        self._hashes = {}
        self._hash_lock = RLock()
        self.base = Path(base).resolve(strict=True) if base else None
        if self.base and self.base.suffix.lower() != ".dxf":
            raise ValueError("기준 도면은 DXF여야 합니다")

    def resolve(self, name, suffix):
        # 명시적으로 지정한 외부 기준 도면만 불투명한 별칭으로 허용한다.
        if name == "@base.dxf" and self.base and suffix == ".dxf":
            return self.base
        normalized = name.replace(chr(92), "/")
        relative = Path(normalized)
        if (not name or name == "@base.dxf" or relative.is_absolute()
                or PureWindowsPath(name).drive or ".." in relative.parts):
            raise PermissionError("작업 폴더 안의 상대 경로만 읽을 수 있습니다")
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise PermissionError("작업 폴더 밖의 파일은 읽을 수 없습니다")
        if path.suffix.lower() != suffix:
            raise ValueError(f"{suffix} 파일을 선택해 주세요")
        if not path.is_file():
            raise FileNotFoundError("선택한 파일이 없습니다")
        return path

    def fingerprint(self, path):
        # SSE 연결과 목록 조회가 겹쳐도 완성된 캐시만 반환한다.
        with self._hash_lock:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            previous = self._hashes.get(path)
            if previous and previous[0] == signature:
                return dict(previous[1])
            content = path.read_bytes()
            result = {'mtime_ns': stat.st_mtime_ns, 'size': stat.st_size,
                      'sha256': hashlib.sha256(content).hexdigest()}
            self._hashes[path] = (signature, result)
            return dict(result)

    def files(self, ordered=True):
        folders = [self.root] + sorted(p for p in self.root.iterdir()
                                      if p.is_dir() and p.resolve().is_relative_to(self.root))
        found = []
        for folder in folders:
            paths = [p for p in folder.iterdir() if p.is_file()
                     and p.suffix.lower() in (".json", ".dxf")
                     and p.resolve().is_relative_to(self.root)]
            order = {}
            try:
                entries = json.loads((folder / "hashes.json").read_text(encoding="utf-8-sig")) if ordered else []
                if isinstance(entries, list):
                    order = {row["op"] + ".json": i for i, row in enumerate(entries)
                             if isinstance(row, dict) and isinstance(row.get("op"), str)}
            except (OSError, ValueError):
                pass
            found.extend(sorted(paths, key=lambda p: (order.get(p.name, len(order)), p.name)))
        return found

    def snapshot(self):
        result = {}
        for path in self.files(ordered=False):
            try:
                result[path.relative_to(self.root).as_posix()] = self.fingerprint(path)
            except OSError:
                continue  # 저장 중 사라진 파일은 다음 폴링에서 다시 확인한다.
        if self.base:
            try:
                result["@base.dxf"] = self.fingerprint(self.base)
            except OSError:
                pass
        return result

    def discover(self):
        specs, bases = [], []
        for path in self.files():
            if (path.name in ("hashes.json", "bind.json")
                    or path.relative_to(self.root).as_posix().lower() == "review/notes.json"):
                continue
            try:
                row = {"path": path.relative_to(self.root).as_posix(), **self.fingerprint(path)}
                if path.suffix.lower() == ".dxf":
                    bases.append(row)
                    continue
                text = path.read_text(encoding="utf-8-sig")
                try:
                    raw = json.loads(text)
                    if not isinstance(raw, dict) or not any(k in raw for k in ("items", "schema_version", "op")):
                        continue
                except ValueError:
                    pass
                try:
                    spec = parse_spec(text)
                    row.update(op=spec.op, error=None)
                except SpecError as error:
                    row.update(op=None, error="; ".join(v.message for v in error.violations))
                specs.append(row)
            except (OSError, UnicodeError):
                continue
        if self.base:
            bases = [{"path": "@base.dxf", "name": self.base.name, **self.fingerprint(self.base)}]
        return {"specs": specs, "bases": bases}
