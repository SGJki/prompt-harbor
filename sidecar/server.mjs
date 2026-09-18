#!/usr/bin/env node

import { createServer } from "node:http";
import { pathToFileURL } from "node:url";

const host = process.env.PI_AI_SIDECAR_HOST || "127.0.0.1";
const port = Number(process.env.PI_AI_SIDECAR_PORT || 0);
const maxBody = Number(process.env.PI_AI_SIDECAR_MAX_BODY || 10 * 1024 * 1024);
const localToken = process.env.PI_AI_SIDECAR_TOKEN;
const fixture = process.env.PI_AI_SIDECAR_FIXTURE === "1";

let models;
let loadFailure;

function json(res, status, value) {
  const body = JSON.stringify(value);
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(body) });
  res.end(body);
}

function authorized(req) {
  if (!localToken) return true;
  return req.headers.authorization === `Bearer ${localToken}`;
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > maxBody) {
        reject(Object.assign(new Error("request body too large"), { statusCode: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

async function loadModels() {
  if (fixture) return undefined;
  if (models) return models;
  if (loadFailure) throw loadFailure;
  try {
    const configured = process.env.PI_AI_PACKAGE || "@earendil-works/pi-ai/providers/all";
    const specifier = configured.startsWith(".") || configured.startsWith("/")
      ? pathToFileURL(configured).href
      : configured;
    const module = await import(specifier);
    if (typeof module.builtinModels !== "function") {
      throw new Error("pi-ai package does not export builtinModels()");
    }
    models = module.builtinModels();
    return models;
  } catch (error) {
    loadFailure = error instanceof Error ? error : new Error(String(error));
    throw loadFailure;
  }
}

function fixtureModels() {
  return [{
    id: "fixture/model",
    provider: "fixture",
    name: "Fixture model",
    api: "pi-messages",
    contextWindow: 128000,
    input: ["text"],
    reasoning: false,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
  }];
}

function publicModel(model) {
  return {
    id: `${model.provider}/${model.id}`,
    provider: model.provider,
    name: model.name,
    api: model.api,
    contextWindow: model.contextWindow,
    input: model.input,
    reasoning: model.reasoning,
    cost: model.cost,
  };
}

function modelFor(id) {
  const slash = typeof id === "string" ? id.indexOf("/") : -1;
  if (slash <= 0 || slash === id.length - 1) return undefined;
  return { provider: id.slice(0, slash), id: id.slice(slash + 1) };
}

function writeEvent(res, event) {
  res.write(`data: ${JSON.stringify(event)}\n\n`);
  res.flushHeaders?.();
}

function usageOf(message) {
  return message?.usage || {
    input: 0,
    output: 0,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}

function eventToWire(event) {
  if (event.type === "start") return { type: "start" };
  if (event.type === "done") {
    return { type: "done", reason: event.reason, usage: usageOf(event.message), responseId: event.message?.responseId };
  }
  if (event.type === "error") {
    return {
      type: "error",
      reason: event.reason === "aborted" ? "aborted" : "error",
      usage: usageOf(event.error),
      errorMessage: event.error?.errorMessage,
      responseId: event.error?.responseId,
    };
  }
  const index = event.contentIndex;
  if (event.type === "text_start") return { type: "text_start", contentIndex: index };
  if (event.type === "text_delta") return { type: "text_delta", contentIndex: index, delta: event.delta };
  if (event.type === "text_end") return {
    type: "text_end",
    contentIndex: index,
    content: event.content,
    ...(event.contentSignature === undefined ? {} : { contentSignature: event.contentSignature }),
  };
  if (event.type === "thinking_start") return { type: "thinking_start", contentIndex: index };
  if (event.type === "thinking_delta") return { type: "thinking_delta", contentIndex: index, delta: event.delta };
  if (event.type === "thinking_end") return {
    type: "thinking_end",
    contentIndex: index,
    content: event.content,
    ...(event.thinkingSignature === undefined ? {} : { contentSignature: event.thinkingSignature }),
    ...(event.redacted === undefined ? {} : { redacted: event.redacted }),
  };
  if (event.type === "toolcall_start") {
    const block = event.partial?.content?.[index];
    return { type: "toolcall_start", contentIndex: index, id: block?.id || "", toolName: block?.name || "" };
  }
  if (event.type === "toolcall_delta") return { type: "toolcall_delta", contentIndex: index, delta: event.delta };
  if (event.type === "toolcall_end") return { type: "toolcall_end", contentIndex: index, toolCall: event.toolCall };
  return undefined;
}

async function streamFixture(res, body) {
  writeEvent(res, { type: "start" });
  writeEvent(res, { type: "text_start", contentIndex: 0 });
  writeEvent(res, { type: "text_delta", contentIndex: 0, delta: `fixture:${body.model}` });
  writeEvent(res, { type: "text_end", contentIndex: 0, content: `fixture:${body.model}` });
  writeEvent(res, {
    type: "done",
    reason: "stop",
    usage: {
      input: 1,
      output: 1,
      cacheRead: 0,
      cacheWrite: 0,
      totalTokens: 2,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
    },
  });
  res.end();
}

async function streamPi(res, req, body) {
  const collection = await loadModels();
  const selected = modelFor(body.model);
  const model = selected && collection?.getModel(selected.provider, selected.id);
  if (!model) {
    json(res, 404, { error: { code: "model_not_found", message: `Unknown model: ${body.model}` } });
    return;
  }
  res.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-cache",
    connection: "keep-alive",
    "x-accel-buffering": "no",
  });
  const controller = new AbortController();
  req.on("aborted", () => controller.abort());
  res.on("close", () => controller.abort());
  let terminal = false;
  try {
    const options = { ...(body.options || {}), signal: controller.signal };
    for (const key of ["apiKey", "api_key", "headers", "authorization", "proxy-authorization", "accessToken", "refreshToken", "credential", "env", "fetch", "signal", "onPayload", "onResponse", "transformHeaders"]) {
      delete options[key];
    }
    const stream = collection.stream(model, body.context || { messages: [] }, options);
    for await (const event of stream) {
      const wire = eventToWire(event);
      if (wire) writeEvent(res, wire);
      if (event.type === "done" || event.type === "error") {
        terminal = true;
        break;
      }
    }
    if (!terminal) {
      writeEvent(res, { type: "error", reason: "error", usage: usageOf(), errorMessage: "stream ended without a terminal event" });
    }
  } catch (error) {
    writeEvent(res, { type: "error", reason: "error", usage: usageOf(), errorMessage: error instanceof Error ? error.message : String(error) });
  } finally {
    res.end();
  }
}

const server = createServer(async (req, res) => {
  if (!authorized(req)) {
    json(res, 401, { error: { code: "unauthorized", message: "invalid sidecar token" } });
    return;
  }
  if (req.method === "GET" && req.url === "/health") {
    json(res, 200, { ok: true, fixture });
    return;
  }
  if (req.method === "GET" && req.url === "/models") {
    try {
      const collection = await loadModels();
      const values = fixture ? fixtureModels() : collection.getModels().map(publicModel);
      json(res, 200, { models: values });
    } catch (error) {
      json(res, 503, { error: { code: "pi_ai_unavailable", message: error.message } });
    }
    return;
  }
  if (req.method !== "POST" || req.url !== "/messages") {
    json(res, 404, { error: { code: "not_found", message: "not found" } });
    return;
  }
  let body;
  try {
    body = JSON.parse(await readBody(req));
  } catch (error) {
    json(res, error.statusCode || 400, { error: { code: "invalid_request", message: error.message } });
    return;
  }
  if (!body || typeof body !== "object" || typeof body.model !== "string" || !body.context) {
    json(res, 400, { error: { code: "invalid_request", message: "model and context are required" } });
    return;
  }
  res.setHeader("x-pi-model", body.model);
  if (fixture) {
    res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache", connection: "keep-alive", "x-accel-buffering": "no" });
    await streamFixture(res, body);
    return;
  }
  try {
    await streamPi(res, req, body);
  } catch (error) {
    if (!res.headersSent) json(res, 503, { error: { code: "pi_ai_unavailable", message: error.message } });
    else res.end();
  }
});

server.listen(port, host, () => {
  const address = server.address();
  console.log(`READY ${address.address}:${address.port}`);
});
