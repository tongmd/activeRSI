import assert from 'node:assert/strict';
import { test } from 'node:test';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { createNativeHost } from './native-host.mjs';

test('official DSH registries expose native tools, commands and prompt and unwind on disposal', async t => {
  const ctx = await createNativeHost(t);
  // Contract fixture, deliberately independent of the activeRSI adapter implementation.
  const fiber = ctx.plugin({
    name: 'activersi-contract-fixture',
    inject: ['tools', 'commands', 'systemPrompt'],
    apply(pluginCtx) {
      pluginCtx.tools.register(defineTool({
        name: 'activersi_contract_probe', description: 'Inspect the contract fixture', parameters: {},
        output: {
          schema: { type: 'object', additionalProperties: false, properties: { state: { type: 'string', required: true } } },
          render: (_args, value) => [{ type: 'text', text: value.state }],
        },
        async execute(_args, execution) {
          execution.signal.throwIfAborted();
          return { state: 'proposal' };
        },
      }));
      pluginCtx.commands.register({ name: 'activersi-contract', description: 'Inspect fixture', handler: () => ({ kind: 'success', text: 'proposal' }) });
      pluginCtx.systemPrompt.section({ name: 'activersi-contract', order: 4000, text: 'Apply only promoted changes.', interpolate: false });
    },
  });
  await fiber;
  const assembly = await ctx.systemPrompt.assemble();
  assert.ok(assembly.tools.some(tool => tool.name === 'activersi_contract_probe'));
  assert.ok(assembly.sections.some(section => section.name === 'activersi-contract'));
  const result = await ctx.tools.execute({ name: 'activersi_contract_probe', callId: 'contract-fixture', arguments: {}, signal: new AbortController().signal });
  assert.equal(result.isError, false);
  assert.deepEqual(result.value, { state: 'proposal' });
  // Command discovery only: executing a command requires a real Session log.
  assert.equal(ctx.commands.find({}, 'activersi-contract').name, 'activersi-contract');
  await fiber.dispose();
  assert.equal(ctx.tools.get('activersi_contract_probe'), undefined);
  assert.equal(ctx.commands.find({}, 'activersi-contract'), undefined);
  assert.ok(!(await ctx.systemPrompt.assemble()).sections.some(section => section.name === 'activersi-contract'));
});
