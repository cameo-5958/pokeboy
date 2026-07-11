import { createServer, type Server as HttpServer } from "node:http";

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { createMcpExpressApp } from "@modelcontextprotocol/sdk/server/express.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";

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

  return server;
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
