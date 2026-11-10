"""Typed outcomes of a collector call (docs/DESIGN.md, section 4).

Code outside a collector sees only these, never the source library's own exceptions.
"""

from __future__ import annotations


class CollectorError(Exception):
    """Base class of every collector failure."""


class Blocked(CollectorError):
    """The source refused us (rate limit or unusual-traffic page) on every attempt."""


class NotFound(CollectorError):
    """The source has no hotel matching the reference given."""


class ParseError(CollectorError):
    """The source answered, but not in the shape the parser expects. Carries the raw answer."""

    def __init__(self, message: str, raw: str | None = None) -> None:
        super().__init__(message)
        self.raw = raw


class SourceError(CollectorError):
    """Any other failure: a refused request, an unexpected HTTP status, network trouble."""
