from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from doggo.hardware.sts3215 import ServoBusError
from doggo.models import ServoAssignmentRequest, ServoMoveRequest, TeleopCommand
from doggo.runtime import DoggoRuntime


def create_app(config_path: str | Path = "config/doggo.local.yaml") -> FastAPI:
    runtime = DoggoRuntime(config_path)
    static_dir = Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.start()
        app.state.runtime = runtime
        yield
        await runtime.stop()

    app = FastAPI(title="Doggo Control", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    def get_runtime() -> DoggoRuntime:
        return app.state.runtime

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    async def health() -> dict:
        return get_runtime().supervisor.status_snapshot()

    @app.get("/api/config")
    async def config() -> dict:
        return get_runtime().config.model_dump(mode="json")

    @app.get("/api/servos/scan")
    async def scan_servos(
        start_id: int | None = Query(default=None),
        end_id: int | None = Query(default=None),
    ) -> dict:
        try:
            results = await get_runtime().supervisor.scan_servos(start_id=start_id, end_id=end_id)
        except ServoBusError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"found": [result.to_dict() for result in results]}

    @app.get("/api/servos/{servo_id}/position")
    async def read_position(servo_id: int) -> dict:
        try:
            position = await get_runtime().supervisor.read_position(servo_id)
        except ServoBusError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"servo_id": servo_id, "position": position}

    @app.post("/api/servos/{servo_id}/move")
    async def move_servo(servo_id: int, request: ServoMoveRequest) -> dict:
        try:
            await get_runtime().supervisor.move_servo(
                servo_id,
                request.position,
                speed=request.speed,
                acceleration=request.acceleration,
            )
        except ServoBusError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return get_runtime().supervisor.status_snapshot()

    @app.post("/api/servos/assign-id")
    async def assign_servo_id(request: ServoAssignmentRequest) -> dict:
        try:
            await get_runtime().supervisor.assign_servo_id(request.current_id, request.new_id)
        except ServoBusError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return get_runtime().supervisor.status_snapshot()

    @app.post("/api/pose/stand")
    async def stand() -> dict:
        try:
            await get_runtime().supervisor.stand()
        except ServoBusError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return get_runtime().supervisor.status_snapshot()

    @app.post("/api/pose/relax")
    async def relax() -> dict:
        try:
            await get_runtime().supervisor.relax()
        except ServoBusError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return get_runtime().supervisor.status_snapshot()

    @app.post("/api/control/teleop")
    async def control(command: TeleopCommand) -> dict:
        return await get_runtime().supervisor.apply_teleop(command)

    @app.websocket("/ws/control")
    async def control_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json(get_runtime().supervisor.status_snapshot())
        try:
            while True:
                payload = await websocket.receive_json()
                command = TeleopCommand.model_validate(payload)
                status = await get_runtime().supervisor.apply_teleop(command)
                await websocket.send_json(status)
        except WebSocketDisconnect:
            return

    return app
