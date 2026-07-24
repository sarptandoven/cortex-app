from __future__ import annotations

import re
import math
import unicodedata
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from .domain import Candidate, ComparisonOutcome, EvaluationPrompt, HeldOutProfile, JudgeDecision


class ObservableFeature(str, Enum):
    MAX_WORDS = "max_words"
    MAX_SENTENCE_WORDS = "max_sentence_words"
    MAX_EXCLAMATIONS = "max_exclamations"
    PROHIBITED_PHRASE = "prohibited_phrase"
    REQUIRED_PHRASE = "required_phrase"
    LOWERCASE_RATIO_MIN = "lowercase_ratio_min"
    TASK_TERMS = "task_terms"


@dataclass(frozen=True)
class ObservableRule:
    feature: ObservableFeature
    value: float | str | tuple[str, ...]
    weight: float = 1.0
    hard_constraint: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "feature", ObservableFeature(self.feature))
        if (
            isinstance(self.weight, bool)
            or not isinstance(self.weight, (int, float))
            or not math.isfinite(float(self.weight))
            or self.weight <= 0
        ):
            raise ValueError("observable rule weights must be finite and positive")
        if self.feature in {
            ObservableFeature.MAX_WORDS,
            ObservableFeature.MAX_SENTENCE_WORDS,
        }:
            if (
                isinstance(self.value, bool)
                or not isinstance(self.value, (int, float))
                or not math.isfinite(float(self.value))
                or float(self.value) <= 0
            ):
                raise ValueError(f"{self.feature.value} requires a finite positive limit")
        elif self.feature is ObservableFeature.MAX_EXCLAMATIONS:
            if (
                isinstance(self.value, bool)
                or not isinstance(self.value, (int, float))
                or not math.isfinite(float(self.value))
                or float(self.value) < 0
                or not float(self.value).is_integer()
            ):
                raise ValueError("max_exclamations requires a non-negative integer limit")
        elif self.feature is ObservableFeature.LOWERCASE_RATIO_MIN:
            if (
                isinstance(self.value, bool)
                or not isinstance(self.value, (int, float))
                or not math.isfinite(float(self.value))
                or not 0 <= float(self.value) <= 1
            ):
                raise ValueError("lowercase_ratio_min must be between 0 and 1")
        elif self.feature in {
            ObservableFeature.PROHIBITED_PHRASE,
            ObservableFeature.REQUIRED_PHRASE,
        }:
            if not isinstance(self.value, str) or not self.value.strip():
                raise ValueError(f"{self.feature.value} requires a non-empty phrase")
        elif self.feature is ObservableFeature.TASK_TERMS:
            values = self.value if isinstance(self.value, tuple) else (self.value,)
            if not values or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise ValueError("task_terms requires one or more non-empty strings")


@dataclass(frozen=True)
class ObservableRubric:
    prompt_id: str
    cited_memory_ids: tuple[str, ...]
    rules: tuple[ObservableRule, ...]
    tie_epsilon: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "cited_memory_ids", tuple(self.cited_memory_ids))
        object.__setattr__(self, "rules", tuple(self.rules))
        if not self.prompt_id.strip():
            raise ValueError("rubric prompt_id is required")
        if (
            isinstance(self.tie_epsilon, bool)
            or not isinstance(self.tie_epsilon, (int, float))
            or not math.isfinite(float(self.tie_epsilon))
            or self.tie_epsilon < 0
        ):
            raise ValueError("tie_epsilon must be finite and non-negative")
        if len(self.cited_memory_ids) != len(set(self.cited_memory_ids)):
            raise ValueError("rubric cited_memory_ids must be unique")


def _words(text: str) -> list[str]:
    tokens = re.findall(r"[^\W_]+(?:['’][^\W_]+)*", _normalized_text(text))
    words: list[str] = []
    for token in tokens:
        buffered = ""
        for character in token:
            if _uses_character_boundaries(character):
                if buffered:
                    words.append(buffered)
                    buffered = ""
                words.append(character)
            else:
                buffered += character
        if buffered:
            words.append(buffered)
    return words


def _uses_character_boundaries(character: str) -> bool:
    """Conservative fallback for scripts without reliable whitespace boundaries."""
    codepoint = ord(character)
    return any(
        start <= codepoint <= end
        for start, end in (
            (0x0E00, 0x0E7F),  # Thai
            (0x0E80, 0x0EFF),  # Lao
            (0x1000, 0x109F),  # Myanmar
            (0x1780, 0x17FF),  # Khmer
            (0x3040, 0x30FF),  # Hiragana and Katakana
            (0x3400, 0x9FFF),  # CJK ideographs
            (0xAC00, 0xD7AF),  # Hangul syllables
        )
    )


def _normalized_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Cf"
    )


