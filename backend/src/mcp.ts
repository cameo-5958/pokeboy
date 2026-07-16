import { createServer, type Server as HttpServer } from "node:http";

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { createMcpExpressApp } from "@modelcontextprotocol/sdk/server/express.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";

import { dispatch, listDevices, liveDevices } from "./dev.js";
import { getEvents, getSeries, listSessions } from "./telemetry.js";

const DEFAULT_BIND = "100.65.93.90";
const FALLBACK_BIND = "127.0.0.1";
const DEFAULT_PORT = 4141;

function jsonContent(value: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(value) }] };
}

function unknownSession(sessionKey: string) {
  return {
    ...jsonContent({ error: `Unknown telemetry session: ${sessionKey}` }),
    isError: true,
  };
}

function createTelemetryMcpServer(): McpServer {
  const server = new McpServer({ name: "pokeboy-telemetry", version: "1.0.0" });

  server.registerTool(
    "list_sessions",
    { description: "List buffered Pokeboy telemetry sessions." },
    async () => jsonContent(listSessions()),
  );

  server.registerTool(
    "get_latest",
    {
      description: "Get a telemetry session summary and its most recent snapshot.",
      inputSchema: { session_key: z.string().min(1) },
    },
    async ({ session_key }) => {
      const summary = listSessions().find((candidate) => candidate.key === session_key);
      return summary ? jsonContent(summary) : unknownSession(session_key);
    },
  );

  server.registerTool(
    "get_series",
    {
      description: "Read a dotted snapshot metric over the most recent number of seconds.",
      inputSchema: {
        session_key: z.string().min(1),
        metric: z.string().min(1),
        seconds: z.number().nonnegative().optional(),
      },
    },
    async ({ session_key, metric, seconds }) => {
      const series = getSeries(session_key, metric, seconds);
      return series ? jsonContent(series) : unknownSession(session_key);
    },
  );

  server.registerTool(
    "get_events",
    {
      description: "Get buffered events for a telemetry session.",
      inputSchema: { session_key: z.string().min(1) },
    },
    async ({ session_key }) => {
      const events = getEvents(session_key);
      return events ? jsonContent(events) : unknownSession(session_key);
    },
  );

  registerDevTools(server);

  return server;
}

// Bit masks matching the app/emulator input protocol.
const BUTTON_MASKS: Record<string, { group: "buttons" | "dpad"; mask: number }> = {
  a: { group: "buttons", mask: 0x01 },
  b: { group: "buttons", mask: 0x02 },
  select: { group: "buttons", mask: 0x04 },
  start: { group: "buttons", mask: 0x08 },
  right: { group: "dpad", mask: 0x01 },
  left: { group: "dpad", mask: 0x02 },
  up: { group: "dpad", mask: 0x04 },
  down: { group: "dpad", mask: 0x08 },
};

const BUTTON_NAMES = Object.keys(BUTTON_MASKS) as [string, ...string[]];

/** Pick the target device: explicit id, or the only one live, else explain. */
function resolveDevice(deviceId: string | undefined): { device?: string; error?: string } {
  if (deviceId) return { device: deviceId };
  const live = liveDevices();
  if (live.length === 1) return { device: live[0] };
  if (live.length === 0) {
    return { error: "No device is polling for dev commands. Toggle DEV MODE on in the app settings." };
  }
  return { error: `Multiple live devices; pass device_id. Live: ${live.join(", ")}` };
}

/**
 * Dev-mode remote control (opt-in DEV MODE toggle in the app): press emulator
 * buttons and capture the LCD framebuffer of a connected device.
 */
