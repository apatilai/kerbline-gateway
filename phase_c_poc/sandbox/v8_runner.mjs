/**
 * Air-gapped V8 Code Mode Sandbox Runner
 * Invoked by Host RPC Bridge with:
 *   node --max-old-space-size=64 --disallow-code-generation-from-strings sandbox/v8_runner.mjs
 *
 * Isolation guarantees:
 * - Zero network, filesystem, or process/env access inside vm context
 * - codeGeneration.strings = false, codeGeneration.wasm = false
 * - 5,000 ms V8 CPU timeout per script execution
 * - 30,000 ms wall-clock RPC budget enforced across Host RPC Bridge round-trips
 * - Typed RPC calls communicated strictly over stdin/stdout NDJSON
 */

import vm from 'node:vm';
import readline from 'node:readline';

const CPU_TIMEOUT_MS = 5000;
const WALL_TIMEOUT_MS = 30000;

const rl = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
});

const pendingRpcs = new Map();
let nextRpcId = 1;
let scriptCode = null;
let started = false;

function sendLine(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

function callHostRpc(toolName, args) {
  return new Promise((resolve, reject) => {
    const id = nextRpcId++;
    pendingRpcs.set(id, { resolve, reject });
    sendLine({ type: 'rpc_request', id, tool: String(toolName), args: args ?? {} });
  });
}

async function runScript(code) {
  const startWall = Date.now();
  const wallTimer = setTimeout(() => {
    sendLine({
      type: 'error',
      code: 'WALL_TIMEOUT',
      message: `Code Mode exceeded ${WALL_TIMEOUT_MS}ms wall-clock RPC budget`,
    });
    process.exit(1);
  }, WALL_TIMEOUT_MS);

  // Build frozen, minimal kerbline RPC proxy with zero Node globals
  const kerblineProxy = Object.freeze({
    match_invoice_to_po: (args) => callHostRpc('match_invoice_to_po', args),
    propose_payment_batch: (args) => callHostRpc('propose_payment_batch', args),
    change_supplier_bank_details: (args) => callHostRpc('change_supplier_bank_details', args),
  });

  const sandboxGlobals = Object.create(null);
  sandboxGlobals.kerbline = kerblineProxy;
  sandboxGlobals.JSON = JSON;
  sandboxGlobals.Math = Math;
  sandboxGlobals.Number = Number;
  sandboxGlobals.String = String;
  sandboxGlobals.Boolean = Boolean;
  sandboxGlobals.Array = Array;
  sandboxGlobals.Object = Object;

  const context = vm.createContext(sandboxGlobals, {
    name: 'kerbline-v8-isolate',
    codeGeneration: { strings: false, wasm: false },
  });

  try {
    const wrapped = `(async () => {\n${code}\n})()`;
    const script = new vm.Script(wrapped, { filename: 'agent_code_mode.js' });
    const resultPromise = script.runInContext(context, {
      timeout: CPU_TIMEOUT_MS,
      breakOnSigint: true,
    });
    const result = await resultPromise;
    clearTimeout(wallTimer);
    sendLine({
      type: 'result',
      elapsedMs: Date.now() - startWall,
      result: result ?? null,
    });
    process.exit(0);
  } catch (err) {
    clearTimeout(wallTimer);
    sendLine({
      type: 'error',
      code: err?.code === 'ERR_SCRIPT_EXECUTION_TIMEOUT' ? 'CPU_TIMEOUT' : 'RUNTIME_ERROR',
      message: String(err?.message || err),
    });
    process.exit(1);
  }
}

rl.on('line', (line) => {
  if (!line.trim()) return;
  let msg;
  try {
    msg = JSON.parse(line);
  } catch {
    return;
  }

  if (msg.type === 'init' && !started) {
    started = true;
    scriptCode = msg.code || '';
    runScript(scriptCode);
  } else if (msg.type === 'rpc_response') {
    const waiter = pendingRpcs.get(msg.id);
    if (waiter) {
      pendingRpcs.delete(msg.id);
      if (msg.error) {
        waiter.reject(new Error(msg.error));
      } else {
        waiter.resolve(msg.result);
      }
    }
  }
});
