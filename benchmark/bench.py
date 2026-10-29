#!/usr/bin/env python3
"""Small streaming benchmark for local OpenAI-compatible chat endpoints."""

from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import requests


PROMPTS_PATH = Path(__file__).with_name("prompts.json")
CSV_FIELDS = [
    "request_id",
    "concurrency",
    "prompt_id",
    "started_at_utc",
    "success",
    "status_code",
    "ttft_seconds",
    "end_to_end_seconds",
    "completion_tokens",
    "tokens_per_second",
    "measurement_error",
    "error",
]


def parse_sse_data(lines: Iterable[str | bytes]) -> Iterator[dict[str, Any]]:
    """Yield JSON payloads from OpenAI-style SSE data lines."""
    for raw_line in lines:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            return
        if data:
            payload = json.loads(data)
            if not isinstance(payload, dict):
                raise ValueError("SSE data payload must be a JSON object")
            yield payload


def consume_stream(
    lines: Iterable[str | bytes], request_started: float
) -> tuple[float | None, int | None]:
    """Return TTFT and reported completion-token count from an SSE stream."""
    ttft_seconds = None
    completion_tokens = None

    for payload in parse_sse_data(lines):
        choices = payload.get("choices", [])
        if not isinstance(choices, list):
            raise ValueError("SSE choices field must be a list")
        for choice in choices:
            if not isinstance(choice, dict):
                raise ValueError("each SSE choice must be an object")
            delta = choice.get("delta", {})
            if not isinstance(delta, dict):
                continue
            content = delta.get("content")
            if isinstance(content, str) and content and ttft_seconds is None:
                ttft_seconds = time.monotonic() - request_started

        usage = payload.get("usage")
        if usage is not None:
            if not isinstance(usage, dict):
                raise ValueError("SSE usage field must be an object")
            token_count = usage.get("completion_tokens")
            if token_count is not None:
                if not isinstance(token_count, int) or token_count < 0:
                    raise ValueError("completion_tokens must be a non-negative integer")
                completion_tokens = token_count

    return ttft_seconds, completion_tokens


def nearest_rank_percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    if not 0 < percentile <= 1:
        raise ValueError("percentile must be in the range (0, 1]")
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered)) - 1]


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def summarize_requests(
    records: list[dict[str, Any]], concurrency: int, peak_vram_mib: int
) -> dict[str, Any]:
    successful = [record for record in records if record["success"]]
    latencies = [record["end_to_end_seconds"] for record in successful]
    ttfts = [
        record["ttft_seconds"]
        for record in successful
        if record["ttft_seconds"] is not None
    ]
    throughputs = [
        record["tokens_per_second"]
        for record in successful
        if record["tokens_per_second"] is not None
    ]
    failures = len(records) - len(successful)
    return {
        "concurrency": concurrency,
        "request_count": len(records),
        "success_count": len(successful),
        "failure_count": failures,
        "failure_rate": failures / len(records) if records else 0.0,
        "ttft_p50_seconds": median(ttfts),
        "ttft_p95_seconds": nearest_rank_percentile(ttfts, 0.95),
        "tokens_per_second_p50": median(throughputs),
        "tokens_per_second_p95": nearest_rank_percentile(throughputs, 0.95),
        "end_to_end_p50_seconds": median(latencies),
        "end_to_end_p95_seconds": nearest_rank_percentile(latencies, 0.95),
        "token_count_unavailable_count": sum(
            record["success"] and record["completion_tokens"] is None
            for record in records
        ),
        "peak_vram_mib": peak_vram_mib,
    }


def gpu_info() -> list[dict[str, str]]:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    devices = []
    for row in csv.reader(result.stdout.splitlines(), skipinitialspace=True):
        if len(row) != 3:
            raise ValueError(f"Unexpected nvidia-smi GPU row: {row!r}")
        devices.append(
            {
                "name": row[0],
                "memory_total": row[1],
                "driver_version": row[2],
            }
        )
    if not devices:
        raise ValueError("nvidia-smi reported no GPUs")
    return devices


