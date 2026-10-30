"""The only file that talks to Microsoft Foundry.

Two engines, both asked one question: who is the responsible party for this return.

* FoundryGenerativeEngine: chat completion with structured output into ``types.Reading``. Answers
  with one responsible party, a reason, a draft reply and any flags. Costs input + output tokens.
* FoundryProbabilisticEngine: the Decisions API on the same deployment. No prose; it returns a
  probability for every responsible party and a confidence. Costs input tokens only.

There is no offline engine: every engine call is real. Tests inject a fake client through the
optional ``client`` parameter and exercise the pure message builders directly.

Both use an Entra ID token by default (no API key) and record token usage so the board can show
what each call cost. Photo bytes go to the API only; they never reach a log line.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from pathlib import Path
from typing import Any

from .types import (
    EngineContext, Engines, Reading, Request, ResponsibleParty, Scores, Usage,
)


log = logging.getLogger("returns_desk.engine")

ENV_PREFIX = "RETURNS_DESK_"
TOKEN_SCOPE = "https://ai.azure.com/.default"


def _env(name: str) -> str | None:
    return os.environ.get(ENV_PREFIX + name)


def _env_required(name: str) -> str:
    return os.environ[ENV_PREFIX + name]


# USD per 1M tokens, Global Standard, from the Azure retail prices API on
# 2026-10-27. Update if you change models; the app only uses this for display.
PRICES_PER_M = {
    "gpt-6-luna": (0.10, 0.50),
    "gpt-6-luna (decisions)": (0.10, 0.0),   # Decisions API bills input only
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4o-mini": (0.15, 0.60),
}


def estimate_cost_usd(model_name: str, usage: Usage) -> float | None:
    prices = PRICES_PER_M.get(model_name)
    if prices is None:
        return None
    inp, out = prices
    return (usage.input_tokens * inp + usage.output_tokens * out) / 1_000_000


# ----------------------------------------------------------------------------- prompts

PARTY_DESCRIPTIONS: dict[ResponsibleParty, str] = {
    ResponsibleParty.RETAILER: "The retailer bears the cost: a defect, the wrong item sent, or the item not as described.",
    ResponsibleParty.CARRIER: "The carrier bears the cost: the item was damaged or lost in transit.",
    ResponsibleParty.CUSTOMER: "The customer bears the cost: a change of mind, wrong size ordered, or damage after use.",
    ResponsibleParty.UNCLEAR: "The statement does not show who bears the cost; the clerk must find out.",
}

SCORING_QUESTION = "Who is the responsible party for this return?"

SYSTEM_PROMPT = f"""You work at the returns desk of Larkfield Home & Apparel, a retailer of
kitchenware and clothing that sells across the country. A customer has asked to send an item
back. Read their statement, and the photo if there is one, and say who is the responsible party: the party that bears the cost of this return. It is a statement of who pays,
not of who is to blame. Your answer is an input to Larkfield's policy and to the clerk; you do
not decide the return and you never choose a refund amount.

Responsible parties:
- retailer: {PARTY_DESCRIPTIONS[ResponsibleParty.RETAILER]}
- carrier: {PARTY_DESCRIPTIONS[ResponsibleParty.CARRIER]}
- customer: {PARTY_DESCRIPTIONS[ResponsibleParty.CUSTOMER]}
- unclear: {PARTY_DESCRIPTIONS[ResponsibleParty.UNCLEAR]} Use it when the statement and photo
  do not support one of the other three, rather than guessing.

Flags are observations for the clerk, with no authority of their own. Raise each only when the
statement or photo supports it:
- damage_in_transit: the parcel or item shows damage from shipping (crushed box, broken on arrival).
- defect: the item broke or stopped working through no action of the customer.
- remorse: the customer changed their mind or no longer wants the item.
- wrong_item: the customer received a different item from the one ordered.
- used: the item shows signs of use, wear or washing.
- possible_abuse: the account conflicts with the facts held on the order (for example, says it
  never arrived when the delivery date shows it delivered), or reads as an attempt to game the
  return.

