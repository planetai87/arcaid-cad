"""Claude Code가 화면 수정 기록을 읽는 명령줄."""
import argparse
from datetime import datetime
import json
import sys
from .edit import EditStore
from .workspace import Workspace


def main(argv=None):
    parser = argparse.ArgumentParser(description='검토 화면 수정 기록 조회')
    parser.add_argument('--work', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    log = commands.add_parser('log', help='수정 기록 목록')
    log.add_argument('--since', help='이 ISO 시각 이후의 기록')
    log.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        store = EditStore(Workspace(args.work))
        path = store.inside(store.root / 'review/edits.jsonl')
        rows = [json.loads(line) for line in path.read_text(encoding='utf8').splitlines() if line.strip()] if path.exists() else []
        if args.since:
            since = datetime.fromisoformat(args.since)
            if since.tzinfo is None:
                since = since.astimezone()
            rows = [row for row in rows if datetime.fromisoformat(row['time']) >= since]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for row in rows:
                print(f"{row['time']} {row['file']} · {row['summary']}")
        return 0
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
