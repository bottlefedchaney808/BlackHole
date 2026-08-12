#!/usr/bin/env bash
set -u

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  printf '%s\n' 'ERROR: not inside a git repository' >&2
  exit 2
}
cd "$ROOT" || exit 2

printf '%s\n' '[1/4] inspect tradingview-mcp workspace'
if [[ ! -d tradingview-mcp ]]; then
  printf '%s\n' 'ERROR: tradingview-mcp directory is missing' >&2
  exit 1
fi
if [[ -d tradingview-mcp/.git ]]; then
  actual="$(git -C tradingview-mcp rev-parse HEAD 2>/dev/null || true)"
  printf 'INFO: tradingview-mcp nested repo HEAD=%s\n' "${actual:-unknown}"
elif [[ -f tradingview-mcp/.git ]] && git -C tradingview-mcp rev-parse --git-dir >/dev/null 2>&1; then
  actual="$(git -C tradingview-mcp rev-parse HEAD 2>/dev/null || true)"
  printf 'INFO: tradingview-mcp initialized submodule HEAD=%s\n' "${actual:-unknown}"
else
  actual=''
  printf '%s\n' 'INFO: tradingview-mcp is present as a plain workspace directory'
fi
if git status --short -- tradingview-mcp 2>/dev/null | grep -q '^?? '; then
  printf '%s\n' 'INFO: tradingview-mcp is not tracked by the parent repo; continuing with source-only verification'
fi

printf '%s\n' '[2/4] verify tradingview-mcp gitlink when configured'
submodule_path=''
if [[ -f .gitmodules ]]; then
  while IFS= read -r submodule_key; do
    candidate_path="$(git config -f .gitmodules --get "$submodule_key" 2>/dev/null || true)"
    if [[ "$candidate_path" == "tradingview-mcp" ]]; then
      submodule_path="$candidate_path"
      break
    fi
  done < <(git config -f .gitmodules --name-only --get-regexp '^submodule\..*\.path$' 2>/dev/null || true)
fi
expected="$(git ls-tree HEAD -- tradingview-mcp 2>/dev/null | awk '$4 == "tradingview-mcp" {print $3}')"
mode="$(git ls-tree HEAD -- tradingview-mcp 2>/dev/null | awk '$4 == "tradingview-mcp" {print $1}')"
if [[ -n "$submodule_path" || "$mode" == "160000" ]]; then
  if [[ -z "$submodule_path" ]]; then
    printf '%s\n' 'ERROR: tradingview-mcp gitlink exists but .gitmodules mapping is missing' >&2
    exit 1
  fi
  if [[ "$mode" != "160000" ]]; then
    printf 'ERROR: tradingview-mcp is mapped in .gitmodules but has unexpected tree mode=%s\n' "${mode:-missing}" >&2
    exit 1
  fi
  if ! git submodule status -- tradingview-mcp; then
    printf '%s\n' 'ERROR: tradingview-mcp gitlink status failed' >&2
    exit 1
  fi
  if [[ -z "$expected" || -z "$actual" || "$expected" != "$actual" ]]; then
    printf 'ERROR: pinned commit mismatch (expected=%s actual=%s)\n' "${expected:-missing}" "${actual:-missing}" >&2
    exit 1
  fi
else
  printf '%s\n' 'INFO: parent repo does not configure tradingview-mcp as a submodule here; skipped gitlink pin check'
fi

printf '%s\n' '[3/4] verify TradingView MCP source entrypoint'
if [[ ! -f tradingview-mcp/src/server.js ]]; then
  printf '%s\n' 'ERROR: tradingview-mcp/src/server.js is missing' >&2
  exit 1
fi
if ! grep -q 'McpServer' tradingview-mcp/src/server.js; then
  printf '%s\n' 'ERROR: TradingView MCP server entrypoint is missing McpServer wiring' >&2
  exit 1
fi
if ! grep -q 'registerHealthTools' tradingview-mcp/src/server.js; then
  printf '%s\n' 'ERROR: TradingView MCP server entrypoint does not match expected tool registration source' >&2
  exit 1
fi

printf '%s\n' '[4/4] optional live CDP smoke gate (informational on Windows repo)'
if command -v hermes >/dev/null 2>&1; then
  live_output="$(hermes mcp test tradingview 2>&1)"
  live_status=$?
  if [[ $live_status -eq 0 ]]; then
    if printf '%s\n' "$live_output" | grep -E 'tv_health_check|chart_get_state|tools|84' >/dev/null 2>&1; then
      printf '%s\n' 'INFO: live CDP smoke gate returned expected TradingView tool evidence'
    else
      printf '%s\n' 'INFO: live CDP smoke gate passed but did not print the expected read-only tool evidence'
    fi
  else
    printf '%s\n' 'INFO: hermes mcp test tradingview failed; live CDP validation is advisory on this repo'
  fi
else
  printf '%s\n' 'INFO: hermes CLI unavailable; skipped live CDP query'
fi

printf '%s\n' 'RESULT: TradingView workspace verification passed'
