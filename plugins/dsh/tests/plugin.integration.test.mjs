import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import Sessions from '@deepseek-ai/dsh-session';
import plugin from '../index.mjs';
import { createBridge } from '../bridge.mjs';
import { createNativeHost } from './native-host.mjs';
const coreRoot = process.env.ACTIVERSI_CORE_ROOT || fileURLToPath(new URL('../../../', import.meta.url));
async function host(t) {
  const workspace = await fs.mkdtemp(path.join(os.tmpdir(), 'activersi-dsh-test-'));
  t.after(() => fs.rm(workspace, { recursive: true, force: true }));
  const ctx = await createNativeHost(t);
  await ctx.plugin(Sessions, {});
  // Real Session owns command lifecycle; no model driver needed.
  const agent = { session: ctx.sessions.create('activersi-test') };
  const fiber = ctx.plugin(plugin, { workspace, coreRoot, timeoutMs: 10000 });
  await fiber;
  const signal = new AbortController().signal;
  return { ctx, agent, workspace, fiber,
    command: line => ctx.commands.execute(agent, line, [], signal),
    tool: (name, args={}) => ctx.tools.execute({name:`activersi_${name}`,callId:`test-${name}`,arguments:args,agent,signal}),
  };
}

test('native commands initialize Python workspace; real tools observe/status and reject nonexistent feedback', async t => {
  const h = await host(t);
  const events = [];
  h.ctx.on('session/event', (_session, event) => { events.push(event); });
  const init = await h.command('/activersi init');
  assert.equal(init.result.kind, 'success', init.result.text);
  assert.equal(JSON.parse(init.result.text).status, 'initialized');
  assert.deepEqual(events.map(event => event.type), ['command/run', 'command/done']);
  const observation = await h.tool('observe', {kind:'correction',text:'Use a short, concrete plan.'});
  assert.equal(observation.isError, false, JSON.stringify(observation));
  assert.equal(observation.value.status, 'observed');
  const before = await h.tool('status');
  assert.equal(before.value.observations, 1);
  const feedback = await h.tool('feedback', {hypothesis:'does-not-exist',decision:'accept',text:'Real user answer'});
  assert.equal(feedback.isError, true);
  const after = await h.tool('status');
  assert.deepEqual(after.value, before.value);
  const status = await h.command('/activersi status');
  assert.equal(status.result.kind, 'success');
  assert.equal(JSON.parse(status.result.text).observations, 1);
  // Ensure plugin has not silently accepted unknown command names.
  assert.equal((await h.command('/activersi execute')).result.kind, 'error');
});

test('native prompt refresh reads active harness and unload removes all registrations', async t => {
  const h = await host(t);
  assert.match((await h.ctx.systemPrompt.assemble()).contexts.find(x=>x.name==='activersi-harness').text,/not initialized/);
  await h.command('/activersi init');
  const file = path.join(h.workspace, 'harness', 'AGENT.md');
  await fs.writeFile(file, '# Active test harness\nKeep the response compact.\n');
  const assembly = await h.ctx.systemPrompt.assemble();
  assert.match(assembly.contexts.find(x=>x.name==='activersi-harness').text,/Keep the response compact/);
  assert.ok(assembly.sections.some(x=>x.name==='activersi'));
  assert.deepEqual(assembly.tools.filter(x=>x.name.startsWith('activersi_')).map(x=>x.name).sort(), ['activersi_dream','activersi_feedback','activersi_observe','activersi_propose','activersi_status','activersi_wake']);
  await fs.writeFile(file, '# Updated harness\nUse a verified artifact.\n');
  assert.match((await h.ctx.systemPrompt.assemble()).contexts.find(x=>x.name==='activersi-harness').text,/Use a verified artifact/);
  await h.fiber.dispose();
  assert.equal(h.ctx.tools.get('activersi_status'), undefined);
  assert.equal(h.ctx.commands.find(h.agent,'activersi'), undefined);
  const unloaded = await h.ctx.systemPrompt.assemble();
  assert.ok(!unloaded.sections.some(x=>x.name==='activersi'));
  assert.ok(!unloaded.contexts.some(x=>x.name==='activersi-harness'));
});

