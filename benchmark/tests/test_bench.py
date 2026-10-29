import pytest

from benchmark.bench import (
    consume_stream,
    nearest_rank_percentile,
    parse_sse_data,
    summarize_requests,
)


def test_parse_sse_data_ignores_non_data_lines_and_stops_at_done():
    lines = [
        "event: message",
        "",
        'data: {"choices":[{"delta":{"content":"hi"}}]}',
        "data: [DONE]",
        'data: {"choices":[{"delta":{"content":"ignored"}}]}',
    ]

    assert list(parse_sse_data(lines)) == [
        {"choices": [{"delta": {"content": "hi"}}]}
    ]


def test_consume_stream_reads_first_content_and_completion_usage(monkeypatch):
    monkeypatch.setattr("benchmark.bench.time.monotonic", lambda: 5.25)
    lines = [
        'data: {"choices":[{"delta":{"role":"assistant"}}]}',
        'data: {"choices":[{"delta":{"content":"hello"}}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":4}}',
        "data: [DONE]",
    ]

    assert consume_stream(lines, request_started=5.0) == (0.25, 4)


def test_consume_stream_allows_missing_usage():
    lines = ['data: {"choices":[{"delta":{"content":"hello"}}]}', "data: [DONE]"]

    ttft, completion_tokens = consume_stream(lines, request_started=0.0)

    assert ttft is not None
    assert completion_tokens is None


def test_parse_sse_data_rejects_non_object_payload():
    with pytest.raises(ValueError, match="JSON object"):
        list(parse_sse_data(["data: []"]))


def test_nearest_rank_percentile_uses_nearest_rank():
    assert nearest_rank_percentile([5.0, 1.0, 4.0, 2.0, 3.0], 0.95) == 5.0
    assert nearest_rank_percentile([], 0.95) is None


def test_nearest_rank_percentile_rejects_invalid_percentile():
    with pytest.raises(ValueError, match="percentile"):
        nearest_rank_percentile([1.0], 0)


def test_summary_reports_failures_and_successful_latency_statistics():
    records = [
        {
            "success": True,
            "ttft_seconds": 0.2,
            "end_to_end_seconds": 1.2,
            "completion_tokens": 10,
            "tokens_per_second": 10.0,
        },
        {
            "success": True,
            "ttft_seconds": 0.4,
            "end_to_end_seconds": 1.8,
            "completion_tokens": None,
            "tokens_per_second": None,
        },
        {
            "success": False,
            "ttft_seconds": None,
            "end_to_end_seconds": 0.1,
            "completion_tokens": None,
            "tokens_per_second": None,
        },
    ]

    summary = summarize_requests(records, concurrency=2, peak_vram_mib=9000)

    assert summary["request_count"] == 3
    assert summary["success_count"] == 2
    assert summary["failure_count"] == 1
    assert summary["failure_rate"] == pytest.approx(1 / 3)
    assert summary["ttft_p50_seconds"] == pytest.approx(0.3)
    assert summary["end_to_end_p50_seconds"] == pytest.approx(1.5)
    assert summary["tokens_per_second_p50"] == 10.0
    assert summary["token_count_unavailable_count"] == 1
    assert summary["peak_vram_mib"] == 9000
