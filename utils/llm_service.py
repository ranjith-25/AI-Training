import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# Single model id used everywhere in this project, so the generation half of
# the evaluation is not silently comparing different models across scripts.
#
# gemini-2.5-flash (what this repo used to call) now returns 404 for this
# account: "no longer available to new users ... use models/gemini-3.6-flash".
GEN_MODEL = "gemini-3.6-flash"

# Without an explicit timeout the SDK client can hang indefinitely on a stalled
# connection, which silently wedges an evaluation run.
_HTTP_TIMEOUT_MS = 120_000


_client_singleton: genai.Client | None = None


def _client() -> genai.Client:
    """
    Lazily build and CACHE the client.

    Lazy because reading API_KEY at import time would capture the value before
    load_dotenv() has populated the environment. Cached because a client built
    per call is unreferenced as soon as the expression is evaluated — it gets
    garbage-collected mid-request and its httpx pool closes underneath the call,
    raising "Cannot send a request, as the client has been closed."
    """
    global _client_singleton
    if _client_singleton is None:
        api_key = os.getenv("API_KEY")
        if not api_key:
            raise RuntimeError("API_KEY not set in environment")
        _client_singleton = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=_HTTP_TIMEOUT_MS),
        )
    return _client_singleton


def generate_with_gemini_sync(prompt: str, model_name: str = GEN_MODEL) -> str:
    """Blocking generation. Used by the offline evaluation scripts."""
    response = _client().models.generate_content(
        model=model_name,
        contents=prompt,
    )
    return response.text


async def generate_with_gemini(prompt: str, model_name: str = GEN_MODEL) -> str:
    """
    Async generation for the FastAPI request path.

    Note `client.aio.models.generate_content` — `client.models.generate_content`
    is synchronous and returns a response object, not an awaitable, so awaiting
    it raises TypeError.
    """
    response = await _client().aio.models.generate_content(
        model=model_name,
        contents=prompt,
    )
    return response.text
