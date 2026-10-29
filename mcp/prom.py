"""Read-only Prometheus access for the MCP server: fixed queries, one HTTP GET each.

No tool accepts PromQL or any free-form string; the only parameter is an integer
look-back window, validated here. Pure functions are separated from I/O for testing.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

NS = 'namespace="llm-inference"'
MIN_WINDOW, MAX_WINDOW = 1, 60


def validate_window(minutes: int) -> int:
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise ValueError("window_minutes must be an integer")
    if not MIN_WINDOW <= minutes <= MAX_WINDOW:
        raise ValueError(f"window_minutes must be between {MIN_WINDOW} and {MAX_WINDOW}")
    return minutes


def _quantile(q: float, metric: str, w: int) -> str:
    return f"histogram_quantile({q}, sum by (le) (rate({metric}_bucket{{{NS}}}[{w}m])))"


def query_for(tool: str, window_minutes: int = 5) -> str:
    """Return the fixed PromQL for a tool name."""
    w = validate_window(window_minutes)
    queries = {
        "p95_latency_seconds": _quantile(0.95, "vllm:e2e_request_latency_seconds", w),
        "p95_ttft_seconds": _quantile(0.95, "vllm:time_to_first_token_seconds", w),
        "gpu_utilisation_percent": "avg(DCGM_FI_DEV_GPU_UTIL)",
        "gpu_memory_used_mib": "sum(DCGM_FI_DEV_FB_USED)",
        "gpu_temperature_celsius": "max(DCGM_FI_DEV_GPU_TEMP)",
        "error_rate": (
            f'sum(rate(vllm:request_success_total{{{NS},finished_reason=~"abort|error"}}[{w}m]))'
            f" / sum(rate(vllm:request_success_total{{{NS}}}[{w}m]))"
        ),
        "requests_running": f"sum(vllm:num_requests_running{{{NS}}})",
        "requests_waiting": f"sum(vllm:num_requests_waiting{{{NS}}})",
        "kv_cache_usage_ratio": f"max(vllm:kv_cache_usage_perc{{{NS}}})",
        "vllm_scrape_up": f'min(up{{{NS},job="vllm"}})',
    }
    if tool not in queries:
        raise KeyError(tool)
    return queries[tool]


@dataclass
class Sample:
    value: float | None
    series: int


def parse_instant(payload: dict) -> Sample:
    """Parse a Prometheus instant-query response. NaN and empty results -> value None."""
    if payload.get("status") != "success":
        raise RuntimeError(f"prometheus error: {payload.get('error', 'unknown')}")
    result = payload["data"]["result"]
    if not result:
        return Sample(None, 0)
    raw = result[0]["value"][1]
    value = float(raw)
    if value != value:  # NaN, e.g. a histogram with no observations in the window
        return Sample(None, len(result))
    return Sample(value, len(result))


class Prometheus:
    def __init__(self, base_url: str, log_path: Path, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.log_path = log_path
        self.timeout = timeout

    def _log(self, record: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")

    def call(self, tool: str, window_minutes: int = 5) -> Sample:
        record = {"ts": time.time(), "tool": tool, "window_minutes": window_minutes}
        try:
            expr = query_for(tool, window_minutes)
            record["query"] = expr
            resp = httpx.get(f"{self.base_url}/api/v1/query", params={"query": expr}, timeout=self.timeout)
            resp.raise_for_status()
            sample = parse_instant(resp.json())
            record.update(value=sample.value, series=sample.series)
            return sample
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._log(record)
