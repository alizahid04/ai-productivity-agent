"""Cloud LLM service — Hugging Face Inference API and Google Gemini.

This module is the ONLY place in the codebase that talks to an LLM
backend. Every other module (the agent graph, the LLM-powered tools) only
calls the small interface exposed here — complete_chat(),
complete_json_with_retry(), complete_structured_with_retry() — so adding a
new provider or changing which model/provider is active never touches
graph.py or the tools.

Two providers are supported, selectable at runtime via the Settings page
(`/api/settings/llm`, backed by app.agent.llm_settings):
  - Hugging Face Inference API, with up to three configurable models
    (HF_MODEL_1/2/3 in .env) to pick between.
  - Google Gemini.
Both share the same retry/self-correction logic via `_BaseLLMClient`; each
provider class only implements `_chat()`.
"""
from __future__ import annotations

from app.config import get_settings


class LLMError(Exception):
    """Raised when the configured LLM backend is unreachable, misconfigured,
    or returns something unusable. Callers show this text directly to the
    user, so messages here should always explain how to fix the problem."""


class _BaseLLMClient:
    """Shared retry/self-correction logic. Subclasses only need to
    implement `_chat(messages, temperature, want_json) -> str`."""

    def _chat(self, messages: list[dict], temperature: float = 0.2, want_json: bool = False) -> str:
        raise NotImplementedError

    def complete_json(self, system: str, user: str, temperature: float = 0.1) -> str:
        """Ask the model for a structured-JSON response. Returns raw text (caller parses)."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self._chat(messages, temperature=temperature, want_json=True)

    def complete_json_with_retry(self, system: str, user: str, max_retries: int = 2, temperature: float = 0.1) -> dict:
        """Like complete_json, but self-corrects malformed output internally:
        if the model's response doesn't parse as JSON, it's told exactly what
        went wrong and asked to try again, up to `max_retries` times, before
        this raises.
        """
        from app.agent.json_utils import JSONExtractionError, extract_json_object  # local import avoids a cycle

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error: JSONExtractionError | None = None

        for attempt in range(max_retries + 1):
            raw = self._chat(messages, temperature=temperature, want_json=True)
            try:
                return extract_json_object(raw)
            except JSONExtractionError as e:
                last_error = e
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"That was not valid JSON ({e}). Respond again with ONLY a single "
                            "valid JSON object — no markdown fences, no commentary."
                        ),
                    }
                )
        raise last_error  # type: ignore[misc]

    def complete_structured_with_retry(
        self, system: str, user: str, schema: type, max_retries: int = 2, temperature: float = 0.1
    ):
        """Like complete_json_with_retry, but also validates the parsed JSON
        against a Pydantic model, retrying on either failure mode (invalid
        JSON, or valid JSON that doesn't match the schema) with the same
        combined retry budget.
        """
        from pydantic import ValidationError

        from app.agent.json_utils import JSONExtractionError, extract_json_object  # local import avoids a cycle

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            raw = self._chat(messages, temperature=temperature, want_json=True)
            correction: str | None = None
            try:
                parsed = extract_json_object(raw)
            except JSONExtractionError as e:
                last_error = e
                correction = f"That was not valid JSON ({e}). Respond again with ONLY a single valid JSON object."
            else:
                try:
                    return schema(**parsed)
                except ValidationError as e:
                    last_error = e
                    correction = (
                        f"That JSON did not match the required schema: {e}. "
                        "Respond again with corrected JSON matching the schema exactly."
                    )

            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": correction})

        raise last_error  # type: ignore[misc]

    def complete_chat(self, messages: list[dict], temperature: float = 0.3) -> str:
        return self._chat(messages, temperature=temperature, want_json=True)


class HuggingFaceLLMClient(_BaseLLMClient):
    """Talks to the Hugging Face Inference API (router, OpenAI-compatible
    chat-completions surface)."""

    def __init__(self, api_key: str, model: str, api_base: str, timeout: int = 30):
        self.api_key = api_key
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

    def _post(self, payload: dict):
        import httpx

        url = f"{self.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                return client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as e:
            raise LLMError(f"Hugging Face API timed out after {self.timeout}s") from e
        except httpx.TransportError as e:
            raise LLMError(f"Could not reach Hugging Face API: {e}") from e

    def _chat(self, messages: list[dict], temperature: float = 0.2, want_json: bool = False) -> str:
        base_payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 1200,
        }

        resp = None
        if want_json:
            # Most providers behind the HF router honor OpenAI-style JSON
            # mode. If a provider rejects the field, fall back to a plain
            # request instead of failing the whole call.
            resp = self._post({**base_payload, "response_format": {"type": "json_object"}})
            if resp.status_code >= 400 and b"response_format" in resp.content:
                resp = None

        if resp is None:
            resp = self._post(base_payload)

        if resp.status_code == 401:
            raise LLMError("Hugging Face API rejected the request: invalid API key.")
        if resp.status_code == 404:
            raise LLMError(
                f"Hugging Face API model '{self.model}' not found or not deployed via Inference Providers."
            )
        if resp.status_code == 429:
            raise LLMError("Hugging Face API rate limit exceeded. Please retry shortly.")
        if resp.status_code >= 500:
            raise LLMError(f"Hugging Face API server error ({resp.status_code}).")
        if resp.status_code >= 400:
            raise LLMError(f"Hugging Face API error {resp.status_code}: {resp.text[:300]}")

        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"Unexpected Hugging Face API response shape: {e}") from e


class GeminiLLMClient(_BaseLLMClient):
    """Talks to the Google Gemini API (generateContent endpoint)."""

    def __init__(self, api_key: str, model: str, timeout: int = 30):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    @staticmethod
    def _to_gemini_payload(messages: list[dict], temperature: float, want_json: bool) -> dict:
        system_parts: list[str] = []
        contents: list[dict] = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                system_parts.append(m["content"])
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": m["content"]}]})
            else:
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})

        payload: dict = {
            "contents": contents,
            "generationConfig": {"temperature": temperature},
        }
        if system_parts:
            payload["system_instruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        if want_json:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        return payload

    def _chat(self, messages: list[dict], temperature: float = 0.2, want_json: bool = False) -> str:
        import httpx

        url = f"{self.base_url}/models/{self.model}:generateContent"
        payload = self._to_gemini_payload(messages, temperature, want_json)

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, params={"key": self.api_key}, json=payload)
        except httpx.TimeoutException as e:
            raise LLMError(f"Gemini API timed out after {self.timeout}s") from e
        except httpx.TransportError as e:
            raise LLMError(f"Could not reach Gemini API: {e}") from e

        if resp.status_code in (401, 403):
            raise LLMError("Gemini API rejected the request: invalid or unauthorized API key.")
        if resp.status_code == 404:
            raise LLMError(f"Gemini model '{self.model}' not found.")
        if resp.status_code == 429:
            raise LLMError("Gemini API rate limit exceeded. Please retry shortly.")
        if resp.status_code >= 500:
            raise LLMError(f"Gemini API server error ({resp.status_code}).")
        if resp.status_code >= 400:
            raise LLMError(f"Gemini API error {resp.status_code}: {resp.text[:300]}")

        try:
            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                block_reason = (data.get("promptFeedback") or {}).get("blockReason")
                if block_reason:
                    raise LLMError(f"Gemini declined to respond (reason: {block_reason}).")
                raise LLMError("Gemini returned no candidates.")
            parts = candidates[0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"Unexpected Gemini API response shape: {e}") from e


def get_llm_client() -> _BaseLLMClient:
    """Builds a fresh client reflecting the currently selected provider and
    (for Hugging Face) the currently active model. Not cached — the
    selection can change at runtime via the Settings page, and construction
    here is cheap (no network I/O happens until a request is made).
    """
    from app.agent import llm_settings

    settings = get_settings()
    state = llm_settings.get_state()
    provider = state["provider"]

    if provider == "gemini":
        if not settings.GEMINI_API_KEY:
            raise LLMError(
                "Gemini is the selected provider, but GEMINI_API_KEY is not set. "
                "Add it to .env and restart the app, or switch providers in Settings."
            )
        return GeminiLLMClient(
            api_key=settings.GEMINI_API_KEY,
            model=settings.GEMINI_MODEL,
            timeout=settings.TOOL_TIMEOUT_SECONDS,
        )

    # Default / "huggingface"
    if not settings.HF_API_KEY:
        raise LLMError(
            "Hugging Face is the selected provider, but HF_API_KEY is not set. "
            "Add it to .env and restart the app, or switch providers in Settings."
        )
    model = state.get("active_hf_model") or settings.HF_MODEL_1
    return HuggingFaceLLMClient(
        api_key=settings.HF_API_KEY,
        model=model,
        api_base=settings.HF_API_BASE,
        timeout=settings.TOOL_TIMEOUT_SECONDS,
    )


def verify_llm_backend_ready(settings) -> None:
    """Called once at application startup. Confirms at least the currently
    selected provider has its API key configured, failing fast with a clear
    message otherwise — there is intentionally no fallback to fake/
    rule-based behavior.
    """
    from app.agent import llm_settings

    state = llm_settings.get_state()
    provider = state["provider"]

    if provider == "gemini":
        if not settings.GEMINI_API_KEY:
            raise RuntimeError(
                "\n[FATAL] LLM_PROVIDER is set to 'gemini', but GEMINI_API_KEY is missing.\n"
                "Set GEMINI_API_KEY in your .env file, or set LLM_PROVIDER=huggingface with "
                "HF_API_KEY set instead. See .env.example.\n"
            )
        return

    if not settings.HF_API_KEY:
        raise RuntimeError(
            "\n[FATAL] LLM_PROVIDER is set to 'huggingface', but HF_API_KEY is missing.\n"
            "Set HF_API_KEY in your .env file, or set LLM_PROVIDER=gemini with GEMINI_API_KEY "
            "set instead. See .env.example.\n"
        )
