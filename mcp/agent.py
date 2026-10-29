"""Ask the vLLM-served model whether the inference service is healthy, using ONLY the
read-only MCP tools from mcp/server.py (spawned as a stdio subprocess).

Usage: python mcp/agent.py --llm-url http://localhost:8001/v1 --model qwen2.5-7b-awq \
         --prometheus-url http://localhost:9090
The model has no other tools: no shell, no file access, no free-form queries.
Every MCP tool call is logged by the server (MCP_TOOL_LOG); the transcript is printed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from mcp import Client, StdioServerParameters
from mcp.types import TextContent

QUESTION = "Is the inference service healthy, and why might it be slow? Use the tools to check, then answer briefly."
SYSTEM = (
    "You are an operations assistant for a vLLM inference service. You can only use the provided "
    "read-only monitoring tools. Base every claim on tool results; if a tool returns no data, say so. "
    "Do not invent numbers."
)
MAX_STEPS = 8


def to_openai_tool(tool) -> dict:
    return {
        "type": "function",
        "function": {"name": tool.name, "description": tool.description or "", "parameters": tool.input_schema},
    }


def text_of(result) -> str:
    return "\n".join(b.text for b in result.content if isinstance(b, TextContent)) or "(empty result)"


async def run(args) -> int:
    server_path = Path(__file__).resolve().parent / "server.py"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_path)],
        env={**os.environ, "PROMETHEUS_URL": args.prometheus_url, "MCP_TOOL_LOG": args.tool_log},
    )
    async with Client(params) as client:
        tools = [to_openai_tool(t) for t in (await client.list_tools()).tools]
        print(f"MCP tools offered to the model: {[t['function']['name'] for t in tools]}\n")
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": args.question}]
        for step in range(MAX_STEPS):
            resp = httpx.post(
                f"{args.llm_url}/chat/completions",
                json={"model": args.model, "messages": messages, "tools": tools, "temperature": 0, "max_tokens": 600},
                timeout=120,
            )
            resp.raise_for_status()
            msg = resp.json()["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls") and v is not None})
            if not calls:
                print("--- final answer ---")
                print(msg.get("content", ""))
                return 0
            for call in calls:
                name = call["function"]["name"]
                try:
                    arguments = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                if name not in {t["function"]["name"] for t in tools}:
                    output = f"unknown tool {name}"
                else:
                    result = await client.call_tool(name, arguments)
                    output = text_of(result)
                print(f"[step {step}] tool call: {name}({arguments}) -> {output}")
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})
        print("stopped: step limit reached without a final answer", file=sys.stderr)
        return 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--llm-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--prometheus-url", default="http://localhost:9090")
    p.add_argument("--tool-log", default="logs/mcp-tool-calls.jsonl")
    p.add_argument("--question", default=QUESTION)
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
