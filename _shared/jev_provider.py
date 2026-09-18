"""Jev client with provider selection driven by providers.toml.

This file is the single source of truth. It is COPIED verbatim into each app
folder by `scripts/sync_provider.py` so every app stays standalone-forkable --
clone one folder, `uv run app.py`, no repo-root imports. CI checks the copies
match. Do not edit the copies; edit this file and re-sync.

Both supported providers speak the same wire format:

    POST <base_url>   {"model":..., "state":..., "questions": {...}}
    ->                {"model":..., "answers": {...}, "usage": {...}}

so switching providers is a config change, never a code change.
"""

from __future__ import annotations

import os
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

DEFAULT_PROVIDER = "typesafe"
RETRY_STATUS = {429, 529, 500, 502, 503}
MAX_ATTEMPTS = 5


class JevError(RuntimeError):
    """Any failure talking to a Jev provider."""


class MissingKeyError(JevError):
    """The selected provider's API key is not in the environment."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _find_config(start: Path | None = None) -> Path | None:
    """Walk up from `start` looking for providers.toml.

    An app folder may carry its own providers.toml to pin a provider; otherwise
    the repo-root one wins. Returns None if neither exists.
    """
    here = (start or Path(__file__).resolve().parent).resolve()
    for directory in (here, *here.parents):
        candidate = directory / "providers.toml"
        if candidate.is_file():
            return candidate
    return None


def _load_dotenv(config_path: Path | None) -> None:
    """Populate os.environ from .env files next to providers.toml and the app.

    Deliberately tiny rather than depending on python-dotenv: one less install
    for someone who forked a single folder. Existing env vars always win.
    """
    candidates = [Path.cwd() / ".env"]
    if config_path is not None:
        candidates.append(config_path.parent / ".env")
    for env_file in candidates:
        if not env_file.is_file():
            continue
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key and value and key not in os.environ:
                os.environ[key] = value


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    model: str
    api_key_env: str
    signup: str
    context_tokens: int
    input_per_mtok: float
    output_per_mtok: float

    @property
    def api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise MissingKeyError(
                f"Provider '{self.name}' needs {self.api_key_env}, which is not set.\n"
                f"  1. cp .env.example .env\n"
                f"  2. put your key in {self.api_key_env}\n"
                f"  Get one at: {self.signup}\n"
                f"  Or switch providers:  export JEV_PROVIDER=<other>"
            )
        return key


def load_provider(name: str | None = None, start: Path | None = None) -> ProviderConfig:
    """Resolve which provider to use: arg > JEV_PROVIDER env > providers.toml > default."""
    config_path = _find_config(start)
    _load_dotenv(config_path)

    if config_path is None:
        raise JevError(
            "No providers.toml found. Copy the one from the repo root next to this app."
        )
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))

    chosen = name or os.environ.get("JEV_PROVIDER") or config.get("provider") or DEFAULT_PROVIDER
    if chosen not in config:
        known = sorted(k for k, v in config.items() if isinstance(v, dict) and k != "pricing")
        raise JevError(f"Unknown provider '{chosen}'. providers.toml defines: {known}")

    section = config[chosen]
    pricing = config.get("pricing", {})
    return ProviderConfig(
        name=chosen,
        base_url=section["base_url"],
        model=section["model"],
        api_key_env=section["api_key_env"],
        signup=section.get("signup", ""),
        context_tokens=int(section.get("context_tokens", 32000)),
        input_per_mtok=float(pricing.get("input_per_mtok", 0.042)),
        output_per_mtok=float(pricing.get("output_per_mtok", 0.0)),
    )


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


@dataclass
class Answers:
    """Typed accessors over one response. Raises on id/type mismatch rather than
    silently returning None, because a typo in a question id should fail loudly."""

    raw: dict[str, Any]
    provider: ProviderConfig
    elapsed_s: float = 0.0

    def _get(self, qid: str, expected: str) -> dict[str, Any]:
        answers = self.raw.get("answers", {})
        if qid not in answers:
            raise KeyError(f"No answer for '{qid}'. Got: {sorted(answers)}")
        answer = answers[qid]
        if answer.get("type") != expected:
            raise TypeError(f"'{qid}' is a {answer.get('type')}, not a {expected}")
        return answer

    def noul(self, qid: str) -> float:
        """P(yes), 0..1. A noul has no separate confidence -- 0.5 means genuinely
        split, NOT 'medium intensity'."""
        return float(self._get(qid, "noul")["noul"])

    def choice(self, qid: str) -> str:
        return str(self._get(qid, "choice")["choice"])

    def score(self, qid: str) -> float:
        """Probability-weighted position across levels; lands between levels."""
        return float(self._get(qid, "score")["score"])

    def probabilities(self, qid: str) -> dict[str, float]:
        answer = self.raw.get("answers", {}).get(qid, {})
        return {k: float(v) for k, v in answer.get("probabilities", {}).items()}

    def confidence(self, qid: str) -> float:
        """Distribution concentration for choice/score. NOT permission to act and
        NOT workflow correctness -- see docs/confidence-is-not-correctness.md."""
        answer = self.raw.get("answers", {}).get(qid, {})
        if "confidence" not in answer:
            raise TypeError(f"'{qid}' is a {answer.get('type')}; only choice/score carry confidence")
        return float(answer["confidence"])

    @property
    def input_tokens(self) -> int:
        return int(self.raw.get("usage", {}).get("input_tokens", 0))

    @property
    def cost_usd(self) -> float:
        """Output tokens are free on Jev, so this is input-only by design."""
        return self.input_tokens / 1e6 * self.provider.input_per_mtok


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@dataclass
class JevClient:
    provider: ProviderConfig = field(default_factory=load_provider)
    timeout_s: float = 60.0
    _client: httpx.Client | None = field(default=None, repr=False)

    # Running totals, so every app can print what a run actually cost.
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    calls: int = 0

    def __post_init__(self) -> None:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout_s)

    def ask(self, state: Any, questions: dict[str, dict[str, Any]]) -> Answers:
        """Evaluate `state` against every question in one round trip.

        Questions are answered in PARALLEL against a state ingested once, and
        cannot see each other's answers. Batching N questions into one call is
        dramatically cheaper and faster than N calls -- always prefer one call.
        """
        if not questions:
            raise ValueError("ask() needs at least one question")

        payload = {"model": self.provider.model, "state": state, "questions": questions}
        headers = {
            "Authorization": f"Bearer {self.provider.api_key}",
            "Content-Type": "application/json",
        }

        started = time.perf_counter()
        response = self._request(payload, headers)
        elapsed = time.perf_counter() - started

        answers = Answers(raw=response, provider=self.provider, elapsed_s=elapsed)
        self.calls += 1
        self.total_input_tokens += answers.input_tokens
        self.total_cost_usd += answers.cost_usd
        return answers

    def _request(self, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        assert self._client is not None
        last_error: Exception | None = None

        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self._client.post(self.provider.base_url, json=payload, headers=headers)
            except httpx.RequestError as exc:  # network-level, worth retrying
                last_error = exc
                time.sleep(2**attempt * 0.5)
                continue

            if response.status_code in RETRY_STATUS:
                # Honour Retry-After when the provider sends one.
                wait = response.headers.get("retry-after")
                delay = float(wait) if wait and wait.replace(".", "", 1).isdigit() else 2**attempt * 0.5
                last_error = JevError(f"{response.status_code}: {response.text[:200]}")
                time.sleep(delay)
                continue

            if response.status_code == 402:
                raise JevError(
                    f"402 Payment Required from '{self.provider.name}'. "
                    f"Add credit at {self.provider.signup}"
                )
            if response.status_code == 401:
                raise JevError(
                    f"401 Unauthorized. {self.provider.api_key_env} is set but rejected by "
                    f"'{self.provider.name}'. Check the key is for the right service."
                )
            if response.status_code >= 400:
                raise JevError(f"{response.status_code} from {self.provider.base_url}: {response.text[:500]}")

            return response.json()

        raise JevError(f"Giving up after {MAX_ATTEMPTS} attempts. Last error: {last_error}")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def __enter__(self) -> "JevClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Question builders -- thin, so app code reads like the docs
# ---------------------------------------------------------------------------


def noul(instructions: Any, *, true: str | None = None, false: str | None = None) -> dict[str, Any]:
    """Yes/no. Returns P(yes). Use one per label when several may apply at once."""
    question: dict[str, Any] = {"type": "noul", "instructions": instructions}
    criteria = {k: v for k, v in (("true", true), ("false", false)) if v is not None}
    if criteria:
        question["criteria"] = criteria
    return question


def choice(instructions: Any, options: dict[str, str | None]) -> dict[str, Any]:
    """Exactly one option wins. Include a no-match option when nothing may fit."""
    if len(options) < 2:
        raise ValueError("a choice needs at least 2 options")
    return {"type": "choice", "instructions": instructions, "criteria": options}


def score(instructions: Any, levels: list[str]) -> dict[str, Any]:
    """Ordered rubric. Each level must describe a concrete situation and stand alone."""
    if len(levels) < 2:
        raise ValueError("a score needs at least 2 levels")
    return {"type": "score", "instructions": instructions, "criteria": levels}


# ---------------------------------------------------------------------------
# Self-check: `python jev_provider.py` -- offline, no key or network needed.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent

    cfg = load_provider("typesafe", start=repo_root)
    assert cfg.name == "typesafe" and cfg.base_url.startswith("https://api.typesafe.ai"), cfg
    assert cfg.context_tokens == 64000, cfg

    other = load_provider("openrouter", start=repo_root)
    assert other.base_url.endswith("/api/alpha/decisions"), other
    assert other.model == "typesafe/jev-1.13", other
    assert other.context_tokens == 32000, "OpenRouter serves 32k, not TypeSafe's 64k"

    os.environ["JEV_PROVIDER"] = "openrouter"
    assert load_provider(start=repo_root).name == "openrouter", "env var must beat providers.toml"
    del os.environ["JEV_PROVIDER"]

    assert noul("q?") == {"type": "noul", "instructions": "q?"}
    assert noul("q?", true="yes means")["criteria"] == {"true": "yes means"}
    assert choice("pick", {"a": None, "b": "desc"})["criteria"]["b"] == "desc"
    assert score("rate", ["low", "high"])["criteria"] == ["low", "high"]
    for bad, args in ((choice, ("x", {"only": None})), (score, ("x", ["only"]))):
        try:
            bad(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad.__name__} must reject a single option")

    fake = Answers(
        raw={
            "answers": {
                "urgent": {"type": "noul", "noul": 0.92},
                "team": {
                    "type": "choice",
                    "choice": "technical",
                    "probabilities": {"billing": 0.08, "technical": 0.85, "sales": 0.07},
                    "confidence": 0.82,
                },
                "anger": {
                    "type": "score",
                    "score": 1.6,
                    "legend": {"0": "Calm", "1": "Frustrated", "2": "Very angry"},
                    "probabilities": {"0": 0.05, "1": 0.3, "2": 0.65},
                    "confidence": 0.78,
                },
            },
            "usage": {"input_tokens": 1_000_000, "output_tokens": 48},
        },
        provider=cfg,
    )
    assert fake.noul("urgent") == 0.92
    assert fake.choice("team") == "technical"
    assert fake.score("anger") == 1.6
    assert fake.confidence("team") == 0.82
    assert abs(fake.cost_usd - 0.042) < 1e-9, "1M input tokens must cost exactly $0.042"

    for call, exc in (
        (lambda: fake.noul("team"), TypeError),
        (lambda: fake.noul("nope"), KeyError),
        (lambda: fake.confidence("urgent"), TypeError),
    ):
        try:
            call()
        except exc:
            pass
        else:
            raise AssertionError(f"expected {exc.__name__}")

    print("jev_provider self-check passed")
