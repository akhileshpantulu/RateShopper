"""Contract with the google-hotels library's internals (docs/DESIGN.md, finding F2).

The rate-shop collector replaces the library's transport and drives its plans directly. These are internal modules,
so this test pins exactly what the collector relies on. If it fails after an upgrade, read the library's changes
before updating the pin in pyproject.toml.
"""

from __future__ import annotations

import inspect

import google_hotels
from google_hotels import plans, runner
from google_hotels._protocol import batchexecute, constants
from google_hotels.arguments import filters_of, reference_of
from google_hotels.options import SearchFilters, Stay, make_stay


def test_pinned_version() -> None:
    assert google_hotels.__version__ == "0.1.0"


def test_plans_used_by_the_collector() -> None:
    expected = {
        "search": ["query", "stay", "filters", "locale", "max_results", "with_offers"],
        "hotel": ["reference", "stay", "filters", "locale"],
        "price_calendar": ["token", "stay", "days"],
        "_resolve": ["reference", "stay", "filters"],
    }
    for name, parameters in expected.items():
        assert list(inspect.signature(getattr(plans, name)).parameters) == parameters, name
    assert plans.Locale("en", "gb").country == "gb"


def test_runner_drives_any_transport_with_a_call_method() -> None:
    assert list(inspect.signature(runner.run_async).parameters) == ["plan", "transport"]


def test_protocol_pieces() -> None:
    assert callable(batchexecute.encode_call)
    assert callable(batchexecute.read_payload)
    for name in (
        "BATCHEXECUTE_URL",
        "RPC_SOURCE_PATH",
        "REQUEST_HEADERS",
        "REQUEST_TIMEOUT_SECONDS",
        "ATTEMPT_DEADLINE_SECONDS",
        "MAX_CALENDAR_DAYS",
    ):
        assert hasattr(constants, name), name


def test_argument_helpers() -> None:
    assert reference_of("ChUIqbjvp9r_u_oVGgkvbS8wMjIyMGQQAQ").property_token is not None
    assert isinstance(filters_of(), SearchFilters)
    parameters = list(inspect.signature(make_stay).parameters)
    assert parameters == ["check_in", "check_out", "nights", "adults", "children_ages", "currency", "today"]
    assert "nights" in dir(Stay)