test('bridge abort drains the owned Python child and refuses work after disposal', async t => {
  const fixtureRoot = await fs.mkdtemp(path.join(os.tmpdir(),'activersi-dsh-abort-'));
  t.after(()=>fs.rm(fixtureRoot,{recursive:true,force:true}));
  await fs.mkdir(path.join(fixtureRoot,'activersi'));
  await fs.writeFile(path.join(fixtureRoot,'activersi','__init__.py'),'');
  const marker = path.join(fixtureRoot,'ready');
  await fs.writeFile(path.join(fixtureRoot,'activersi','runtime.py'),`import os, time\ndef main():\n    with open(${JSON.stringify(marker)}, 'w') as f: f.write(str(os.getpid()))\n    time.sleep(60)\n`);
  const bridge = createBridge({workspace:path.join(fixtureRoot,'workspace'),coreRoot:fixtureRoot,python:'python3',timeoutMs:10000});
  t.after(()=>bridge.dispose());
  const controller = new AbortController();
  const pending = bridge.run(['status'],{signal:controller.signal});
  const rejected = assert.rejects(pending,/cancelled/);
  for (let tries=0; tries<100; tries++) { try { await fs.access(marker); break; } catch { await new Promise(r=>setTimeout(r,10)); } }
  const pid = Number(await fs.readFile(marker,'utf8'));
  controller.abort();
  await rejected;
  assert.throws(()=>process.kill(pid,0), {code:'ESRCH'});
  await bridge.dispose();
  await assert.rejects(bridge.run(['status']),/disposed/);
});

// Synthetic fixture only, created inside a disposable test workspace. It is not
// evidence of user preferences or a model evaluation; no proposal is promoted.
function proposalFixture(prepared) {
  return {
    hypotheses:[{id:'fixture-compact',requirement:'TEST FIXTURE: compact plans may improve review',alternatives:['TEST FIXTURE: priorities may matter more'],observation_ids:[prepared.observations.at(-1).id],probe:{question:'TEST FIXTURE: shorter layout or clearer priorities?',information_gain:0.8,interruption_cost:0.2}}],
    candidate_harness:prepared.harness+'\nTEST FIXTURE: use a compact review layout.\n',
    candidate_policy:{question_budget:1,interruption_weight:1.2,minimum_value:0.1},
    rationale:'Synthetic integration fixture; no real user requirement or model evidence.',
  };
}

test('host model proposal contract completes dream/wake/feedback without promoting harness', async t => {
  const h = await host(t);
  await h.command('/activersi init');
  await h.tool('observe',{kind:'correction',text:'TEST FIXTURE: shorten the plan layout.'});
  const before = (await h.tool('status')).value;
  const prepared = await h.tool('dream');
  assert.equal(prepared.isError,false,JSON.stringify(prepared));
  assert.match(prepared.value.snapshot_sha256,/^[a-f0-9]{64}$/);
  assert.ok(prepared.value.response_contract.hypotheses);
  const proposed = await h.tool('propose',{snapshot:prepared.value.snapshot_sha256,proposal:JSON.stringify(proposalFixture(prepared.value))});
  assert.equal(proposed.isError,false,JSON.stringify(proposed));
  assert.equal(proposed.value.cycle.status,'proposed');
  const awake = await h.tool('wake');
  assert.equal(awake.isError,false,JSON.stringify(awake));
  assert.equal(awake.value.action,'probe');
  assert.equal(awake.value.probes.length,1);
  const feedback = await h.tool('feedback',{hypothesis:proposed.value.hypotheses[0].id,decision:'accept',text:'TEST FIXTURE answer: compact layout.'});
  assert.equal(feedback.isError,false,JSON.stringify(feedback));
  assert.equal(feedback.value.harness_changed,false);
  const after = (await h.tool('status')).value;
  assert.equal(after.harness_sha256,before.harness_sha256);
  assert.equal(after.cycles.length,1);
});

test('stale proposal and invalid proposal contract reject without mutating workspace state', async t => {
  const h = await host(t);
  await h.command('/activersi init');
  await h.tool('observe',{kind:'request',text:'TEST FIXTURE request.'});
  const first = (await h.tool('dream')).value;
  await h.tool('observe',{kind:'correction',text:'TEST FIXTURE newer correction.'});
  const statePath = path.join(h.workspace,'state.json');
  const before = await fs.readFile(statePath,'utf8');
  const stale = await h.tool('propose',{snapshot:first.snapshot_sha256,proposal:JSON.stringify(proposalFixture(first))});
  assert.equal(stale.isError,true);
  assert.match(JSON.stringify(stale.content),/changed since dream/);
  assert.equal(await fs.readFile(statePath,'utf8'),before);
  const fresh = (await h.tool('dream')).value;
  const invalid = proposalFixture(fresh);
  invalid.hypotheses[0].observation_ids=['missing-observation'];
  const failed = await h.tool('propose',{snapshot:fresh.snapshot_sha256,proposal:JSON.stringify(invalid)});
  assert.equal(failed.isError,true);
  assert.match(JSON.stringify(failed.content),/existing observation IDs/);
  assert.equal(await fs.readFile(statePath,'utf8'),before);
  assert.equal((await h.tool('status')).value.cycles.length,0);
});
