"""Token counters and usage helpers: six nullable fields, unknown stays ``None``, never zero-filled."""

from coding_agent_cli import Outcome, Result, Usage
from coding_agent_cli._drivers._common import as_int, read_usage
from coding_agent_cli._drivers._usage import TokenCounters


def test_new_counters_are_all_unknown() -> None:
    assert TokenCounters().usage() == Usage()


def test_responses_sum_and_a_repeated_response_id_replaces_its_earlier_values() -> None:
    counters = TokenCounters()
    counters.record_response("a", input=10, output=1)
    counters.record_response("a", input=12, output=3)
    counters.record_response("b", input=5, output=2, cache_read=4)

    assert counters.usage() == Usage(input=17, output=5, cache_read=4)


def test_reported_zero_is_kept_and_unreported_field_stays_null() -> None:
    counters = TokenCounters()
    counters.record_response("a", input=0, output=0)

    assert counters.usage().input == 0
    assert counters.usage().cache_write is None


def test_backend_totals_override_summed_responses_per_field() -> None:
    counters = TokenCounters()
    counters.record_response("a", input=10, output=1, cache_read=3)
    counters.record_totals(input=40, output=9)

    assert counters.usage() == Usage(input=40, output=9, cache_read=3)


def test_context_keeps_latest_value_and_none_leaves_it_unchanged() -> None:
    counters = TokenCounters()
    counters.record_context(used=100, window=1000)
    counters.record_context(used=250)
    assert counters.usage() == Usage(context_used=250, context_window=1000)

    counters.clear_context()
    assert counters.usage() == Usage(context_window=1000)


def test_invalid_values_are_ignored() -> None:
    counters = TokenCounters()
    counters.record_response("a", input=-1, output=True, cache_read="7", cache_write=2.5)
    counters.record_context(used=-5, window=float("nan"))

    assert counters.usage() == Usage()


def test_read_usage_normalizes_input_and_keeps_malformed_components_visible() -> None:
    usage = read_usage(
        {
            "input_tokens": 10,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 3,
            "output_tokens": 4,
        }
    )
    assert (usage.input, usage.output, usage.cache_read, usage.cache_write, usage.observed) == (33, 4, 20, 3, True)

    no_input = read_usage({"cache_read_input_tokens": 5})
    assert no_input.input is None
    assert no_input.cache_read == 5
    assert no_input.observed is True

    malformed = read_usage({"input_tokens": "x", "output_tokens": 2})
    assert malformed.input is None
    assert malformed.output == 2
    assert malformed.observed is True

    assert read_usage({}).observed is False


def test_as_int_rejects_bool_negative_and_non_int() -> None:
    assert as_int(3) == 3
    assert as_int(True) is None
    assert as_int(-1) is None
    assert as_int("3") is None


def test_result_usage_defaults_to_all_unknown() -> None:
    assert Result(outcome=Outcome.SUCCESS).usage == Usage()