def _feature_value(text: str, rule: ObservableRule) -> float:
    normalized = _normalized_text(text)
    lowered = normalized.casefold()
    if rule.feature is ObservableFeature.MAX_WORDS:
        limit = float(rule.value)
        count = len(_words(text))
        return 1.0 if count <= limit else -min(1.0, (count - limit) / max(limit, 1.0))
    if rule.feature is ObservableFeature.MAX_SENTENCE_WORDS:
        limit = float(rule.value)
        sentences = [part for part in re.split(r"[.!?]+", text) if part.strip()]
        longest = max((len(_words(sentence)) for sentence in sentences), default=0)
        return 1.0 if longest <= limit else -min(1.0, (longest - limit) / max(limit, 1.0))
    if rule.feature is ObservableFeature.MAX_EXCLAMATIONS:
        limit = int(float(rule.value))
        return 1.0 if normalized.count("!") <= limit else -1.0
    if rule.feature is ObservableFeature.PROHIBITED_PHRASE:
        return 1.0 if str(rule.value).casefold() not in lowered else -1.0
    if rule.feature is ObservableFeature.REQUIRED_PHRASE:
        return 1.0 if str(rule.value).casefold() in lowered else -1.0
    if rule.feature is ObservableFeature.LOWERCASE_RATIO_MIN:
        letters = [character for character in text if character.isalpha()]
        ratio = (
            sum(1 for character in letters if character.islower()) / len(letters)
            if letters
            else 0.0
        )
        return 1.0 if ratio >= float(rule.value) else -1.0
    if rule.feature is ObservableFeature.TASK_TERMS:
        raw_terms = rule.value if isinstance(rule.value, tuple) else (str(rule.value),)
        terms = tuple(term.casefold() for term in raw_terms)
        if not terms:
            return 0.0
        coverage = sum(1 for term in terms if term in lowered) / len(terms)
        return 2.0 * coverage - 1.0
    raise ValueError(f"unsupported observable feature: {rule.feature}")


def observable_utility(text: str, rubric: ObservableRubric) -> tuple[float, tuple[str, ...]]:
    score = 0.0
    violations: list[str] = []
    for rule in rubric.rules:
        value = _feature_value(text, rule)
        if rule.hard_constraint and value < 0:
            score -= 10.0 * rule.weight
            violations.append(rule.feature.value)
        else:
            score += rule.weight * value
    return score, tuple(violations)


@dataclass(frozen=True)
class ObservableFeatureJudge:
    """Offline oracle based only on frozen text and cited observable rules."""

    rubrics: Mapping[str, ObservableRubric]
    judge_id: str = "observable_feature_oracle_v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "rubrics", MappingProxyType(dict(self.rubrics)))

    def reproducibility_config(self) -> Mapping[str, object]:
        return {"rubrics": self.rubrics}

    def judge(
        self,
        prompt: EvaluationPrompt,
        profile: HeldOutProfile,
        left: Candidate,
        right: Candidate,
        *,
        seed: int,
    ) -> JudgeDecision:
        del seed
        rubric = self.rubrics.get(prompt.prompt_id)
        if rubric is None:
            return JudgeDecision(
                ComparisonOutcome.ABSTAIN,
                rationale="no observable rubric for prompt",
            )
        profile_by_id = {item.memory_id: item for item in profile.items}
        cited_items = [profile_by_id.get(memory_id) for memory_id in rubric.cited_memory_ids]
        eligible = [
            item
            for item in cited_items
            if item is not None
            and item.author_class == "user"
            and item.status == "active"
            and item.trust_score > 0
        ]
        if not rubric.rules or len(eligible) != len(rubric.cited_memory_ids):
            return JudgeDecision(
                ComparisonOutcome.ABSTAIN,
                rationale="insufficient active owner-authored profile evidence",
                cited_memory_ids=tuple(item.memory_id for item in eligible),
            )

        left_score, left_violations = observable_utility(left.text, rubric)
        right_score, right_violations = observable_utility(right.text, rubric)
        delta = left_score - right_score
        if left_violations and right_violations:
            outcome = ComparisonOutcome.BOTH_BAD
        elif left_violations:
            outcome = ComparisonOutcome.RIGHT
        elif right_violations:
            outcome = ComparisonOutcome.LEFT
        elif delta > rubric.tie_epsilon:
            outcome = ComparisonOutcome.LEFT
        elif delta < -rubric.tie_epsilon:
            outcome = ComparisonOutcome.RIGHT
        else:
            outcome = ComparisonOutcome.TIE
        return JudgeDecision(
            outcome=outcome,
            rationale=(
                f"observable utility left={left_score:.4f} right={right_score:.4f}; "
                f"left_violations={left_violations}; right_violations={right_violations}"
            ),
            cited_memory_ids=tuple(item.memory_id for item in eligible),
            confidence=1.0,
            metadata={
                "left_utility": left_score,
                "right_utility": right_score,
                "left_hard_violations": left_violations,
                "right_hard_violations": right_violations,
            },
        )
