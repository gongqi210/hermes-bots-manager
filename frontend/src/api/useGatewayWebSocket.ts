// Phase 4-08 Task 1: WebSocket hook for /api/v1/ws/gateway/{name}/logs.
// Wraps partysocket: fetches token → connects → sends subscribe on session →
// accumulates log_line frames (capped 5000) + dropped_marker counts.
// Backoff config locked to D-07; line cap to D-08.

import { useEffect, useRef, useState } from 'react';
// PartySocket's class enforces a `host` field; we just want the reconnecting
// WS primitive (re-exported as `WebSocket`), which takes (url, protocols, opts).
import { WebSocket as PartySocket } from 'partysocket';
import { fetchWsToken } from './gateway';

export type LogLine = {
  ts: string;
  level: 'debug' | 'info' | 'warn' | 'warning' | 'error' | 'critical';
  text: string;
};

export interface UseGatewayWebSocketOptions {
  keywords?: string[];
  levelMin?: string;
  paused?: boolean;
  enabled?: boolean;
}

const MAX_LINES = 5000;

export function useGatewayWebSocket(
  botName: string,
  opts: UseGatewayWebSocketOptions = {},
) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [droppedCount, setDroppedCount] = useState(0);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const socketRef = useRef<PartySocket | null>(null);
  const pausedRef = useRef(opts.paused ?? false);
  const buffer = useRef<LogLine[]>([]);
  const subOptsRef = useRef({
    keywords: opts.keywords ?? [],
    levelMin: opts.levelMin ?? 'info',
  });

  // Pause/resume — flush buffered lines on resume.
  useEffect(() => {
    pausedRef.current = opts.paused ?? false;
    if (!pausedRef.current && buffer.current.length) {
      setLines((prev) => {
        const merged = [...prev, ...buffer.current];
        buffer.current = [];
        return merged.length > MAX_LINES ? merged.slice(-MAX_LINES) : merged;
      });
    }
  }, [opts.paused]);

  // Open the socket once per botName.
  useEffect(() => {
    if (opts.enabled === false) return;
    let cancelled = false;
    let ws: PartySocket | null = null;
    (async () => {
      try {
        const { token } = await fetchWsToken(botName);
        if (cancelled) return;
        const base =
          (import.meta.env.VITE_WS_BASE_URL as string | undefined) ??
          window.location.origin.replace(/^http/, 'ws');
        const url = `${base}/api/v1/ws/gateway/${encodeURIComponent(botName)}/logs?token=${token}`;
        ws = new PartySocket(url, [], {
          minReconnectionDelay: 1000,
          maxReconnectionDelay: 30000,
          reconnectionDelayGrowFactor: 1.5,
        });
        socketRef.current = ws;
        ws.addEventListener('open', () => setIsConnected(true));
        ws.addEventListener('close', () => setIsConnected(false));
        ws.addEventListener('message', (ev: MessageEvent) => {
          let msg: unknown;
          try {
            msg = JSON.parse(ev.data as string);
          } catch {
            return;
          }
          if (typeof msg !== 'object' || msg === null) return;
          const m = msg as { type?: string; [k: string]: unknown };
          if (m.type === 'session') {
            ws?.send(
              JSON.stringify({
                type: 'subscribe',
                keywords: subOptsRef.current.keywords,
                level_min: subOptsRef.current.levelMin,
              }),
            );
          } else if (m.type === 'log_line') {
            const line: LogLine = {
              ts: String(m.ts ?? ''),
              level: (m.level as LogLine['level']) ?? 'info',
              text: String(m.text ?? ''),
            };
            if (pausedRef.current) {
              buffer.current.push(line);
              if (buffer.current.length > MAX_LINES) {
                buffer.current = buffer.current.slice(-MAX_LINES);
              }
            } else {
              setLines((prev) => {
                const next = [...prev, line];
                return next.length > MAX_LINES ? next.slice(-MAX_LINES) : next;
              });
            }
          } else if (m.type === 'dropped_marker') {
            setDroppedCount(
              (prev) => prev + (typeof m.count === 'number' ? m.count : 0),
            );
          } else if (m.type === 'error') {
            setError(String(m.msg ?? 'unknown'));
          }
        });
      } catch (e) {
        setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
      ws?.close();
    };
  }, [botName, opts.enabled]);

  // Re-send subscribe when filters change (no reconnect).
  const keywordsKey = (opts.keywords ?? []).join(',');
  useEffect(() => {
    subOptsRef.current = {
      keywords: opts.keywords ?? [],
      levelMin: opts.levelMin ?? 'info',
    };
    if (socketRef.current) {
      try {
        socketRef.current.send(
          JSON.stringify({
            type: 'subscribe',
            keywords: subOptsRef.current.keywords,
            level_min: subOptsRef.current.levelMin,
          }),
        );
      } catch {
        // socket not yet open — initial subscribe will fire on session message
      }
    }
    // We intentionally key on the joined string instead of the array reference.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keywordsKey, opts.levelMin]);

  return {
    lines,
    droppedCount,
    isConnected,
    error,
    clear: () => setLines([]),
  };
}
