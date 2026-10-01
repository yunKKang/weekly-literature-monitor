"""LLM Review Stage — second-pass relevance judgment using LLM.

This stage sends paper abstracts to an LLM and asks: "Is this paper really
about the environmental impacts of gross fixed capital formation (GFCF)?
Does it use MRIO/EEIO methods, analyze capital stock lifecycle impacts,
or quantify embodied emissions in investment?"

The LLM acts as a final filter to remove false positives that passed
the rule-based scoring but are semantically off-topic.

Stage name: llm_review
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from literature_monitor.pipeline.base import PipelineState

logger = logging.getLogger(__name__)

# Default prompt template for GFCF research relevance judgment
DEFAULT_PROMPT = """You are a research assistant helping to filter academic papers
for relevance to a specific research topic.

## Research Topic

{topic_name}: {topic_description}

## Paper to Evaluate

Title: {title}
Abstract: {abstract}
Journal: {journal}
Year: {year}

## Your Task

Based on the research topic description above, determine if this paper is genuinely
relevant to the topic. Consider:
- Does the paper's core research question align with the topic?
- Does it use methods or address issues central to this research area?
- Would a researcher in this field find this paper valuable?

## Output Format

Respond in JSON:
{{
  "relevant": true/false,
  "confidence": "high"/"medium"/"low",
  "reason": "<one sentence explaining the judgment>"
}}
"""

# Stage 1: Title-only fast filter (cheap, ~100 tokens)
TITLE_FILTER_PROMPT = """You are a research paper classifier. Quickly determine if a paper's title suggests it is relevant to a research topic.

Topic: {topic_name}

Title: {title}
Journal: {journal}