class GpuMemorySampler:
    def __init__(self, interval_seconds: float = 0.5) -> None:
        self.interval_seconds = interval_seconds
        self.peak_vram_mib = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: Exception | None = None

    def _sample(self) -> None:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        values = [int(line.strip()) for line in result.stdout.splitlines() if line.strip()]
        if not values:
            raise ValueError("nvidia-smi reported no GPU memory values")
        self.peak_vram_mib = max(self.peak_vram_mib, sum(values))

    def _sample_until_stopped(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self._sample()
            except (OSError, subprocess.CalledProcessError, ValueError) as error:
                self._error = error
                self._stop.set()

    def start(self) -> None:
        self._sample()
        self._thread = threading.Thread(target=self._sample_until_stopped, daemon=True)
        self._thread.start()

    def stop(self) -> int:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if self._error is not None:
            raise RuntimeError(f"GPU memory sampling failed: {self._error}") from self._error
        self._sample()
        return self.peak_vram_mib


def request_once(
    *,
    request_id: int,
    concurrency: int,
    prompt: dict[str, str],
    base_url: str,
    model: str,
    max_tokens: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    started_at_utc = datetime.now(timezone.utc).isoformat()
    request_started = time.monotonic()
    status_code = None
    try:
        with requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt["text"]}],
                "max_tokens": max_tokens,
                "temperature": 0,
                "seed": 42,
                "stream": True,
                "stream_options": {"include_usage": True},
            },
            headers={"Authorization": "Bearer ollama"},
            stream=True,
            timeout=(10, timeout_seconds),
        ) as response:
            status_code = response.status_code
            response.raise_for_status()
            ttft_seconds, completion_tokens = consume_stream(
                response.iter_lines(decode_unicode=True), request_started
            )
        end_to_end_seconds = time.monotonic() - request_started
        if ttft_seconds is None:
            return {
                "request_id": request_id,
                "concurrency": concurrency,
                "prompt_id": prompt["id"],
                "started_at_utc": started_at_utc,
                "success": False,
                "status_code": status_code,
                "ttft_seconds": None,
                "end_to_end_seconds": end_to_end_seconds,
                "completion_tokens": completion_tokens,
                "tokens_per_second": None,
                "measurement_error": "stream contained no generated content",
                "error": "stream contained no generated content",
            }
        generation_seconds = end_to_end_seconds - ttft_seconds
        tokens_per_second = (
            completion_tokens / generation_seconds
            if completion_tokens is not None and generation_seconds > 0
            else None
        )
        return {
            "request_id": request_id,
            "concurrency": concurrency,
            "prompt_id": prompt["id"],
            "started_at_utc": started_at_utc,
            "success": True,
            "status_code": status_code,
            "ttft_seconds": ttft_seconds,
            "end_to_end_seconds": end_to_end_seconds,
            "completion_tokens": completion_tokens,
            "tokens_per_second": tokens_per_second,
            "measurement_error": (
                "server did not report completion_tokens"
                if completion_tokens is None
                else None
            ),
            "error": None,
        }
    except (
        requests.RequestException,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValueError,
    ) as error:
        return {
            "request_id": request_id,
            "concurrency": concurrency,
            "prompt_id": prompt["id"],
            "started_at_utc": started_at_utc,
            "success": False,
            "status_code": status_code,
            "ttft_seconds": None,
            "end_to_end_seconds": time.monotonic() - request_started,
            "completion_tokens": None,
            "tokens_per_second": None,
            "measurement_error": None,
            "error": f"{type(error).__name__}: {error}",
        }


def load_prompts() -> list[dict[str, str]]:
    with PROMPTS_PATH.open(encoding="utf-8") as prompts_file:
        prompts = json.load(prompts_file)
    if not isinstance(prompts, list) or not prompts:
        raise ValueError("prompts.json must contain a non-empty list")
    for prompt in prompts:
        if not isinstance(prompt, dict) or not all(
            isinstance(prompt.get(field), str) and prompt[field]
            for field in ("id", "text")
        ):
            raise ValueError("each prompt must contain non-empty id and text strings")
    return prompts


def run_benchmark(args: argparse.Namespace) -> tuple[Path, Path]:
    run_started_at = datetime.now(timezone.utc)
    prompts = load_prompts()
    devices = gpu_info()
    warmup = request_once(
        request_id=0,
        concurrency=1,
        prompt=prompts[0],
        base_url=args.base_url,
        model=args.model,
        max_tokens=args.max_tokens,
        timeout_seconds=args.timeout,
    )
    if not warmup["success"]:
        raise RuntimeError(f"Warm-up request failed: {warmup['error']}")

    all_records: list[dict[str, Any]] = []
    summaries = []
    next_request_id = 1
    for concurrency in args.concurrency:
        request_count = len(prompts) * args.repeats
        scheduled = [
            (next_request_id + index, prompts[index % len(prompts)])
            for index in range(request_count)
        ]
        next_request_id += request_count
        sampler = GpuMemorySampler()
        sampler.start()
        try:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = [
                    executor.submit(
                        request_once,
                        request_id=request_id,
                        concurrency=concurrency,
                        prompt=prompt,
                        base_url=args.base_url,
                        model=args.model,
                        max_tokens=args.max_tokens,
                        timeout_seconds=args.timeout,
                    )
                    for request_id, prompt in scheduled
                ]
                records = [future.result() for future in futures]
        finally:
            peak_vram_mib = sampler.stop()
        all_records.extend(records)
        summaries.append(
            summarize_requests(records, concurrency, peak_vram_mib)
        )

    run_id = f"{args.engine}_{run_started_at.strftime('%Y%m%dT%H%M%S.%fZ')}"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / f"{run_id}.json"
    csv_path = args.output_dir / f"{run_id}.csv"
    report = {
        "run_id": run_id,
        "generated_at_utc": run_started_at.isoformat(),
        "engine": args.engine,
        "software_version": args.software_version,
        "image": args.image,
        "base_url": args.base_url,
        "model": args.model,
        "python_version": platform.python_version(),
        "gpu_devices": devices,
        "settings": {
            "concurrency_levels": args.concurrency,
            "repeats_per_prompt": args.repeats,
            "prompt_count": len(prompts),
            "max_tokens": args.max_tokens,
            "timeout_seconds": args.timeout,
            "warmup_success": warmup["success"],
            "warmup_ttft_seconds": warmup["ttft_seconds"],
        },
        "summary": summaries,
        "requests": all_records,
    }
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(all_records)
    return json_path, csv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, choices=("ollama", "vllm"))
    parser.add_argument("--software-version", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    if any(value < 1 for value in args.concurrency):
        parser.error("concurrency values must be positive integers")
    if args.repeats < 1 or args.max_tokens < 1 or args.timeout <= 0:
        parser.error("repeats, max-tokens, and timeout must be positive")
    return args


def main() -> int:
    args = parse_args()
    json_path, csv_path = run_benchmark(args)
    print(f"JSON: {json_path}")
    print(f"CSV:  {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
