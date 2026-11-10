from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class MappingCandidate:
    internal_hotel_name: str
    platform_hotel_name: str
    score: float


def basic_name_similarity(left: str, right: str) -> float:
    left_tokens = set(left.lower().split())
    right_tokens = set(right.lower().split())
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return overlap / union
