import asyncio
import logging
import time

from llm_bias.sampling import make_request

logger = logging.getLogger(__name__)


async def is_model_loaded(name: str):
    resp = await make_request("GET", "/models")
    models = resp.get("data", [])
    if not models:
        raise RuntimeError("No models found")
    cur = next((m for m in models if m["id"] == name), None)
    if not cur:
        raise RuntimeError(f'Model "{name}" not found')
    return cur.get("status", {}).get("value", "unloaded") == "loaded"


async def wait_for_model(name: str, every: int = 1, timeout: int = 30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if await is_model_loaded(name):
            return
        await asyncio.sleep(every)
    raise TimeoutError(f"Model {name} not loaded after {timeout} seconds")


async def load_model(name: str):
    for _ in range(10):
        resp = await make_request("POST", "/models/load", {"model": name})
        if resp.get("success"):
            await wait_for_model(name, timeout=120)
            return
        # a concurrent load in flight makes the router refuse; back off and retry
        logger.warning("model %s load refused, retrying", name)
        await asyncio.sleep(3)
    raise RuntimeError(f"could not load model {name}")


async def unload_model(name: str):
    resp = await make_request("POST", "/models/unload", {"model": name})
    if not resp.get("success"):
        raise RuntimeError(f"could not unload model {name}: {resp}")
