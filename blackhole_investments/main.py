"""BlackHole Investments — a coding / research / trading specialist agent
built on the Claude Agent SDK.

Runs as an interactive REPL by default (`python main.py`), or takes a single
prompt as a one-shot query (`python main.py "..."`).

Requires ANTHROPIC_API_KEY in the environment (see .env.example). Two custom
tools bridge it into the FinancialDevelopment monorepo — see tools/market_tools.py:
  - mcp__market_tools__query_swap_data: read-only swaps.db lookups
  - mcp__market_tools__run_suite: trigger Vol_Suite/Options_Suite/VaR/sentiment
    runs via the dashboard widget API (POST /api/widgets/{slug}/run)
"""
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
)

from tools.market_tools import market_tools_server

SYSTEM_PROMPT = """You are BlackHole Investments, a specialist agent covering three roles:

1. Coding: read, write, and debug code in the FinancialDevelopment quant-finance
   monorepo (options pricing, volatility, VaR, sentiment suites, and the DTCC
   swaps-ingestion pipeline).
2. Research: investigate market structure, instruments, and strategy questions,
   using web search and the swap-data lookup tool.
3. Trading: reason about positioning, risk, and strategy using the analysis
   suites — trigger a run via the run_suite tool when you need fresh
   numbers rather than guessing.

Always state which role a given answer draws on when it isn't obvious. Prefer
running a real tool over speculating when live data would settle the question.
"""

OPTIONS = ClaudeAgentOptions(
    system_prompt=SYSTEM_PROMPT,
    model="sonnet",
    permission_mode="acceptEdits",
    allowed_tools=[
        "Read",
        "Grep",
        "Glob",
        "Bash",
        "WebSearch",
        "mcp__market_tools__query_swap_data",
        "mcp__market_tools__run_suite",
    ],
    mcp_servers={"market_tools": market_tools_server},
)


def _print_message(message) -> None:
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                print(block.text)


async def run_once(prompt: str) -> None:
    async with ClaudeSDKClient(options=OPTIONS) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            _print_message(message)


async def run_repl() -> None:
    print("BlackHole Investments — type a prompt, or 'exit' to quit.\n")
    async with ClaudeSDKClient(options=OPTIONS) as client:
        while True:
            try:
                prompt = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not prompt or prompt.lower() in {"exit", "quit"}:
                break
            await client.query(prompt)
            async for message in client.receive_response():
                _print_message(message)


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in.")
        sys.exit(1)

    try:
        if len(sys.argv) > 1:
            asyncio.run(run_once(" ".join(sys.argv[1:])))
        else:
            asyncio.run(run_repl())
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        print(f"Agent SDK error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
