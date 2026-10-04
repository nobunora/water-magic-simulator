"""FastAPI application wiring for the local application skeleton."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from floodsim import __version__
from floodsim.api.errors import ApiContractError
from floodsim.api.routes_config import router as config_router
from floodsim.api.routes_geocode import router as geocode_router
from floodsim.api.routes_health import router as health_router
from floodsim.api.routes_rainfall import router as rainfall_router
from floodsim.api.routes_results import router as results_router
from floodsim.api.routes_runs import router as runs_router

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

app = FastAPI(
    title="Urban Pluvial Flood Simulator",
    version=__version__,
)


@app.exception_handler(ApiContractError)
async def api_contract_error_handler(_: Request, exc: ApiContractError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "stage": exc.stage,
                "retryable": exc.retryable,
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(request: Request, exc: RequestValidationError):
    if not request.url.path.startswith("/api/v1"):
        return await request_validation_exception_handler(request, exc)
    message = "入力値を確認してください。"
    if isinstance(exc.body, dict) and exc.body.get("water_magic") is not None:
        message = "; ".join(error["msg"].removeprefix("Value error, ") for error in exc.errors())
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": "INPUT_VALIDATION_ERROR",
                "message": message,
                "stage": None,
                "retryable": False,
            }
        },
    )


app.include_router(health_router, prefix="/api/v1", tags=["health"])
app.include_router(config_router, prefix="/api/v1", tags=["config"])
app.include_router(geocode_router, prefix="/api/v1", tags=["geocode"])
app.include_router(rainfall_router, prefix="/api/v1", tags=["rainfall"])
app.include_router(runs_router, prefix="/api/v1", tags=["runs"])
app.include_router(results_router, prefix="/api/v1", tags=["results"])
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="spa")
