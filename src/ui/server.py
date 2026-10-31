"""루프백에서만 실행하는 검토 API와 정적 화면."""
import asyncio
import json
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.engine.spec import SpecError
from .specs import read_spec
from .edit import EditStore, EditConflict
from .base import read_base
from .workspace import Workspace
from .notes import NotesStore, RevisionConflict

DIST = Path(__file__).resolve().parents[2] / "ui/web/dist"


async def change_events(workspace, interval=0.5):
    before = await run_in_threadpool(workspace.snapshot)
    yield ": connected\n\n"
    while True:
        await asyncio.sleep(interval)
        after = await run_in_threadpool(workspace.snapshot)
        for path in sorted(before.keys() | after.keys()):
            if before.get(path) != after.get(path):
                payload = {"type": "changed", "path": path, "sha256": after.get(path, {}).get("sha256")}
                yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
        before = after


def create_app(work, base=None, dist=None):
    workspace = Workspace(work, base)
    notes_store = NotesStore(workspace.root)
    edit_store = EditStore(workspace)
    dist = Path(dist) if dist is not None else DIST

    def resolve(request, suffix):
        try:
            return workspace.resolve(request.query_params.get("path", ""), suffix)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from None
        except FileNotFoundError as error:
            raise HTTPException(404, str(error)) from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    async def notes(request):
        try:
            if request.method == 'GET':
                data = await run_in_threadpool(notes_store.read)
            else:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError('요청은 JSON 객체여야 합니다')
                note_id = request.path_params.get('id')
                allowed = {'rev', 'status', 'text', 'reply'} if note_id else {'rev', 'text', 'target'}
                if set(body) - allowed or 'rev' not in body:
                    raise ValueError('요청 필드와 rev를 확인해 주세요')
                if note_id:
                    if any(body[key] is None for key in body):
                        raise ValueError('변경 값은 null일 수 없습니다')
                    data = await run_in_threadpool(notes_store.update, body['rev'], note_id,
                                                   **{k: v for k, v in body.items() if k != 'rev'})
                else:
                    data = await run_in_threadpool(notes_store.create, body['rev'], body.get('text'), body.get('target'))
            return JSONResponse(data)
        except RevisionConflict as error:
            return JSONResponse(error.current, status_code=409)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from None
        except FileNotFoundError as error:
            raise HTTPException(404, str(error)) from None
        except (ValueError, UnicodeError) as error:
            raise HTTPException(400, str(error)) from None
        except OSError as error:
            raise HTTPException(503, f'메모 저장소 접근 실패: {error}') from None

    async def inventory(request):
        return JSONResponse(await run_in_threadpool(workspace.discover))

    async def spec(request):
        return JSONResponse(await run_in_threadpool(read_spec, resolve(request, ".json")))

    async def edit_spec(request):
        try:
            body = await request.json()
            if not isinstance(body, dict) or not isinstance(body.get("path"), str):
                raise ValueError("명세 경로가 필요합니다")
            undo = request.url.path.endswith("/undo")
            allowed = {"path", "sha256"} if undo else {"path", "sha256", "ops"}
            if set(body) != allowed:
                raise ValueError("요청 필드를 확인해 주세요")
            data = await run_in_threadpool(edit_store.save, body["path"], body["sha256"],
                                           None if undo else body["ops"])
            return JSONResponse(data)
        except EditConflict as error:
            return JSONResponse(error.current, status_code=409)
        except SpecError as error:
            return JSONResponse({"violations": [v.model_dump(mode="json") for v in error.violations]}, status_code=422)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from None
        except FileNotFoundError as error:
            raise HTTPException(404, str(error)) from None
        except (ValueError, UnicodeError) as error:
            raise HTTPException(400, str(error)) from None
        except OSError as error:
            raise HTTPException(503, f"명세 저장소 접근 실패: {error}") from None

    async def base_dxf(request):
        path = resolve(request, ".dxf")
        try:
            result = await run_in_threadpool(read_base, path)
        except (OSError, ValueError) as error:
            raise HTTPException(422, f"DXF 읽기 실패: {error}") from None
        return JSONResponse(result)

    async def events(request):
        return StreamingResponse(change_events(workspace), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def index(request):
        if (dist / "index.html").is_file():
            return FileResponse(dist / "index.html")
        return HTMLResponse('<!doctype html><html lang="ko"><meta charset="utf-8">'
                            '<title>arcaid 검토 화면</title><h1>화면 빌드가 필요합니다</h1>'
                            '<p>저장소의 ui/web 폴더에서 <code>npm install</code> 후 '
                            '<code>npm run build</code>를 실행하고 새로 고침하세요.</p></html>')

    routes = [Route("/api/spec/edit", edit_spec, methods=["POST"]),
              Route("/api/spec/undo", edit_spec, methods=["POST"]),
              Route("/api/notes", notes, methods=["GET", "POST"]),
              Route("/api/notes/{id}", notes, methods=["POST"]),
              Route("/api/workspace", inventory), Route("/api/spec", spec),
              Route("/api/base", base_dxf), Route("/api/events", events), Route("/", index)]
    if dist.is_dir():
        routes.append(Mount("/", StaticFiles(directory=dist), name="static"))
    app = Starlette(routes=routes)
    app.state.workspace = workspace
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    return app