Is this paper likely relevant to the topic? Answer ONLY in JSON:
{{"likely_relevant": true/false, "confidence": "high"/"medium"/"low", "reason": "brief"}}
"""


class LLMReviewStage:
    """Second-pass relevance judgment using LLM.

    Only runs on papers above min_level threshold (default: MEDIUM).
    Sends title + abstract to LLM with a domain-specific prompt.
    Papers judged irrelevant get a large score penalty.

    Stage name: llm_review
    """

    name = "llm_review"

    def __init__(
        self,
        topic_name: str = "GFCF Environmental Impact",
        topic_description: str = "",
        provider: str = "deepseek",
        model: str = "deepseek-chat",
        min_level: str = "MEDIUM",
        max_papers: int = 50,
        max_cost_usd: float = 5.0,
        penalty_points: float = 100.0,
        prompt_template: str | None = None,
        api_key: str | None = None,
        base_url: str = "",
        two_stage: bool = True,
        concurrency: int = 3,
    ) -> None:
        self.topic_name = topic_name
        self.topic_description = topic_description or topic_name
        self.provider = provider
        self.model = model
        self.min_level = min_level
        self.max_papers = max_papers
        self.max_cost_usd = max_cost_usd
        self.penalty_points = penalty_points
        self.prompt_template = prompt_template or DEFAULT_PROMPT
        self.api_key = api_key or self._default_api_key(provider)
        self.base_url = base_url or self._default_base_url(provider)
        self.two_stage = two_stage
        self.concurrency = concurrency
        self._stage1_calls = 0
        self._stage2_calls = 0
        self._calls_made = 0
        self._total_cost = 0.0

    @staticmethod
    def _default_api_key(provider: str) -> str:
        return {
            "deepseek": os.environ.get("DEEPSEEK_API_KEY", ""),
            "openai": os.environ.get("OPENAI_API_KEY", ""),
            "anthropic": os.environ.get("ANTHROPIC_API_KEY", ""),
        }.get(provider, "")

    @staticmethod
    def _default_base_url(provider: str) -> str:
        return {
            "deepseek": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            "openai": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        }.get(provider, "")

    @staticmethod
    def _token_rates(provider: str, model: str) -> tuple[float, float]:
        """USD per million input/output tokens used for budget accounting."""
        if provider == "deepseek":
            return 0.14, 0.28
        if provider == "anthropic":
            return 3.0, 15.0
        return 0.15, 0.60

    def run(self, state: PipelineState) -> PipelineState:
        """Run LLM review. Two-stage mode: title filter → abstract deep judge."""
        # Check budget
        if self._calls_made >= self.max_papers:
            return state
        if self._total_cost >= self.max_cost_usd:
            return state

        # Check relevance level threshold
        level_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        if level_order.get(state.relevance_level, 0) < level_order.get(self.min_level, 0):
            return state

        if not self.two_stage:
            return self._run_single_stage(state)

        # === Two-stage mode ===

        # Legacy-protected papers: skip title filter, go straight to deep review
        # (they score HIGH on legacy mechanism and must not be penalized)
        if state.breakdown.get('legacy_priority') in ('HIGH', 'MEDIUM'):
            logger.info("Legacy bypass for paper: %s", state.title[:40])
            # Force single-stage deep review
            return self._run_single_stage(state)

        # Stage 1: Title-only fast filter (cheap)
        if not state.title or len(state.title) < 10:
            return self._run_single_stage(state)

        try:
            stage1_result = self._call_title_filter(state)
        except Exception as e:
            logger.warning("LLM stage1 failed: %s", e)
            return state
        if not stage1_result:
            return state

        self._stage1_calls += 1

        # If Stage 1 says NOT likely relevant → penalize immediately
        if not stage1_result.get("likely_relevant", True):
            self._calls_made += 1
            breakdown = dict(state.breakdown)
            breakdown["llm_review"] = {
                "stage": "title_filter",
                "relevant": False,
                "confidence": stage1_result.get("confidence", "low"),
                "reason": stage1_result.get("reason", ""),
                "model": self.model,
            }
            return state.with_(
                total_score=max(0.0, state.total_score - self.penalty_points),
                relevance_level="LOW",
                breakdown=breakdown,
            )

        # Stage 2: Deep abstract judgment (only if Stage 1 says likely relevant)
        if not state.abstract or len(state.abstract) < 50:
            # No abstract — use Stage 1 result as final
            self._calls_made += 1
            breakdown = dict(state.breakdown)
            breakdown["llm_review"] = {
                "stage": "title_filter_only",
                "relevant": True,
                "confidence": stage1_result.get("confidence", "low"),
                "reason": stage1_result.get("reason", ""),
                "model": self.model,
            }
            boost = 2.0 if stage1_result.get("confidence") == "high" else 0.0
            return state.with_(
                total_score=round(state.total_score + boost, 3),
                breakdown=breakdown,
            )

        try:
            stage2_result = self._call_llm(state)
        except Exception as e:
            logger.warning("LLM stage2 failed: %s", e)
            return state
        if not stage2_result:
            return state

        self._stage2_calls += 1
        self._calls_made += 1

        return self._apply_llm_result(state, stage2_result, stage="abstract_judge")

    def _run_single_stage(self, state: PipelineState) -> PipelineState:
        """Single-stage LLM review (backward compat)."""
        if not state.abstract or len(state.abstract) < 50:
            return state
        try:
            result = self._call_llm(state)
        except Exception as e:
            logger.warning("LLM review failed: %s", e)
            return state
        if not result:
            return state
        self._calls_made += 1
        return self._apply_llm_result(state, result, stage="single")

    def _apply_llm_result(self, state: PipelineState, result: dict, stage: str = "") -> PipelineState:
        """Apply LLM judgment to paper score."""
        relevant = result.get("relevant", True)
        confidence = result.get("confidence", "low")
        reason = result.get("reason", "")

        breakdown = dict(state.breakdown)
        breakdown["llm_review"] = {
            "stage": stage,
            "relevant": relevant,
            "confidence": confidence,
            "reason": reason,
            "model": self.model,
        }
        # Include 6D output if present
        for dim in ("methodology", "research_object", "environmental_dimension"):
            if dim in result:
                breakdown["llm_review"][dim] = result[dim]

        if not relevant:
            return state.with_(
                total_score=max(0.0, state.total_score - self.penalty_points),
                relevance_level="LOW",
                breakdown=breakdown,
            )

        boost = 5.0 if confidence == "high" else 2.0 if confidence == "medium" else 0.0
        return state.with_(
            total_score=round(state.total_score + boost, 3),
            breakdown=breakdown,
        )

    def _call_title_filter(self, state: PipelineState) -> dict[str, Any] | None:
        """Stage 1: Fast title-only relevance filter (cheap, ~100 tokens)."""
        prompt = TITLE_FILTER_PROMPT.format(
            topic_name=self.topic_name,
            title=state.title,
            journal=state.journal or "unknown",
        )
        try:
            return self._call_api(prompt, response_contract="title_filter")
        except Exception as e:
            logger.warning("Title filter API call failed: %s", e)
            return None

    def _call_llm(self, state: PipelineState) -> dict[str, Any] | None:
        """Stage 2: Full abstract-based LLM judgment."""
        prompt = self.prompt_template.format(
            topic_name=self.topic_name,
            topic_description=self.topic_description,
            title=state.title,
            abstract=state.abstract[:2000],
            journal=state.journal,
            year=state.year or "unknown",
        )
        return self._call_api(prompt, response_contract="abstract_review")

    def _call_api(self, prompt: str, response_contract: str = "abstract_review") -> dict[str, Any] | None:
        """Dispatch to the appropriate LLM API provider."""
        try:
            if self.provider in {"openai", "deepseek"}:
                return self._call_compatible_chat(prompt, response_contract=response_contract)
            elif self.provider == "anthropic":
                return self._call_anthropic(prompt, response_contract=response_contract)
            else:
                logger.warning("Unknown LLM provider: %s", self.provider)
                return None
        except Exception as e:
            logger.warning("LLM API call failed: %s", e)
            return None

    @staticmethod
    def _parse_json_response(
        content: str,
        response_contract: str = "abstract_review",
    ) -> dict[str, Any] | None:
        """Parse, normalize, and validate an LLM JSON response contract."""
        parsed = LLMReviewStage._extract_json_object(content)
        if parsed is None:
            logger.warning("LLM returned non-JSON: %s", content[:150])
            return None
        return LLMReviewStage._normalize_json_contract(parsed, response_contract)

    @staticmethod
    def _extract_json_object(content: str) -> dict[str, Any] | None:
        """Extract the first JSON object from common LLM response wrappers."""
        import re as _re

        content = _re.sub(r"<[^>]*>", "", content).strip()
        if not content:
            return None

        candidates = [content]
        fenced = _re.sub(r"^```(?:json)?\s*", "", content, flags=_re.MULTILINE)
        fenced = _re.sub(r"```\s*$", "", fenced, flags=_re.MULTILINE).strip()
        if fenced != content:
            candidates.append(fenced)
        m = _re.search(r"\{.*\}", content, _re.DOTALL)
        if m:
            candidates.append(m.group())

        for candidate in candidates:
            if not candidate.startswith("{"):
                continue
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        return None

    @staticmethod
    def _normalize_json_contract(
        value: dict[str, Any],
        response_contract: str,
    ) -> dict[str, Any] | None:
        """Normalize aliases and enforce the required LLM response shape."""
        confidence = LLMReviewStage._normalize_confidence(value.get("confidence"))
        reason = value.get("reason", value.get("rationale", value.get("explanation", "")))
        reason = str(reason).strip() if reason is not None else ""

        if response_contract == "title_filter":
            verdict = LLMReviewStage._coerce_bool(
                value.get("likely_relevant", value.get("relevant", value.get("is_relevant")))
            )
            verdict_key = "likely_relevant"
        else:
            verdict = LLMReviewStage._coerce_bool(
                value.get("relevant", value.get("is_relevant", value.get("likely_relevant")))
            )
            verdict_key = "relevant"

        if verdict is None or confidence is None:
            logger.warning(
                "LLM JSON failed %s contract validation: %s",
                response_contract,
                value,
            )
            return None

        normalized: dict[str, Any] = {
            verdict_key: verdict,
            "confidence": confidence,
            "reason": reason,
        }
        if response_contract != "title_filter":
            for dim in ("methodology", "research_object", "environmental_dimension"):
                if dim in value and value[dim] is not None:
                    normalized[dim] = str(value[dim]).strip()
        return normalized

    @staticmethod
    def _coerce_bool(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "y", "1"}:
                return True
            if normalized in {"false", "no", "n", "0"}:
                return False
        if isinstance(value, int) and value in {0, 1}:
            return bool(value)
        return None

    @staticmethod
    def _normalize_confidence(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if normalized in {"high", "medium", "low"}:
            return normalized
        return None

    @staticmethod
    def _repair_prompt(original_prompt: str, malformed_response: str, response_contract: str) -> str:
        if response_contract == "title_filter":
            schema = '{"likely_relevant": true/false, "confidence": "high"/"medium"/"low", "reason": "brief"}'
        else:
            schema = '{"relevant": true/false, "confidence": "high"/"medium"/"low", "reason": "brief"}'
        return (
            "Repair the malformed LLM JSON response below. "
            "Return ONLY one valid JSON object matching this schema:\n"
            f"{schema}\n\n"
            "Original task prompt:\n"
            f"{original_prompt[:1200]}\n\n"
            "Malformed response:\n"
            f"{malformed_response[:1200]}"
        )

    def _post_openai_chat(self, body: dict[str, Any]) -> dict[str, Any] | None:
        import httpx

        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    self._resolve_url("/chat/completions"),
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            logger.warning("OpenAI API error: %s", e.response.status_code)
            return None
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as e:
            logger.warning("OpenAI API connection error: %s", e)
            return None

    @staticmethod
    def _extract_openai_content(data: dict[str, Any]) -> str:
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            logger.warning(
                "Unexpected OpenAI response structure: %s",
                list(data.keys()),
            )
            raise


    def _resolve_url(self, path: str) -> str:
        """Resolve API URL from base_url or provider default."""
        if self.base_url:
            base = self.base_url.rstrip("/")
            # If base_url already ends with the path, use as-is
            if base.endswith(path):
                return base
            return f"{base}{path}"
        return f"https://api.openai.com/v1{path}"

    def _call_compatible_chat(
        self,
        prompt: str,
        response_contract: str = "abstract_review",
    ) -> dict[str, Any] | None:
        """Call OpenAI-compatible API via httpx (handles chunked encoding)."""
        api_key = self.api_key
        if not api_key:
            logger.warning("No API key for %s — skipping LLM review", self.provider)
            return None

        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 300,
            "response_format": {"type": "json_object"},
        }

        data = self._post_openai_chat(body)
        if data is None:
            return None

        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 200)
        completion_tokens = usage.get("completion_tokens", 100)
        input_rate, output_rate = self._token_rates(self.provider, self.model)
        self._total_cost += prompt_tokens * input_rate / 1_000_000 + completion_tokens * output_rate / 1_000_000

        content = self._extract_openai_content(data)
        parsed = self._parse_json_response(content, response_contract=response_contract)
        if parsed is not None:
            return parsed

        repair_body = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": self._repair_prompt(prompt, content, response_contract),
            }],
            "temperature": 0.0,
            "max_tokens": 200,
            "response_format": {"type": "json_object"},
        }
        repair_data = self._post_openai_chat(repair_body)
        if repair_data is None:
            return None
        repair_usage = repair_data.get("usage", {})
        self._total_cost += repair_usage.get("prompt_tokens", 200) * input_rate / 1_000_000 + repair_usage.get("completion_tokens", 100) * output_rate / 1_000_000
        repair_content = self._extract_openai_content(repair_data)
        return self._parse_json_response(repair_content, response_contract=response_contract)

    # Backwards-compatible test/integration hook; provider dispatch uses the
    # provider-neutral name above.
    def _call_openai(self, prompt: str, response_contract: str = "abstract_review"):
        return self._call_compatible_chat(prompt, response_contract=response_contract)

    def _call_anthropic(
        self,
        prompt: str,
        response_contract: str = "abstract_review",
    ) -> dict[str, Any] | None:
        """Call Anthropic API via httpx."""
        import httpx

        api_key = self.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.warning("No Anthropic API key — skipping LLM review")
            return None

        body = {
            "model": self.model,
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}],
        }

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    "https://api.anthropic.com/v1/messages",
                    json=body,
                    headers={
                        "x-api-key": api_key,
                        "Content-Type": "application/json",
                        "anthropic-version": "2023-06-01",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.ConnectError) as e:
            logger.warning("Anthropic API error: %s", e)
            return None

        content = data["content"][0]["text"]
        return self._parse_json_response(content, response_contract=response_contract)
