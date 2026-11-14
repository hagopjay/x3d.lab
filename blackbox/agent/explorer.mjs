/**
 * explorer.mjs — a live, long-lived MCP session used by the UI's Explorer tab.
 *
 * This exists to prove a point: the same tool surface the agent uses is also
 * available to a human, one call at a time, with the server's own JSON Schema
 * driving the form. Same handshake, same validation, same wire.
 */
import { McpClient } from '../lib/mcp-client.mjs';
import { serverArgs } from './mcp-process.mjs';

let session = null;

async function ensureSession() {
  if (session?.client && !session.client._closed) return session;
  const client = new McpClient({
    command: process.execPath,
    args: serverArgs(),
    requestTimeoutMs: 180_000,
  });
  await client.start();
  await client.initialize();
  const res = await client.callTool('new_page', { url: 'about:blank', isolatedContext: 'blackbox-explorer' });
  const pageText = res.text || '';
  const selected = pageText.split('\n').find((l) => l.includes('[selected]'));
  const pageId = Number(selected?.match(/^\s*(\d+):/)?.[1] ?? 1);
  session = { client, pageId, startedAt: Date.now() };
  return session;
}

export async function listTools() {
  const { client } = await ensureSession();
  return {
    pageId: session.pageId,
    server: client.serverInfo,
    protocolVersion: client.protocolVersion,
    tools: client.tools.map((t) => ({
      name: t.name,
      description: (t.description || '').split('\n')[0].slice(0, 200),
      schema: t.inputSchema,
      requiresPageId: Boolean(t.inputSchema?.properties?.pageId),
    })),
  };
}

export async function probeTool({ tool, args = {}, url } = {}) {
  const s = await ensureSession();
  const { client } = s;

  // A page-scoped tool with no pageId gets the explorer's default page;
  // the schema is the source of truth for whether pageId is even allowed.
  const schema = client.toolMap.get(tool)?.inputSchema;
  if (!schema) throw new Error(`unknown tool "${tool}"`);
  const finalArgs = { ...args };
  if (schema.properties?.pageId && finalArgs.pageId === undefined) finalArgs.pageId = s.pageId;
  if (url && (tool === 'navigate_page')) finalArgs.url = url;

  const started = Date.now();
  const res = await client.callTool(tool, finalArgs, { timeoutMs: 180_000 });
  return {
    tool,
    args: finalArgs,
    pageId: s.pageId,
    ms: Date.now() - started,
    bytes: res.bytes,
    tokens: res.tokens,
    isError: res.isError,
    text: (res.text || '').slice(0, 20000),
    image: res.images?.[0] ? `data:${res.images[0].mimeType};base64,${res.images[0].data}` : null,
  };
}

export function resetExplorer() {
  session?.client?.close();
  session = null;
}
