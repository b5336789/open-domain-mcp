from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..context import Context
from ..validation import (
    VALIDATION_FAILED,
    VALIDATION_PASSED,
    VALIDATION_VALIDATING,
    ValidationStore,
    build_run,
    build_scenario,
    summarize_validation,
)
from ..views import VIEW_NAMES
from .auth import ALL_VIEWS, auth_dependency, require_view_access
from .deps import get_ctx
from .simulation import run_simulation

router = APIRouter()


class ScenarioRequest(BaseModel):
    view: str
    name: str
    query: str


class RunRequest(BaseModel):
    view: str
    query: str
    name: str = ""
    top_k: int = 5


def _collection(ctx: Context) -> str:
    stats = ctx.store.stats()
    return str(stats.get("collection") or ctx.settings.collection_name)


def _validate_view(view: str) -> None:
    if view not in VIEW_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown view {view!r}")


def _validate_required(value: str, field: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise HTTPException(status_code=422, detail=f"{field} is required")
    return stripped


def _principal_views(principal: dict) -> set[str] | None:
    views = set(principal.get("views") or ())
    if ALL_VIEWS in views:
        return None
    return {view for view in views if view in VIEW_NAMES}


def _filter_allowed_scenarios(items: list[dict], principal: dict) -> list[dict]:
    allowed = _principal_views(principal)
    if allowed is None:
        return items
    return [item for item in items if item.get("view") in allowed]


def _summary_for_principal(
    store: ValidationStore,
    collection: str,
    view: str | None,
    principal: dict,
) -> dict:
    if view is not None:
        require_view_access(principal, view)
        return summarize_validation(store, collection, view)
    allowed = _principal_views(principal)
    if allowed is None:
        return summarize_validation(store, collection)
    summaries = [
        summarize_validation(store, collection, allowed_view)
        for allowed_view in sorted(allowed)
    ]
    latest_runs = [
        summary["latest_run"] for summary in summaries if summary.get("latest_run")
    ]
    latest = sorted(latest_runs, key=lambda run: run.get("created_at", 0), reverse=True)
    latest_run_count = sum(int(summary.get("latest_run_count") or 0) for summary in summaries)
    passed = sum(int(summary.get("passed") or 0) for summary in summaries)
    failed = sum(int(summary.get("failed") or 0) for summary in summaries)
    if latest_run_count == 0:
        status = VALIDATION_VALIDATING
    elif failed:
        status = VALIDATION_FAILED
    else:
        status = VALIDATION_PASSED
    return {
        "collection": collection,
        "view": None,
        "status": status,
        "scenario_count": sum(int(summary.get("scenario_count") or 0) for summary in summaries),
        "latest_run_count": latest_run_count,
        "passed": passed,
        "failed": failed,
        "pass_rate": (passed / latest_run_count) if latest_run_count else 0.0,
        "latest_run": latest[0] if latest else None,
    }


@router.get("/api/validation/scenarios")
def list_scenarios(
    view: str | None = None,
    ctx: Context = Depends(get_ctx),
    principal: dict = Depends(auth_dependency),
) -> list[dict]:
    if view is not None:
        _validate_view(view)
        require_view_access(principal, view)
    scenarios = ValidationStore(ctx.settings.data_dir).scenarios(_collection(ctx), view)
    return _filter_allowed_scenarios(scenarios, principal)


@router.post("/api/validation/scenarios")
def create_scenario(
    body: ScenarioRequest,
    ctx: Context = Depends(get_ctx),
    principal: dict = Depends(auth_dependency),
) -> dict:
    _validate_view(body.view)
    require_view_access(principal, body.view)
    scenario = build_scenario(
        collection=_collection(ctx),
        view=body.view,
        name=_validate_required(body.name, "name"),
        query=_validate_required(body.query, "query"),
    )
    return ValidationStore(ctx.settings.data_dir).append_scenario(scenario)


@router.post("/api/validation/scenarios/{scenario_id}/run")
def run_scenario(
    scenario_id: str,
    ctx: Context = Depends(get_ctx),
    principal: dict = Depends(auth_dependency),
) -> dict:
    collection = _collection(ctx)
    store = ValidationStore(ctx.settings.data_dir)
    scenario = store.scenario(collection, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"unknown scenario {scenario_id!r}")
    require_view_access(principal, scenario["view"])
    try:
        result = run_simulation(ctx, scenario["view"], scenario["query"])
        run = build_run(collection=collection, scenario=scenario, result=result)
    except Exception as exc:
        run = build_run(collection=collection, scenario=scenario, error=str(exc))
    return store.append_run(run)


@router.post("/api/validation/run")
def run_validation(
    body: RunRequest,
    ctx: Context = Depends(get_ctx),
    principal: dict = Depends(auth_dependency),
) -> dict:
    _validate_view(body.view)
    require_view_access(principal, body.view)
    collection = _collection(ctx)
    store = ValidationStore(ctx.settings.data_dir)
    scenario = build_scenario(
        collection=collection,
        view=body.view,
        name=_validate_required(body.name or body.query, "name"),
        query=_validate_required(body.query, "query"),
    )
    scenario = store.append_scenario(scenario)
    result = run_simulation(ctx, body.view, scenario["query"], body.top_k)
    run = store.append_run(
        build_run(collection=collection, scenario=scenario, result=result)
    )
    return {
        "scenario": scenario,
        "run": run,
        "result": result,
        "summary": summarize_validation(store, collection, body.view),
    }


@router.get("/api/validation/summary")
def validation_summary(
    view: str | None = None,
    ctx: Context = Depends(get_ctx),
    principal: dict = Depends(auth_dependency),
) -> dict:
    if view is not None:
        _validate_view(view)
    store = ValidationStore(ctx.settings.data_dir)
    return _summary_for_principal(store, _collection(ctx), view, principal)
