"""Read-only MCP server exposing a few fixed Prometheus queries about the vLLM service.

Run over stdio:  python mcp/server.py
Env: PROMETHEUS_URL (default http://localhost:9090), MCP_TOOL_LOG (default logs/mcp-tool-calls.jsonl)
Every tool call is appended to the log as one JSON line.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server import MCPServer  # noqa: E402  (the installed SDK, not this folder)
from prom import Prometheus  # noqa: E402

prom = Prometheus(
    os.environ.get("PROMETHEUS_URL", "http://localhost:9090"),
    Path(os.environ.get("MCP_TOOL_LOG", "logs/mcp-tool-calls.jsonl")),
)
server = MCPServer("prometheus-readonly")


def _fmt(name: str, unit: str, window: int | None = None, *, tool: str, window_arg: int = 5) -> str:
    sample = prom.call(tool, window_arg)
    where = f" over the last {window} min" if window else ""
    if sample.value is None:
        return f"{name}: no data{where} (no matching series or no observations in the window)"
    return f"{name}{where}: {sample.value:.4g} {unit}".rstrip()


@server.tool()
def get_p95_latency(window_minutes: int = 5) -> str:
    """p95 end-to-end request latency of the vLLM service in seconds over a look-back window (1-60 min)."""
    return _fmt("p95 e2e latency", "s", window_minutes, tool="p95_latency_seconds", window_arg=window_minutes)


@server.tool()
def get_p95_time_to_first_token(window_minutes: int = 5) -> str:
    """p95 time to first token of the vLLM service in seconds over a look-back window (1-60 min)."""
    return _fmt("p95 TTFT", "s", window_minutes, tool="p95_ttft_seconds", window_arg=window_minutes)


@server.tool()
def get_error_rate(window_minutes: int = 5) -> str:
    """Fraction of finished vLLM requests that ended in abort or error over a look-back window (1-60 min)."""
    return _fmt("error rate (aborted/errored share of finished requests)", "", window_minutes,
                tool="error_rate", window_arg=window_minutes)


@server.tool()
def get_gpu_utilisation() -> str:
    """Current GPU utilisation percent (DCGM)."""
    return _fmt("GPU utilisation", "%", tool="gpu_utilisation_percent")


@server.tool()
def get_gpu_memory_used() -> str:
    """Current GPU framebuffer memory used in MiB (DCGM)."""
    return _fmt("GPU memory used", "MiB", tool="gpu_memory_used_mib")


@server.tool()
def get_gpu_temperature() -> str:
    """Current GPU temperature in degrees Celsius (DCGM)."""
    return _fmt("GPU temperature", "degC", tool="gpu_temperature_celsius")


@server.tool()
def get_queue_state() -> str:
    """Current number of running and waiting (queued) requests and KV-cache usage of the vLLM service."""
    parts = [
        _fmt("running requests", "", tool="requests_running"),
        _fmt("waiting requests", "", tool="requests_waiting"),
        _fmt("KV cache usage (0-1)", "", tool="kv_cache_usage_ratio"),
    ]
    return "; ".join(parts)


@server.tool()
def get_service_scrape_status() -> str:
    """Whether Prometheus can currently scrape the vLLM metrics endpoint (1 = up, 0 = down)."""
    return _fmt("vLLM scrape up", "", tool="vllm_scrape_up")


if __name__ == "__main__":
    server.run("stdio")
