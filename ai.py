"""AI providers with automatic fallback: Gemini (Google AI Studio) first, then OpenRouter.

Both are optional individually; at least one key must be set in .env.
"""
import asyncio
import json
import logging
import re

import httpx

log = logging.getLogger("mentor.ai")


class MentorError(Exception):
    """A problem the learner should see as a friendly message."""


class ProviderError(Exception):
    def __init__(self, provider, code, retryable=True):
        super().__init__(f"{provider} error {code}")
        self.provider, self.code, self.retryable = provider, code, retryable


# ---------------------------------------------------------------- Gemini
class Gemini:
    name = "Gemini"

    def __init__(self, api_key: str, model: str, timeout_s: int = 60):
        from google import genai
        from google.genai import types
        self.types = types
        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_s * 1000))
        self.model = model

    async def complete(self, system, history, prompt, search=False, as_json=False) -> str:
        t = self.types
        contents = [t.Content(role=h["role"], parts=[t.Part(text=h["text"])]) for h in history]
        contents.append(t.Content(role="user", parts=[t.Part(text=prompt)]))
        use_search = search and not as_json
        for _ in range(2):
            kw = {"system_instruction": system}
            if use_search:
                kw["tools"] = [t.Tool(google_search=t.GoogleSearch())]
            if as_json:
                kw["response_mime_type"] = "application/json"
            try:
                resp = await self.client.aio.models.generate_content(
                    model=self.model, contents=contents, config=t.GenerateContentConfig(**kw))
                return (resp.text or "").strip()
            except Exception as e:
                code = getattr(e, "code", None)
                if use_search and code in (400, 403, 429):
                    use_search = False  # search not in quota: retry once without it
                    continue
                raise ProviderError(self.name, code, retryable=code in (429, 500, 502, 503, 504, None))
        raise ProviderError(self.name, "search")

    async def stream(self, system, history, prompt):
        """Yields the answer in pieces as the model writes it (so the learner sees text straight away)."""
        t = self.types
        contents = [t.Content(role=h["role"], parts=[t.Part(text=h["text"])]) for h in history]
        contents.append(t.Content(role="user", parts=[t.Part(text=prompt)]))
        try:
            it = await self.client.aio.models.generate_content_stream(
                model=self.model, contents=contents, config=t.GenerateContentConfig(system_instruction=system))
            async for chunk in it:
                if chunk.text:
                    yield chunk.text
        except ProviderError:
            raise
        except Exception as e:
            code = getattr(e, "code", None)
            raise ProviderError(self.name, code, retryable=code in (429, 500, 502, 503, 504, None))


# ---------------------------------------------------------------- OpenRouter (OpenAI-compatible)
class OpenRouter:
    name = "OpenRouter"
    URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, api_key: str, model: str, site_name: str = "Data Architect Mentor"):
        self.key, self.model = api_key, model
        self.headers = {"Authorization": f"Bearer {api_key}", "X-Title": site_name}
        self.http = httpx.AsyncClient(timeout=90)

    async def complete(self, system, history, prompt, search=False, as_json=False) -> str:
        msgs = [{"role": "system", "content": system}]
        msgs += [{"role": "assistant" if h["role"] == "model" else "user", "content": h["text"]} for h in history]
        msgs.append({"role": "user", "content": prompt + ("\n\nReturn only valid JSON." if as_json else "")})
        body = {"model": self.model, "messages": msgs}
        if as_json:
            body["response_format"] = {"type": "json_object"}
        try:
            r = await self.http.post(self.URL, headers=self.headers, json=body)
        except httpx.HTTPError:
            raise ProviderError(self.name, None)
        if r.status_code == 400 and as_json:  # model may not support response_format
            body.pop("response_format")
            r = await self.http.post(self.URL, headers=self.headers, json=body)
        if r.status_code != 200:
            raise ProviderError(self.name, r.status_code, retryable=r.status_code in (408, 429, 500, 502, 503, 504))
        data = r.json()
        if "error" in data:
            raise ProviderError(self.name, data["error"].get("code"))
        return (data["choices"][0]["message"].get("content") or "").strip()


# ---------------------------------------------------------------- fallback chain
def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        try:
            data = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            data = None
    return data if isinstance(data, dict) else {"_raw": text}


class AI:
    def __init__(self, providers: list, grader=None):
        if not providers:
            raise SystemExit("No AI provider configured: set GEMINI_API_KEY and/or OPENROUTER_API_KEY in .env")
        self.providers = providers
        self.grader = grader  # optional stronger model for grading answers

    async def ask(self, prompt, system, history=None, search=False, as_json=False, for_grading=False) -> str:
        chain = ([self.grader] if for_grading and self.grader else []) + self.providers
        last = None
        for round_ in range(2):  # two passes over the chain, short wait between
            for p in chain:
                try:
                    text = await p.complete(system, history or [], prompt, search=search, as_json=as_json)
                    if text:
                        return text
                    last = ProviderError(p.name, "empty")
                except ProviderError as e:
                    last = e
                    log.warning("%s (%s) failed: code=%s", p.name, getattr(p, "model", ""), e.code)
            if last and not last.retryable:
                break
            if round_ == 0:
                await asyncio.sleep(8)
        if last and last.code == 429:
            raise MentorError("The free AI quota is used up for now. Please try again later.")
        raise MentorError("The AI service is busy or misconfigured right now. Please try again in a few minutes.")

    async def stream(self, prompt, system, history=None):
        """Streams from the first provider that starts answering; moves on if one fails before its first words.
        Providers without streaming give their whole answer as one piece."""
        last = None
        for p in self.providers:
            started = False
            try:
                if hasattr(p, "stream"):
                    async for piece in p.stream(system, history or [], prompt):
                        started = True
                        yield piece
                else:
                    text = await p.complete(system, history or [], prompt)
                    if text:
                        started = True
                        yield text
                if started:
                    return
            except ProviderError as e:
                last = e
                log.warning("%s (%s) stream failed: code=%s", p.name, getattr(p, "model", ""), e.code)
                if started:  # failed half-way: tell the learner rather than mixing two answers
                    raise MentorError("The answer was cut off. Please ask again.")
        if last and last.code == 429:
            raise MentorError("The free AI quota is used up for now. Please try again later.")
        raise MentorError("The AI service is busy right now. Please try again in a minute.")

    async def ask_json(self, prompt, system, for_grading=False) -> dict:
        return _parse_json(await self.ask(prompt, system, as_json=True, for_grading=for_grading))


def build_ai(cfg) -> AI:
    providers = []
    if cfg.GEMINI_API_KEY:
        for model in [m.strip() for m in cfg.GEMINI_MODEL.split(",") if m.strip()]:
            providers.append(Gemini(cfg.GEMINI_API_KEY, model))
    if cfg.OPENROUTER_API_KEY and cfg.OPENROUTER_MODEL:
        providers.append(OpenRouter(cfg.OPENROUTER_API_KEY, cfg.OPENROUTER_MODEL))
    grader = None
    if cfg.OPENROUTER_API_KEY and cfg.OPENROUTER_GRADER_MODEL:
        grader = OpenRouter(cfg.OPENROUTER_API_KEY, cfg.OPENROUTER_GRADER_MODEL)
    return AI(providers, grader)
