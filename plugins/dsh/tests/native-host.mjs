/** Real DSH registries for adapter integration tests. No provider or network calls. */
export async function createNativeHost(t) {
  const [{ Context }, { default: SystemPrompt }, { default: Tools }, { default: Commands }] = await Promise.all([
    import('@deepseek-ai/cordis'),
    import('@deepseek-ai/dsh-system-prompt'),
    import('@deepseek-ai/dsh-tools'),
    import('@deepseek-ai/dsh-commands'),
  ]);
  const ctx = new Context();
  t.after(() => ctx.fiber.dispose());
  await ctx.plugin(SystemPrompt, {});
  await ctx.plugin(Tools, { mode: 'native' });
  await ctx.plugin(Commands, {});
  return ctx;
}
