import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import prom  # noqa: E402


def test_window_validation():
    assert prom.validate_window(5) == 5
    for bad in (0, 61, -1, "5", 2.5, True):
        with pytest.raises(ValueError):
            prom.validate_window(bad)


def test_query_for_is_fixed_and_includes_window():
    q = prom.query_for("p95_latency_seconds", 10)
    assert "histogram_quantile(0.95" in q and "[10m]" in q
    assert 'namespace="llm-inference"' in q


def test_unknown_tool_rejected():
    with pytest.raises(KeyError):
        prom.query_for("up; drop everything")


def test_parse_instant_value():
    payload = {"status": "success", "data": {"result": [{"value": [1.0, "0.758"]}]}}
    assert prom.parse_instant(payload) == prom.Sample(0.758, 1)


def test_parse_instant_empty_and_nan():
    assert prom.parse_instant({"status": "success", "data": {"result": []}}).value is None
    nan = {"status": "success", "data": {"result": [{"value": [1.0, "NaN"]}]}}
    assert prom.parse_instant(nan).value is None


def test_parse_instant_error():
    with pytest.raises(RuntimeError):
        prom.parse_instant({"status": "error", "error": "bad query"})


def test_call_logs_success_and_failure(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    client = prom.Prometheus("http://prom.invalid", log)

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"status": "success", "data": {"result": [{"value": [1, "42"]}]}}

    monkeypatch.setattr(prom.httpx, "get", lambda *a, **k: Resp())
    assert client.call("gpu_utilisation_percent").value == 42.0

    with pytest.raises(ValueError):
        client.call("p95_latency_seconds", 999)

    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert records[0]["tool"] == "gpu_utilisation_percent" and records[0]["value"] == 42.0
    assert "error" in records[1]
