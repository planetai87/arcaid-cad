"""외부 반환 필터(구조 결정 D8): MCP 결과가 AI로 나가기 전에 정책(config/egress.yaml)대로 거른다

- 키 이름으로 항목(path·text·block_attribute·sheet)을 정하고, 정책이 deny면 값을 같은 타입의 "[비공개]"로 바꾼다.
  키는 지우지 않는다(저수준 MCP 서버가 결과를 outputSchema로 검증하므로).
- 정책에 없는 문자열 값은 unclassified 정책을 따른다. 묶음(dict·list)은 안으로 들어가 본다.
- 가린 값이 문장(summary·message·reason·error·warnings·details)에 들어 있으면 거기서도 가린다.
  path가 deny면 문장 안의 Windows·POSIX 경로도 가린다.
- run_lisp 결과(value)는 lisp_value 정책: allow / scrub_paths(경로만 가림) / deny.
- 2026-10-14 결정은 A안(제한 없음)이라 기본 정책으로는 run_lisp 경로만 가린다.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import yaml

REDACTED = "[비공개]"
PATH_MARK = "[경로]"
FREE_TEXT = {"summary", "message", "reason", "error", "warnings", "details"}
CATEGORIES = ("path", "text", "block_attribute", "sheet")
DECISIONS = ("allow", "deny", "scrub_paths")
# 드라이브·UNC·POSIX 절대 경로. 축척(1/100)·URL은 제외하고 공백이 든 경로도 가린다.
_PATH = re.compile(r"(?:(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\[^\\\s]+\\|(?<![\w/\\:])/(?=[^\s/]))[^\"'\r\n<>|*?,;()\[\]{}]+")
_MIN_SECRET = 3


class EgressPolicy:
    def __init__(self, policy: Dict[str, str], fields: Dict[str, List[str]], always: Iterable[str]):
        if not isinstance(policy, dict) or not isinstance(fields, dict):
            raise ValueError("egress policy·fields는 객체여야 함")
        if not isinstance(always, (list, tuple, set)) or any(not isinstance(v, str) for v in always):
            raise ValueError("egress always는 문자열 목록이어야 함")
        if any(not isinstance(v, list) or any(not isinstance(n, str) for n in v) for v in fields.values()):
            raise ValueError("egress fields의 값은 문자열 목록이어야 함")
        for key, value in policy.items():
            if value not in DECISIONS:
                raise ValueError(f"egress 정책 값이 틀림: {key}={value}(가능: {', '.join(DECISIONS)})")
        self.policy = dict(policy)
        self.fields = {k: list(v or []) for k, v in fields.items()}
        self.always: Set[str] = set(always)
        self._lookup = {name: cat for cat, names in self.fields.items() for name in names}

    @classmethod
    def load(cls, path) -> "EgressPolicy":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError("egress 정책은 객체여야 함")
        return cls(data.get("policy", {}), data.get("fields", {}), data.get("always", []))

    def with_overrides(self, policy: Dict[str, str]) -> "EgressPolicy":
        return EgressPolicy({**self.policy, **policy}, self.fields, self.always)

    def decide(self, category: str) -> str:
        return self.policy.get(category, "deny")   # 정해지지 않은 항목은 보내지 않는다(D8 규칙 5)

    @property
    def unclassified(self) -> str:
        return self.policy.get("unclassified", "deny")

    def category(self, key: str, parent: Optional[str]) -> Optional[str]:
        """키의 항목. 'always'면 항상 보냄, None이면 분류되지 않음"""
        if parent and f"{parent}.{key}" in self._lookup:
            return self._lookup[f"{parent}.{key}"]
        if parent and f"{parent}.{key}" in self.always:
            return "always"
        if key in self._lookup:
            return self._lookup[key]
        if key in self.always:
            return "always"
        return None

    @property
    def denies_paths(self) -> bool:
        return self.decide("path") == "deny"


class _Ctx:
    def __init__(self):
        self.secrets: Set[str] = set()
        self.changed = False


def _redact(value: Any, ctx: _Ctx) -> Any:
    if isinstance(value, str):
        if value and value != REDACTED:
            if len(value) >= _MIN_SECRET:
                ctx.secrets.add(value)
            ctx.changed = True
            return REDACTED
        return value
    if isinstance(value, list):
        return [_redact(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: _redact(v, ctx) for k, v in value.items()}
    return value


def _walk(node: Any, parent: Optional[str], policy: EgressPolicy, ctx: _Ctx) -> Any:
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            category = policy.category(key, parent)
            if category == "always":
                out[key] = _walk(value, key, policy, ctx)
            elif category is not None:
                out[key] = _redact(value, ctx) if policy.decide(category) == "deny" else _walk(value, key, policy, ctx)
            elif isinstance(value, (dict, list)):
                out[key] = _walk(value, key, policy, ctx)
            elif isinstance(value, str) and policy.unclassified == "deny":
                out[key] = _redact(value, ctx)
            else:
                out[key] = value
        return out
    if isinstance(node, list):
        return [_walk(v, parent, policy, ctx) for v in node]
    return node


def load_policy(path: Optional[str], config: dict, repo_root: Path) -> EgressPolicy:
    """인자(cwd 기준) → 환경 변수 → 설정 → 기본 순서로 읽는다. 나머지는 저장소 기준이다"""
    if path is not None:
        return EgressPolicy.load(Path(path))
    selected = Path(os.environ.get("ARCAID_EGRESS_FILE") or config.get("egress", {}).get("file") or "config/egress.yaml")
    return EgressPolicy.load(selected if selected.is_absolute() else repo_root / selected)


def scrub_text(text: str, secrets: Iterable[str], paths: bool) -> str:
    """문장 안의 가린 값(긴 것부터)과, paths면 Windows·POSIX 경로를 지운다"""
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    if paths:
        text = _PATH.sub(PATH_MARK, text)
    return text


def _scrub_free_text(node: Any, key: Optional[str], ctx: _Ctx, paths: bool) -> Any:
    if isinstance(node, dict):
        return {k: _scrub_free_text(v, k, ctx, paths) for k, v in node.items()}
    if isinstance(node, list):
        return [_scrub_free_text(v, key, ctx, paths) for v in node]
    if isinstance(node, str) and key in FREE_TEXT:
        cleaned = scrub_text(node, ctx.secrets, paths)
        if cleaned != node:
            ctx.changed = True
        return cleaned
    return node


def filter_payload(tool: str, payload: Dict[str, Any], policy: EgressPolicy) -> Tuple[Dict[str, Any], bool]:
    """구조화 결과를 정책대로 거른다. (거른 결과, 하나라도 바꿨는지)"""
    ctx = _Ctx()
    out = _walk(payload, None, policy, ctx)
    if ctx.secrets or policy.denies_paths:
        out = _scrub_free_text(out, None, ctx, policy.denies_paths)
    if tool == "run_lisp" and isinstance(out.get("value"), str):
        decision = policy.decide("lisp_value")
        if decision == "deny":
            out["value"] = _redact(out["value"], ctx)
        elif decision == "scrub_paths":
            cleaned = _PATH.sub(PATH_MARK, out["value"])
            if cleaned != out["value"]:
                out["value"], ctx.changed = cleaned, True
    return out, ctx.changed


def save_full(work: Path, tool: str, payload: Any) -> Path:
    """가린 결과의 원본을 PC 작업 폴더에 남긴다(<work>/egress/<날짜>/<시각>-<툴>.json)"""
    now = datetime.now()
    folder = Path(work) / "egress" / now.strftime("%Y%m%d")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{now.strftime('%H%M%S-%f')}-{tool}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return path
