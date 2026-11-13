import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createBridge } from '../bridge.mjs';
import { toolOperations } from '../index.mjs';

const coreRoot = fileURLToPath(new URL('../../../', import.meta.url));

test('option-looking observation text survives the CLI boundary', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'activersi-argv-'));
  const bridge = createBridge({ workspace: path.join(root, 'workspace'), coreRoot, python: 'python3', timeoutMs: 5000 });
  try {
    await bridge.run(['init']);
    for (const text of ['--verbose', '--kind=outcome', '$(touch should-not-exist); `echo nope`']) {
      const result = await bridge.run(toolOperations().observe.argv({ text, kind: 'request' }));
      assert.equal(result.observation.text, text);
      assert.equal(result.observation.kind, 'request');
    }
  } finally {
    await bridge.dispose();
    await fs.rm(root, { recursive: true, force: true });
  }
});

test('host helper cancellation unwinds a held core workspace lock', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'activersi-host-cancel-'));
  const workspace = path.join(root, 'workspace');
  const python = path.join(root, 'slow-python');
  const bridge = createBridge({ workspace, coreRoot, python, timeoutMs: 10000 });
  // Execute the real helper, delaying its first state read only after locked()
  // has created .lock. This deterministically exercises its SIGTERM handler.
  await fs.writeFile(python, `#!/usr/bin/env python3
import os, runpy, sys, time
if sys.argv[1] == '-c':
    os.execvp('python3', ['python3', *sys.argv[1:]])
from activersi import runtime
original = runtime.read
def slow_read(file):
    if file.name == 'state.json':
        (file.parent / 'test-ready').write_text('ready')
        time.sleep(30)
    return original(file)
runtime.read = slow_read
script = sys.argv.pop(1)
runpy.run_path(script, run_name='__main__')
`, { mode: 0o700 });
  try {
    await bridge.run(['init']);
    await bridge.run(['observe', '--text=authorized observation']);
    const controller = new AbortController();
    const pending = bridge.run(['prepare'], { host: true, signal: controller.signal });
    const rejected = assert.rejects(pending, /cancelled/);
    let ready = false;
    for (let i = 0; i < 200; i++) {
      ready = await fs.stat(path.join(workspace, 'test-ready')).then(() => true, () => false);
      if (ready) break;
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    assert.ok(ready, 'helper reached the locked state read');
    assert.ok(await fs.stat(path.join(workspace, '.lock')));
    controller.abort();
    await rejected;
    await assert.rejects(fs.stat(path.join(workspace, '.lock')), { code: 'ENOENT' });
    assert.equal((await bridge.run(['observe', '--text=retry after cancellation'])).status, 'observed');
  } finally {
    await bridge.dispose();
    await fs.rm(root, { recursive: true, force: true });
  }
});
