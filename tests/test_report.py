"""The HTML report, as ADR 0010 constrains it.

The assertions are about structure and about what must never appear. Nothing here asserts on
prose: a report whose wording is pinned by a test is a report nobody will improve.
"""

from datetime import UTC, date, datetime

import pytest

from lakmus.report import MAX_FAILURES, CaseFailure, render
from lakmus.results.models import (
    Cost,
    GroupSummary,
    Latency,
    MetricSummary,
    RunConfig,
    RunResult,
    SuiteRef,
    TargetFingerprint,
    ThresholdApplied,
    ToolInfo,
)

RUN_ID = "01JC" + "0" * 22
MOMENT = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)


def _summary(**overrides: object) -> MetricSummary:
    fields: dict[str, object] = {
        "value": 0.94,
        "ci95": (0.91, 0.97),
        "n_cases": 100,
        "n_observations": 100,
        "n_errors": 0,
        "deterministic": True,
        "threshold": ThresholdApplied(min=0.9),
        "passed": True,
        "conclusive": True,
    }
    fields.update(overrides)
    return MetricSummary.model_validate(fields)


def _run(**overrides: object) -> RunResult:
    fields: dict[str, object] = {
        "lakmus_format": "0.1",
        "run_id": RUN_ID,
        "created_at": MOMENT,
        "tool": ToolInfo(name="lakmus", version="0.1.0", python="3.13.0"),
        "suite": SuiteRef(
            id="rh/equite-tri-cv-fr",
            version="1.2.0",
            inputs_sha256="a" * 64,
            content_sha256="b" * 64,
        ),
        "target": TargetFingerprint.create(
            adapter="openai",
            kind="model",
            identity="gpt-x",
            declared_version="gpt-x-2026-05",
            parameters={"temperature": 0.0},
        ),
        "config": RunConfig(
            repeats=1,
            temperature=0.0,
            seed=42,
            timeout_s=60,
            concurrency=8,
            overrides={"temperature": 0.7},
        ),
        "summary": {"accuracy": _summary()},
        "latency_ms": Latency(p50=800, p95=1600),
    }
    fields.update(overrides)
    return RunResult.model_validate(fields)


def test_the_report_carries_no_script_and_reaches_no_network() -> None:
    html = render(_run())

    assert "<script" not in html.lower()
    assert "http://" not in html
    assert "https://" not in html
    assert "<!doctype html>" in html.lower()


def test_the_report_states_its_own_dark_variant() -> None:
    """No JavaScript means the only way to follow the reader's theme is a media query."""
    assert "prefers-color-scheme" in render(_run())


def test_a_run_whose_thresholds_held_reads_as_passed() -> None:
    html = render(_run())

    assert "PASS" in html
    assert "FAIL" not in html


def test_a_failed_threshold_reads_as_failed() -> None:
    html = render(_run(summary={"accuracy": _summary(value=0.5, passed=False, ci95=(0.4, 0.6))}))

    assert "FAIL" in html


def test_an_interrupted_run_says_so_before_any_number() -> None:
    html = render(_run(status="interrupted"))

    assert "interrupted" in html.lower()
    assert "not comparable" in html.lower()
    assert html.lower().index("interrupted") < html.index("accuracy")


def test_a_metric_with_no_value_is_not_reported_as_zero() -> None:
    html = render(
        _run(
            summary={
                "accuracy": _summary(
                    value=None,
                    ci95=None,
                    ci_unavailable_reason="no case produced a score",
                    n_cases=0,
                    n_observations=0,
                    n_errors=12,
                    passed=False,
                )
            }
        )
    )

    assert "0.00" not in html.split("accuracy")[1].split("</tr>")[0]
    assert "no case produced a score" in html


def test_an_inconclusive_pass_is_marked_as_one() -> None:
    """Asserted on the row's own note: the template explains the word in static prose too, so
    `"inconclusive" in html` was true of every report ever rendered."""
    inconclusive = render(_run(summary={"accuracy": _summary(ci95=(0.88, 0.97), conclusive=False)}))
    conclusive = render(_run())

    assert "(inconclusive)" in inconclusive
    assert "(inconclusive)" not in conclusive


