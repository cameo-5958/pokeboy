/**
 * Minimal Discord gateway (v10, JSON encoding) client for the backend.
 *
 * Runs on Node's built-in WebSocket (global since Node 22). Supports IDENTIFY
 * with zero intents (enough for INTERACTION_CREATE), heartbeating with ACK
 * zombie detection, RESUME after drops, and capped exponential reconnect
 * backoff. Nothing else - the Battle Link bot never needs guild state or
 * message content.
 */

const GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json";

const OP_DISPATCH = 0;
const OP_HEARTBEAT = 1;
const OP_IDENTIFY = 2;
const OP_RESUME = 6;
const OP_RECONNECT = 7;
const OP_INVALID_SESSION = 9;
const OP_HELLO = 10;
const OP_HEARTBEAT_ACK = 11;

/** Close codes after which the token will never work; retrying is pointless. */
const FATAL_CLOSE_CODES = new Set([4004, 4010, 4011, 4012, 4013, 4014]);

export type GatewayEvents = {
  /** Fired on every READY with the bot's application id. */
  onReady: (applicationId: string) => void;
  onInteraction: (interaction: any) => void;
  /** Diagnostics channel (connection state changes, fatal errors). */
  onStatus?: (status: string, detail?: unknown) => void;
  /** Fired when the gateway gives up permanently (bad token). */
  onFatal?: (reason: string) => void;
};

export class DiscordGateway {
  private socket: WebSocket | null = null;
  private stopped = false;
  private sequence: number | null = null;
  private sessionId: string | null = null;
  private resumeUrl: string | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private awaitingAck = false;
  private backoffMs = 1000;

  constructor(
    private readonly token: string,
    private readonly events: GatewayEvents,
  ) {}

  start(): void {
    if (typeof WebSocket === "undefined") {
      try { this.events.onFatal?.("Node >= 22 is required (no global WebSocket)"); } catch {}
      return;
    }
    this.stopped = false;
    this.connect(false);
  }

  stop(): void {
    this.stopped = true;
    this.clearTimers();
    if (this.reconnectTimer) { clearTimeout(this.reconnectTimer); this.reconnectTimer = null; }
    const socket = this.socket;
    this.socket = null;
    try { socket?.close(1000); } catch {}
  }

  private status(status: string, detail?: unknown): void {
    try { this.events.onStatus?.(status, detail); } catch {}
  }

  private clearTimers(): void {
    if (this.heartbeatTimer) { clearInterval(this.heartbeatTimer); this.heartbeatTimer = null; }
  }

  private connect(resume: boolean): void {
    if (this.stopped) return;
    const url = resume && this.resumeUrl ? `${this.resumeUrl}?v=10&encoding=json` : GATEWAY_URL;
    this.status("connecting", { resume });
    let socket: WebSocket;
    try {
      socket = new WebSocket(url);
    } catch {
      this.scheduleReconnect(false);
      return;
    }
    this.socket = socket;
    socket.onmessage = (event) => {
      if (this.socket !== socket) return;
      let payload: any;
      try { payload = JSON.parse(String(event.data)); } catch { return; }
      this.handlePayload(socket, payload, resume);
    };
    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.clearTimers();
      if (this.stopped) return;
      if (FATAL_CLOSE_CODES.has(event.code)) {
        this.status("fatal-close", { code: event.code, reason: event.reason });
        try { this.events.onFatal?.(`Gateway closed (${event.code}): ${event.reason || "auth failure"}`); } catch {}
        return;
      }
      // 4007 (bad seq) and 4009 (session timed out) require a fresh identify.
      if (event.code === 4007 || event.code === 4009) this.sessionId = null;
      this.status("closed", { code: event.code, reason: event.reason });
      this.scheduleReconnect(this.sessionId !== null);
    };
    socket.onerror = () => {
      // onclose follows; nothing to do here (undici fires both).
    };
  }

  private handlePayload(socket: WebSocket, payload: any, resuming: boolean): void {
    if (typeof payload.s === "number") this.sequence = payload.s;
    switch (payload.op) {
      case OP_HELLO: {
        const interval = Number(payload.d?.heartbeat_interval) || 41250;
        this.clearTimers();
        this.awaitingAck = false;
        // First beat after the jitter fraction of the interval, per docs.
        setTimeout(() => { if (this.socket === socket) this.sendHeartbeat(socket); }, Math.floor(interval * Math.random()));
        this.heartbeatTimer = setInterval(() => {
          if (this.socket !== socket) return;
          if (this.awaitingAck) {
            // Zombie connection: close and resume.
            this.status("zombie");
            try { socket.close(4000); } catch {}
            return;
          }
          this.sendHeartbeat(socket);
        }, interval);
        if (resuming && this.sessionId) {
          this.send(socket, OP_RESUME, {
            token: this.token,
            session_id: this.sessionId,
            seq: this.sequence,
          });
        } else {
          this.send(socket, OP_IDENTIFY, {
            token: this.token,
            intents: 0,
            properties: { os: "linux", browser: "pokeboy", device: "pokeboy" },
          });
        }
        break;
      }
      case OP_HEARTBEAT:
        this.sendHeartbeat(socket);
        break;
      case OP_HEARTBEAT_ACK:
        this.awaitingAck = false;
        this.backoffMs = 1000; // healthy connection; reset backoff
        break;
      case OP_RECONNECT:
        try { socket.close(4000); } catch {}
        break;
      case OP_INVALID_SESSION:
        if (!payload.d) this.sessionId = null;
        try { socket.close(4000); } catch {}
        break;
      case OP_DISPATCH:
        this.handleDispatch(payload.t, payload.d);
        break;
      default:
        break;
    }
  }

  private handleDispatch(type: string, data: any): void {
    if (type === "READY") {
      this.sessionId = typeof data?.session_id === "string" ? data.session_id : null;
      this.resumeUrl = typeof data?.resume_gateway_url === "string" ? data.resume_gateway_url : null;
      const applicationId = data?.application?.id;
      this.status("ready");
      if (typeof applicationId === "string") {
        try { this.events.onReady(applicationId); } catch {}
      }
    } else if (type === "RESUMED") {
      this.status("resumed");
    } else if (type === "INTERACTION_CREATE") {
      try { this.events.onInteraction(data); } catch {}
    }
  }

  private sendHeartbeat(socket: WebSocket): void {
    this.awaitingAck = true;
    this.send(socket, OP_HEARTBEAT, this.sequence);
  }

  private send(socket: WebSocket, op: number, d: unknown): void {
    try { socket.send(JSON.stringify({ op, d })); } catch {}
  }

  private scheduleReconnect(resume: boolean): void {
    if (this.stopped || this.reconnectTimer) return;
    const delay = this.backoffMs;
    this.backoffMs = Math.min(this.backoffMs * 2, 60000);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect(resume);
    }, delay);
  }
}
