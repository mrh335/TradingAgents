"""GUI configuration: API keys + run defaults + Ollama model discovery
+ sibling-service integrations (e.g. financial planner URL/key)."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from gui.config import (
    GUI_CONFIG_PATH,
    PROVIDER_KEYS,
    PROVIDER_LABELS,
    load,
    load_integration,
    save,
    set_integration,
)
from service.schemas import ProviderKey, SettingsResponse, SettingsUpdateRequest

router = APIRouter(prefix="/settings", tags=["settings"])


def _ollama_api_base(url: str) -> str:
    """Strip a trailing ``/v1`` if present so we can hit ``/api/tags`` cleanly."""
    url = url.rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url


def _detect_ollama_models(url: str, timeout: float = 5.0) -> List[Dict[str, Any]]:
    """Hit ``<url>/api/tags`` and return the list of installed models.

    Raises ``HTTPException`` on connection or HTTP errors with a message
    the UI can render.
    """
    base = _ollama_api_base(url)
    try:
        resp = requests.get(f"{base}/api/tags", timeout=timeout)
    except requests.RequestException as e:
        raise HTTPException(
            status_code=502,
            detail=f"could not reach Ollama at {base}: {e}",
        )
    if not resp.ok:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama returned {resp.status_code}: {resp.text[:200]}",
        )
    try:
        data = resp.json()
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Ollama returned non-JSON; is the URL pointing at Ollama?",
        )
    out: List[Dict[str, Any]] = []
    for m in (data.get("models") or []):
        out.append({
            "name": m.get("name") or m.get("model") or "",
            "size": m.get("size"),
            "modified_at": m.get("modified_at"),
            "parameter_size": (m.get("details") or {}).get("parameter_size"),
            "family": (m.get("details") or {}).get("family"),
        })
    return out


def _provider_keys_view(cfg_keys: Dict[str, str]) -> list[ProviderKey]:
    out = []
    for provider, env_name in PROVIDER_KEYS.items():
        out.append(
            ProviderKey(
                provider=provider,
                env_name=env_name,
                label=PROVIDER_LABELS.get(provider, provider),
                set_in_env=bool(os.environ.get(env_name)),
                set_in_config=bool(cfg_keys.get(env_name)),
            )
        )
    return out


@router.get("", response_model=SettingsResponse)
def get_settings() -> SettingsResponse:
    cfg = load()
    return SettingsResponse(
        api_keys=_provider_keys_view(cfg.get("api_keys", {})),
        defaults=cfg.get("defaults", {}),
        config_path=str(GUI_CONFIG_PATH),
    )


@router.get("/ollama/models")
def ollama_models(url: str | None = None) -> Dict[str, Any]:
    """List the models installed on the configured (or supplied) Ollama instance.

    If ``url`` is provided, test that URL — otherwise read ``defaults.ollama_base_url``
    from the saved config.
    """
    cfg = load()
    target = url or (cfg.get("defaults", {}) or {}).get("ollama_base_url") or ""
    if not target:
        raise HTTPException(
            status_code=400,
            detail="no Ollama URL configured. Set ollama_base_url in Settings or pass ?url=...",
        )
    models = _detect_ollama_models(target)
    return {"url": target, "models": models, "count": len(models)}


@router.put("", response_model=SettingsResponse)
def update_settings(req: SettingsUpdateRequest) -> SettingsResponse:
    cfg = load()
    if req.api_keys is not None:
        cfg.setdefault("api_keys", {})
        for env_name, value in req.api_keys.items():
            if value:
                cfg["api_keys"][env_name] = value
            elif env_name in cfg["api_keys"]:
                del cfg["api_keys"][env_name]
    if req.defaults is not None:
        cfg.setdefault("defaults", {}).update(req.defaults)
    save(cfg)
    return SettingsResponse(
        api_keys=_provider_keys_view(cfg.get("api_keys", {})),
        defaults=cfg.get("defaults", {}),
        config_path=str(GUI_CONFIG_PATH),
    )


# ── Sibling integrations (financial planner etc.) ───────────────────────
#
# Stored URL/key per named integration. Env vars (PLANNER_API_URL,
# PLANNER_API_KEY) still take precedence at request time — these endpoints
# only manage the GUI-config fallback. ``set_in_env`` in the response
# tells the UI to display the field as read-only when the env var wins.

_PLANNER_URL_ENV = "PLANNER_API_URL"
_PLANNER_KEY_ENV = "PLANNER_API_KEY"


def _mask_key(value: str) -> str:
    """Show only the last 4 chars so the user can verify which key is
    stored without re-reading the full secret on every page load."""
    if not value:
        return ""
    tail = value[-4:] if len(value) >= 4 else value
    return f"…{tail}"


class IntegrationView(BaseModel):
    name: str
    url: str
    masked_key: str
    url_set_in_env: bool
    key_set_in_env: bool


class IntegrationUpdateRequest(BaseModel):
    url: Optional[str] = None
    key: Optional[str] = None


class PlannerProbeResult(BaseModel):
    ok: bool
    status_code: Optional[int] = None
    error: Optional[str] = None


@router.get("/integrations/planner", response_model=IntegrationView)
def get_planner_integration() -> IntegrationView:
    """Returns the planner integration config the GUI uses for its
    Settings page. The full key is never returned — only a masked tail
    so the user can verify what's stored."""
    stored = load_integration("planner")
    return IntegrationView(
        name="planner",
        url=stored["url"],
        masked_key=_mask_key(stored["key"]),
        url_set_in_env=bool(os.environ.get(_PLANNER_URL_ENV)),
        key_set_in_env=bool(os.environ.get(_PLANNER_KEY_ENV)),
    )


@router.put("/integrations/planner", response_model=IntegrationView)
def update_planner_integration(req: IntegrationUpdateRequest) -> IntegrationView:
    """Update the planner URL and/or key in the GUI config. None values
    leave the existing field as-is; empty strings explicitly clear."""
    set_integration("planner", url=req.url, key=req.key)
    return get_planner_integration()


@router.post("/integrations/planner/test", response_model=PlannerProbeResult)
def test_planner_integration() -> PlannerProbeResult:
    """Hit /api/health on the configured planner with the stored auth
    header to confirm URL + key are correct. Doesn't sync anything —
    just a connectivity probe so the user gets immediate feedback after
    saving the config."""
    from service import planner_client
    if not planner_client.is_configured():
        return PlannerProbeResult(
            ok=False,
            error="No URL or key configured. Set both in the form above.",
        )
    result = planner_client.healthcheck()
    return PlannerProbeResult(
        ok=bool(result.get("ok")),
        status_code=result.get("status_code"),
        error=result.get("error") or (result.get("body") if not result.get("ok") else None),
    )