def test_a_run_with_no_price_says_so_rather_than_showing_zero() -> None:
    html = render(_run(cost=Cost(tokens_in=4000, tokens_out=120)))

    assert "4000" in html
    assert "$0.00" not in html


def test_a_priced_run_carries_the_date_and_source_of_the_price() -> None:
    html = render(
        _run(
            cost=Cost(
                usd=1.25,
                pricing_as_of=date(2026, 10, 1),
                pricing_source="https-free provenance",
                tokens_in=10,
                tokens_out=2,
            )
        )
    )

    assert "1.25" in html
    assert "2026-10-01" in html


def test_the_fingerprint_and_both_suite_digests_are_in_the_provenance() -> None:
    run = _run()
    html = render(run)

    assert run.target.sha256 in html
    assert run.suite.inputs_sha256 in html
    assert run.suite.content_sha256 in html
    assert "0.7" in html, "an override must be visible, with its value"


def test_the_breakdown_is_rendered_one_group_field_at_a_time() -> None:
    html = render(
        _run(
            by_group={
                "accuracy": {
                    "variable": {
                        "nom": GroupSummary(value=0.9, ci95=(0.8, 1.0), n_cases=50),
                        "âge": GroupSummary(value=0.7, ci95=(0.6, 0.8), n_cases=50),
                    }
                }
            }
        )
    )

    assert "variable" in html
    assert "âge" in html


def test_markup_in_a_case_id_is_escaped_not_rendered() -> None:
    html = render(
        _run(),
        failures=[
            CaseFailure(
                case_id="<img src=x onerror=alert(1)>",
                metric_id="accuracy",
                reason="the target refused",
                kind="bad_request",
            )
        ],
    )

    assert "<img" not in html
    assert "&lt;img" in html


def test_the_failure_table_is_capped_and_says_how_many_it_left_out() -> None:
    failures = [
        CaseFailure(case_id=f"c-{index}", metric_id="accuracy", reason="refused", kind="timeout")
        for index in range(MAX_FAILURES + 7)
    ]

    html = render(_run(), failures=failures)

    assert f"c-{MAX_FAILURES - 1}" in html
    assert f"c-{MAX_FAILURES}" not in html
    # The count in words. A bare `"7" in html` was satisfied by the fixture's own `0.7` override.
    assert "7 more not shown" in html


def test_failures_are_counted_by_kind_over_all_of_them_not_only_the_shown_ones() -> None:
    failures = [
        CaseFailure(case_id=f"t-{index}", metric_id="accuracy", reason="no answer", kind="timeout")
        for index in range(MAX_FAILURES + 5)
    ] + [CaseFailure(case_id="r-0", metric_id="accuracy", reason="429", kind="rate_limited")]

    html = render(_run(), failures=failures)

    assert f"{MAX_FAILURES + 5}" in html
    assert "rate_limited" in html


@pytest.mark.parametrize("value", ["--out", "&", "é"])
def test_rendering_is_total_over_odd_suite_identifiers(value: str) -> None:
    run = _run(
        suite=SuiteRef(id=value, version="1.0.0", inputs_sha256="c" * 64, content_sha256="d" * 64)
    )

    assert render(run)


def test_a_run_that_measured_nothing_does_not_read_as_passed() -> None:
    """A suite with no threshold has nothing to fail, which is not the same as having passed."""
    html = render(
        _run(
            summary={
                "accuracy": _summary(
                    value=None,
                    ci95=None,
                    ci_unavailable_reason="no case produced a score",
                    n_cases=0,
                    n_observations=0,
                    n_errors=500,
                    threshold=None,
                    passed=None,
                    conclusive=None,
                )
            }
        )
    )

    assert "NO RESULT" in html
    assert "PASS" not in html
    assert "500" in html


def test_the_report_links_to_nothing_and_embeds_nothing() -> None:
    """Asserted on the attributes rather than on the absence of a URL: a real `pricing.toml`
    entry puts its source in the report as inert text, and that is not a network call."""
    html = render(_run())

    assert "src=" not in html
    assert "href=" not in html
    assert "@import" not in html
    assert "url(" not in html
