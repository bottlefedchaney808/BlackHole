/**
 * direct_cdp.mjs argument-validation tests -- no TradingView/CDP connection
 * needed. direct_cdp.mjs is the CDP recovery-path script (bypasses the MCP
 * transport, see its own header comment); these only cover the parts that
 * fail before any CDP import is attempted.
 *
 * Run: node --test tests/direct_cdp.test.js
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCRIPT = join(__dirname, '..', 'direct_cdp.mjs');

function run(args) {
  try {
    const stdout = execFileSync('node', [SCRIPT, ...args], {
      encoding: 'utf-8',
      timeout: 10000,
    });
    return { stdout, exitCode: 0 };
  } catch (err) {
    return {
      stdout: err.stdout || '',
      stderr: err.stderr || '',
      exitCode: err.status,
    };
  }
}

describe('direct_cdp.mjs — argument validation (no CDP connection required)', () => {
  it('unknown command exits 1 with a clear error', () => {
    const { stderr, exitCode } = run(['not-a-real-command']);
    assert.equal(exitCode, 1);
    const parsed = JSON.parse(stderr);
    assert.equal(parsed.success, false);
    assert.match(parsed.error, /unknown command/);
  });

  it('add without a symbol exits 1 before attempting a CDP connection', () => {
    const { stderr, exitCode } = run(['add']);
    assert.equal(exitCode, 1);
    assert.match(JSON.parse(stderr).error, /usage: add SYMBOL/);
  });

  it('addbulk without symbols exits 1 before attempting a CDP connection', () => {
    const { stderr, exitCode } = run(['addbulk']);
    assert.equal(exitCode, 1);
    assert.match(JSON.parse(stderr).error, /usage: addbulk/);
  });

  it('switch-list without a name exits 1 before attempting a CDP connection', () => {
    const { stderr, exitCode } = run(['switch-list']);
    assert.equal(exitCode, 1);
    assert.match(JSON.parse(stderr).error, /usage: switch-list NAME/);
  });

  it('the process exits promptly even when a command throws (no hung event loop)', () => {
    const start = Date.now();
    run(['not-a-real-command']);
    assert.ok(Date.now() - start < 5000, 'should not hang waiting on an open CDP handle');
  });
});
