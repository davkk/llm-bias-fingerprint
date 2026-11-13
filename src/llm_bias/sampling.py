import asyncio
import json
import urllib.error
import urllib.request

from llm_bias import config


class SamplingError(RuntimeError):
    """the model server is unreachable or answered with something unusable"""


def make_request_sync(method: str, endpoint: str, payload: dict | None = None):
    assert config.API_URL is not None
    req = urllib.request.Request(
        config.API_URL + endpoint,
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload).encode("utf-8") if payload else None,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise SamplingError(f"{method} {endpoint}: HTTP {e.code}: {body}") from e
    except Exception as e:
        raise SamplingError(f"{method} {endpoint} failed: {e}") from e


async def make_request(method: str, endpoint: str, payload: dict | None = None):
    return await asyncio.to_thread(make_request_sync, method, endpoint, payload)


async def send_prompt(model: str, prompt: str, params: dict | None = None):
    params = params or {}
    messages = [
        {
            "role": "system",
            "content": params.get("system_prompt", config.SYSTEM_PROMPT),
        },
        {"role": "user", "content": prompt},
    ]
    payload = {
        "model": model,
        "messages": messages,
        "temperature": params.get("temperature", 1.0),
        "top_p": params.get("top_p", 1.0),
        "max_tokens": params.get("max_tokens", 16),
        "chat_template_kwargs": {
            "enable_thinking": params.get("enable_thinking", False)
            or params.get("reasoning_effort") is not None,
        },
    }
    if params.get("seed") is not None:
        payload["seed"] = params["seed"]
    if "reasoning_effort" in params and params.get("reasoning_effort") is not None:
        payload["reasoning_effort"] = params["reasoning_effort"]
    resp = await make_request("POST", "/v1/chat/completions", payload)
    try:
        return resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise SamplingError(f"malformed completion response: {resp!r}") from e
