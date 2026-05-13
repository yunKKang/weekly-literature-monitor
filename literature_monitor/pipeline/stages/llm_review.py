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
import re
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
        provider: str = "openai",
        model: str = "gpt-4o-mini",
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
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL", "")
        self.two_stage = two_stage
        self.concurrency = concurrency
        self._stage1_calls = 0
        self._stage2_calls = 0
        self._calls_made = 0
        self._total_cost = 0.0

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

        # Stage 1: Title-only fast filter (cheap)
        if not state.title or len(state.title) < 10:
            return state

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
            return self._call_api(prompt)
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
        return self._call_api(prompt)

    def _call_api(self, prompt: str) -> dict[str, Any] | None:
        """Dispatch to the appropriate LLM API provider."""
        try:
            if self.provider == "openai":
                return self._call_openai(prompt)
            elif self.provider == "anthropic":
                return self._call_anthropic(prompt)
            else:
                logger.warning("Unknown LLM provider: %s", self.provider)
                return None
        except Exception as e:
            logger.warning("LLM API call failed: %s", e)
            return None

    @staticmethod
    def _parse_json_response(content: str) -> dict[str, Any] | None:
        """Parse JSON from LLM response, stripping <think>...</think> tags if present."""
        # Strip DeepSeek/R1 thinking tags
        content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL)
        # Strip markdown code fences
        content = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.MULTILINE)
        content = re.sub(r"```\s*$", "", content.strip(), flags=re.MULTILINE)
        content = content.strip()
        if not content:
            return None
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.warning("LLM returned non-JSON: %s", content[:150])
            return None

    def _resolve_url(self, path: str) -> str:
        """Resolve API URL from base_url or provider default."""
        if self.base_url:
            base = self.base_url.rstrip("/")
            # If base_url already ends with the path, use as-is
            if base.endswith(path):
                return base
            return f"{base}{path}"
        return f"https://api.openai.com/v1{path}"

    def _call_openai(self, prompt: str) -> dict[str, Any] | None:
        """Call OpenAI-compatible API via httpx (handles chunked encoding)."""
        import httpx

        api_key = self.api_key
        if not api_key:
            logger.warning("No OpenAI API key — skipping LLM review")
            return None

        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 300,
        }

        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    self._resolve_url("/chat/completions"),
                    json=body,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.warning("OpenAI API error: %s", e.response.status_code)
            return None
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as e:
            logger.warning("OpenAI API connection error: %s", e)
            return None

        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 200)
        completion_tokens = usage.get("completion_tokens", 100)
        self._total_cost += (
            prompt_tokens * 0.15 / 1_000_000
            + completion_tokens * 0.60 / 1_000_000
        )

        content = data["choices"][0]["message"]["content"]
        return self._parse_json_response(content)

    def _call_anthropic(self, prompt: str) -> dict[str, Any] | None:
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
        return self._parse_json_response(content)
