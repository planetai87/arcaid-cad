"""python -m src.ui --work 폴더로 로컬 검토 화면을 연다."""
import argparse
import threading
import webbrowser

import uvicorn

from .server import create_app


def main(argv=None):
    parser = argparse.ArgumentParser(description="명세·DXF 읽기 전용 검토 화면(CAD 연결 없음)")
    parser.add_argument("--work", required=True, help="작업 명세 폴더")
    parser.add_argument("--base", help="기준 DXF 파일")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--no-open", action="store_true", help="브라우저를 자동으로 열지 않음")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("포트는 1~65535 사이여야 합니다")
    try:
        app = create_app(args.work, args.base)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    address = f"http://127.0.0.1:{args.port}"
    print(f"검토 화면: {address}", flush=True)
    if not args.no_open:
        timer = threading.Timer(0.8, webbrowser.open, args=(address,))
        timer.daemon = True
        timer.start()
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
