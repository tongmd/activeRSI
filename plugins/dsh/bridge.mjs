import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ENTRY = 'import signal; from activersi.runtime import main; signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt())); raise SystemExit(main())';
export function normalizeConfig(input = {}) {
  const { workspace, python = 'python3', coreRoot, timeoutMs = 660000 } = input;
  if (typeof workspace !== 'string' || !path.isAbsolute(workspace) || workspace === path.parse(workspace).root) throw new TypeError('workspace must be an absolute non-root directory');
  if (typeof python !== 'string' || !python.trim()) throw new TypeError('python must name a Python executable');
  if (coreRoot !== undefined && (typeof coreRoot !== 'string' || !path.isAbsolute(coreRoot))) throw new TypeError('coreRoot must be an absolute source checkout path');
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 660000) throw new TypeError('timeoutMs must be 100–660000');
  return { workspace: path.normalize(workspace), python, coreRoot, timeoutMs };
}

export function createBridge(config) {
  const pending = new Set();
  let closed = false;
  async function run(args, { signal, stdin, host = false } = {}) {
    if (closed) throw new Error('activeRSI plugin is disposed');
    signal?.throwIfAborted();
    if (stdin !== undefined && Buffer.byteLength(stdin) > 1_000_000) throw new Error('Proposal exceeds 1 MB');
    let finish;
    const finished = new Promise(resolve => { finish = resolve; });
    const prefix = host ? [fileURLToPath(new URL('./host_bridge.py', import.meta.url))] : ['-c', ENTRY];
    const child = spawn(config.python, [...prefix, '--workspace', config.workspace, ...args], {
      cwd: config.coreRoot,
      env: { ...process.env, ...(config.coreRoot ? { PYTHONPATH: config.coreRoot } : {}), PYTHONUNBUFFERED: '1' },
      stdio: ['pipe', 'pipe', 'pipe'], shell: false,
    });
    const entry = { child, finished, stop: undefined };
    pending.add(entry);
    try {
      return await new Promise((resolve, reject) => {
        let out = [], err = [], bytes = 0, failure, force;
        const stop = reason => {
          if (failure) return;
          failure = reason;
          child.kill('SIGTERM');
          force = setTimeout(() => child.kill('SIGKILL'), 2000);
          force.unref();
        };
        entry.stop = stop;
        const abort = () => stop(new Error('activeRSI operation cancelled; inspect status before retrying writes'));
        signal?.addEventListener('abort', abort, { once: true });
        if (signal?.aborted) abort();
        const timer = setTimeout(() => stop(new Error('activeRSI operation timed out; inspect status before retrying writes')), config.timeoutMs);
        const collect = target => chunk => {
          bytes += chunk.length;
          if (bytes > 2_000_000) stop(new Error('activeRSI output exceeded 2 MB'));
          else target.push(chunk);
        };
        child.stdout.on('data', collect(out)); child.stderr.on('data', collect(err));
        child.stdin.on('error', () => {});
        child.once('error', error => { failure = error; });
        child.once('close', code => {
          clearTimeout(timer); clearTimeout(force); signal?.removeEventListener('abort', abort);
          if (failure) return reject(failure);
          if (code !== 0) {
            // Core errors may contain observations; keep them in this owner's tool response only.
            const detail = Buffer.concat(out).toString().trim() || Buffer.concat(err).toString().trim();
            return reject(new Error(`activeRSI exited ${code}: ${detail.slice(0, 4000)}`));
          }
          try { resolve(JSON.parse(Buffer.concat(out).toString())); }
          catch { reject(new Error('activeRSI returned invalid JSON')); }
        });
        child.stdin.end(stdin);
      });
    } finally { pending.delete(entry); finish(); }
  }
  return { run, async dispose() {
    closed = true;
    for (const entry of pending) entry.stop?.(new Error('activeRSI plugin unloaded'));
    await Promise.all([...pending].map(e => e.finished));
  }};
}