function registerDevTools(server: McpServer): void {
  server.registerTool(
    "dev_status",
    { description: "List devices that have polled for dev-mode commands (DEV MODE toggle)." },
    async () => jsonContent(listDevices()),
  );

  server.registerTool(
    "dev_press",
    {
      description:
        "Press one or more Game Boy buttons on a dev-mode device (a, b, start, select, up, down, left, right), holding for hold_ms (default 150).",
      inputSchema: {
        buttons: z.array(z.enum(BUTTON_NAMES)).min(1),
        hold_ms: z.number().int().min(16).max(5000).optional(),
        device_id: z.string().min(1).optional(),
      },
    },
    async ({ buttons, hold_ms, device_id }) => {
      const { device, error } = resolveDevice(device_id);
      if (!device) return { ...jsonContent({ error }), isError: true };
      let buttonMask = 0;
      let dpadMask = 0;
      for (const name of buttons) {
        const { group, mask } = BUTTON_MASKS[name];
        if (group === "buttons") buttonMask |= mask;
        else dpadMask |= mask;
      }
      const result = await dispatch(device, {
        kind: "press",
        buttons: buttonMask,
        dpad: dpadMask,
        holdMs: hold_ms ?? 150,
      });
      if (!result.ok) return { ...jsonContent({ error: result.error }), isError: true };
      return jsonContent({ pressed: buttons, holdMs: hold_ms ?? 150, device });
    },
  );

  server.registerTool(
    "dev_screenshot",
    {
      description:
        "Capture the current 160x144 emulator LCD frame from a dev-mode device as a PNG.",
      inputSchema: { device_id: z.string().min(1).optional() },
    },
    async ({ device_id }) => {
      const { device, error } = resolveDevice(device_id);
      if (!device) return { ...jsonContent({ error }), isError: true };
      const result = await dispatch(device, { kind: "screenshot" });
      const prefix = "data:image/png;base64,";
      if (!result.ok || !result.png?.startsWith(prefix)) {
        return {
          ...jsonContent({ error: result.error ?? "Device returned no frame" }),
          isError: true,
        };
      }
      return {
        content: [
          { type: "image" as const, data: result.png.slice(prefix.length), mimeType: "image/png" as const },
        ],
      };
    },
  );
}

export function startMcpServer(): HttpServer {
  const requestedBind = process.env.TELEMETRY_MCP_BIND || DEFAULT_BIND;
  const configuredPort = Number(process.env.TELEMETRY_MCP_PORT ?? DEFAULT_PORT);
  const port = Number.isInteger(configuredPort) && configuredPort >= 0 && configuredPort <= 65_535
    ? configuredPort
    : DEFAULT_PORT;

  const app = createMcpExpressApp({
    host: requestedBind,
    allowedHosts: [requestedBind, FALLBACK_BIND, "localhost"],
  });

  app.post("/mcp", async (req, res) => {
    const server = createTelemetryMcpServer();
    try {
      const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
      await server.connect(transport);
      await transport.handleRequest(req, res, req.body);
      res.on("close", () => {
        void transport.close();
        void server.close();
      });
    } catch (error) {
      console.error("MCP request failed:", error);
      if (!res.headersSent) {
        res.status(500).json({
          jsonrpc: "2.0",
          error: { code: -32603, message: "Internal server error" },
          id: null,
        });
      }
    }
  });

  const methodNotAllowed = (_req: unknown, res: { status: (code: number) => { json: (body: unknown) => void } }) => {
    res.status(405).json({
      jsonrpc: "2.0",
      error: { code: -32000, message: "Method not allowed" },
      id: null,
    });
  };
  app.get("/mcp", methodNotAllowed);
  app.delete("/mcp", methodNotAllowed);

  const httpServer = createServer(app);
  let activeBind = requestedBind;
  let startupListeningHandler: (() => void) | undefined;

  const listen = (bind: string) => {
    activeBind = bind;
    httpServer.once("error", onStartupError);
    startupListeningHandler = () => {
      httpServer.off("error", onStartupError);
      httpServer.on("error", (error) => console.error("MCP server error:", error));
      const address = httpServer.address();
      const actualPort = typeof address === "object" && address ? address.port : port;
      const actualBind = typeof address === "object" && address ? address.address : bind;
      console.log(`pokeboy telemetry MCP listening on http://${actualBind}:${actualPort}/mcp`);
    };
    httpServer.once("listening", startupListeningHandler);
    httpServer.listen(port, bind);
  };

  const onStartupError = (error: NodeJS.ErrnoException) => {
    if (startupListeningHandler) {
      httpServer.off("listening", startupListeningHandler);
      startupListeningHandler = undefined;
    }
    if (error.code === "EADDRNOTAVAIL" && activeBind !== FALLBACK_BIND) {
      console.warn(
        `Telemetry MCP address ${activeBind} is unavailable; falling back to ${FALLBACK_BIND}`,
      );
      listen(FALLBACK_BIND);
      return;
    }
    console.error(`Unable to start telemetry MCP on ${activeBind}:${port}:`, error);
  };

  listen(requestedBind);
  return httpServer;
}