reason: one sentence for the clerk explaining your answer.
reply_draft: at most three sentences to the customer, in Larkfield's voice: warm, plain, and
without promising any particular refund or replacement, since the clerk or policy decides that."""


def _media_type(path: Path) -> str:
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif",
    }.get(path.suffix.lower(), "application/octet-stream")


def _data_url(path: Path) -> str:
    """Base64 data URL for a photo. The result is sent to the API and never logged."""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{_media_type(path)};base64,{encoded}"


def _request_text(request: Request, context: EngineContext) -> str:
    item, line = context.item, context.order_line
    delivered = context.delivered.isoformat() if context.delivered else "not reported"
    reason_code = request.reason_code or "none picked"
    statement = request.statement.strip() or "(no words, photo only)"
    photo = "attached" if context.photo_path else "none"
    return (
        f"Item: {item.name} (category: {item.category})\n"
        f"Amount paid: ${line.paid:.2f}\n"
        f"Delivery date reported by the carrier: {delivered}\n"
        f"Request submitted: {request.submitted.isoformat()}\n"
        f"Reason code: {reason_code}\n"
        f"Photo: {photo}\n"
        f"Customer statement: {statement}"
    )


def build_reading_messages(request: Request, context: EngineContext) -> list[dict[str, Any]]:
    """Chat completion messages for the generative engine. Pure apart from reading the photo."""
    parts: list[dict[str, Any]] = [{"type": "text", "text": _request_text(request, context)}]
    if context.photo_path is not None:
        parts.append({"type": "image_url", "image_url": {"url": _data_url(context.photo_path)}})
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": parts},
    ]


def build_scoring_input(request: Request, context: EngineContext) -> list[dict[str, Any]]:
    """Decisions API input parts. Note the part names differ from chat completions."""
    text = (
        "The returns desk of Larkfield Home & Apparel, a retailer. Say which party bears the cost of this return "
        "(who pays, not who is to blame).\n" + _request_text(request, context)
    )
    parts: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
    if context.photo_path is not None:
        parts.append({"type": "input_image", "image_url": _data_url(context.photo_path)})
    return parts


def build_scoring_question() -> dict[str, Any]:
    return {
        "type": "choice",
        "name": "responsible_party",
        "instructions": SCORING_QUESTION,
        "choices": [{"value": p.value, "description": PARTY_DESCRIPTIONS[p]} for p in ResponsibleParty],
    }


# ----------------------------------------------------------------------------- client setup

def _make_client(endpoint: str, api_key: str | None) -> tuple[Any, str]:
    """OpenAI client on the Azure OpenAI v1 API. Returns (client, auth mode).

    Plain ``OpenAI`` pointed at ``https://<resource>.openai.azure.com/openai/v1/``. No dated
    api-version, and the client refreshes the Entra token itself when ``api_key`` is a callable.
    """
    from openai import OpenAI

    if api_key:
        return OpenAI(base_url=f"{endpoint}/openai/v1/", api_key=api_key), "api-key"
    # DefaultAzureCredential picks up `az login` locally and a managed identity in Azure.
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    token = get_bearer_token_provider(DefaultAzureCredential(), TOKEN_SCOPE)
    return OpenAI(base_url=f"{endpoint}/openai/v1/", api_key=token), "entra"


# ----------------------------------------------------------------------------- engines

class FoundryGenerativeEngine:
    """Chat deployment on a Microsoft Foundry resource, structured output into Reading."""

    def __init__(self, endpoint: str | None = None, deployment: str | None = None,
                 model_name: str | None = None, client: Any | None = None) -> None:
        self.endpoint = (endpoint or _env_required("ENDPOINT")).rstrip("/")
        self.deployment = deployment or _env_required("DEPLOYMENT")
        self.name = model_name or _env("MODEL") or self.deployment
        self.usage = Usage()
        api_key = _env("API_KEY")
        if client is not None:
            self._client, self.auth = client, ("api-key" if api_key else "entra")
        else:
            self._client, self.auth = _make_client(self.endpoint, api_key)

    def read(self, request: Request, context: EngineContext) -> Reading:
        messages = build_reading_messages(request, context)
        t0 = time.perf_counter()
        try:
            completion = self._client.chat.completions.parse(
                model=self.deployment,
                messages=messages,
                response_format=Reading,
                # No temperature: reasoning-style models such as gpt-6-luna only accept the default.
            )
        except Exception as e:
            log.error("generative engine chat.completions failed for %s: %s", request.request_id, e)
            raise
        u = completion.usage
        if u is not None:
            details = u.prompt_tokens_details
            cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
            self.usage.add(u.prompt_tokens, u.completion_tokens, cached)
        parsed = completion.choices[0].message.parsed
        log.info("generative %s: %s -> %s (photo %s, %d in / %d out tokens, %.1fs)", self.deployment,
                 request.request_id, parsed.responsible_party.value if parsed else None,
                 "yes" if context.photo_path else "no",
                 u.prompt_tokens if u else -1, u.completion_tokens if u else -1,
                 time.perf_counter() - t0)
        if parsed is None:  # refusal or content filter: the clerk has to look
            return Reading(responsible_party=ResponsibleParty.UNCLEAR,
                           reason="The generative engine returned no structured answer.",
                           reply_draft="", flags=[])
        return Reading.model_validate(parsed.model_dump())


class FoundryProbabilisticEngine:
    """Decisions API on a gpt-6-luna deployment. One choice question per request."""

    def __init__(self, endpoint: str | None = None, deployment: str | None = None,
                 model_name: str | None = None, client: Any | None = None) -> None:
        self.endpoint = (endpoint or _env_required("ENDPOINT")).rstrip("/")
        self.deployment = deployment or _env("DECISION_DEPLOYMENT") or _env_required("DEPLOYMENT")
        base = model_name or _env("MODEL") or self.deployment
        self.name = f"{base} (decisions)"
        self.usage = Usage()
        # RETURNS_DESK_DECISION_API_KEY lets the probabilistic engine use a key while the reading
        # engine stays on Entra. The infra grants Foundry User, which covers both, so it is unset there.
        api_key = _env("DECISION_API_KEY") or _env("API_KEY")
        if client is not None:
            self._client, self.auth = client, ("api-key" if api_key else "entra")
        else:
            self._client, self.auth = _make_client(self.endpoint, api_key)

    def score(self, request: Request, context: EngineContext) -> Scores:
        parts = build_scoring_input(request, context)
        t0 = time.perf_counter()
        try:
            result = self._client.decisions.create(
                model=self.deployment,
                input=[{"type": "message", "role": "user", "content": parts}],
                questions=[build_scoring_question()],
            )
        except Exception as e:
            log.error("decisions.create failed for %s: %s", request.request_id, e)
            raise
        u = getattr(result, "usage", None)
        if u is not None:
            details = getattr(u, "input_tokens_details", None)
            cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
            self.usage.add(u.input_tokens, u.output_tokens or 0, cached)
        answer = result.answers[0]
        if answer.type != "choice":  # refusal: the clerk has to look
            log.info("probabilistic %s: %s -> refusal", self.deployment, request.request_id)
            return Scores({p: (1.0 if p is ResponsibleParty.UNCLEAR else 0.0) for p in ResponsibleParty}, 0.0)
        named = {p.value: float(p.probability) for p in answer.probabilities}
        probs = {party: named.get(party.value, 0.0) for party in ResponsibleParty}
        confidence = float(answer.confidence)
        log.info("probabilistic %s: %s -> %s (conf %.2f, photo %s, %d in tokens, %.1fs)", self.deployment,
                 request.request_id, answer.choice, confidence, "yes" if context.photo_path else "no",
                 u.input_tokens if u else -1, time.perf_counter() - t0)
        return Scores(probs, confidence)


def from_env() -> Engines:
    """Both engines from RETURNS_DESK_* env vars. Raises KeyError if the endpoint is unset."""
    return Engines(reading=FoundryGenerativeEngine(), scoring=FoundryProbabilisticEngine())
