// Tests for useGatewayWebSocket (04-08 Task 1).
// Mocks partysocket + fetchWsToken; drives synthetic message events.

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// --- Mock partysocket ---
// vi.mock factory is hoisted; the class must live inside the factory and be
// re-exposed via a module-level handle for tests to drive events.

type Listener = (ev: unknown) => void;
interface MockSocket {
  url: string;
  opts: Record<string, unknown>;
  closed: boolean;
  sent: string[];
  listeners: Record<string, Listener[]>;
  addEventListener: (t: string, fn: Listener) => void;
  send: (d: string) => void;
  close: () => void;
  emitOpen: () => void;
  emitMessage: (payload: unknown) => void;
}

vi.mock('partysocket', () => {
  class MockPartySocket implements MockSocket {
    url: string;
    opts: Record<string, unknown>;
    closed = false;
    sent: string[] = [];
    listeners: Record<string, Listener[]> = {};
    constructor(url: string, _protocols?: string | string[], opts: Record<string, unknown> = {}) {
      this.url = url;
      this.opts = opts;
      // expose to tests via a global registry
      (globalThis as unknown as { __mockPS: { last: MockSocket | null; lastOpts: Record<string, unknown> | null } }).__mockPS = {
        last: this,
        lastOpts: opts,
      };
    }
    addEventListener(type: string, fn: Listener) {
      (this.listeners[type] ||= []).push(fn);
    }
    send(data: string) {
      this.sent.push(data);
    }
    close() {
      this.closed = true;
    }
    emitOpen() {
      (this.listeners.open || []).forEach((f) => f(new Event('open')));
    }
    emitMessage(payload: unknown) {
      const ev = { data: JSON.stringify(payload) } as unknown as MessageEvent;
      (this.listeners.message || []).forEach((f) => f(ev));
    }
  }
  // Re-export under both names since the hook imports `WebSocket as PartySocket`.
  return { PartySocket: MockPartySocket, WebSocket: MockPartySocket };
});

function getRegistry() {
  return (globalThis as unknown as {
    __mockPS?: { last: MockSocket | null; lastOpts: Record<string, unknown> | null };
  }).__mockPS;
}

vi.mock('./gateway', () => ({
  fetchWsToken: vi.fn(async () => Promise.resolve({
    token: 'tkn-abc',
    expires_in: 60,
  })),
}));

import { useGatewayWebSocket } from './useGatewayWebSocket';
import { fetchWsToken } from './gateway';

async function flushAndOpen(): Promise<MockSocket> {
  await waitFor(() => {
    expect(getRegistry()?.last).toBeTruthy();
  });
  return getRegistry()!.last!;
}

beforeEach(() => {
  const reg = getRegistry();
  if (reg) {
    reg.last = null;
    reg.lastOpts = null;
  }
  vi.mocked(fetchWsToken).mockClear();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('useGatewayWebSocket', () => {
  it('H1: fetches ws-token and opens PartySocket with token in URL', async () => {
    renderHook(() => useGatewayWebSocket('foo'));
    const ws = await flushAndOpen();
    expect(fetchWsToken).toHaveBeenCalledWith('foo');
    expect(ws.url).toContain('/api/v1/ws/gateway/foo/logs?token=tkn-abc');
  });

  it('H2: sends subscribe frame after session', async () => {
    renderHook(() =>
      useGatewayWebSocket('foo', { keywords: ['err'], levelMin: 'warn' }),
    );
    const ws = await flushAndOpen();
    act(() => {
      ws.emitOpen();
      ws.emitMessage({ type: 'session', session_token: 's1' });
    });
    expect(ws.sent.length).toBe(1);
    expect(JSON.parse(ws.sent[0])).toEqual({
      type: 'subscribe',
      keywords: ['err'],
      level_min: 'warn',
    });
  });

  it('H3: log_line frames accumulate into lines', async () => {
    const { result } = renderHook(() => useGatewayWebSocket('foo'));
    const ws = await flushAndOpen();
    act(() => {
      ws.emitMessage({
        type: 'log_line',
        ts: '2026-05-04T01:02:03Z',
        level: 'info',
        text: 'hello',
      });
      ws.emitMessage({
        type: 'log_line',
        ts: '2026-05-04T01:02:04Z',
        level: 'error',
        text: 'boom',
      });
    });
    await waitFor(() => expect(result.current.lines.length).toBe(2));
    expect(result.current.lines[1]).toMatchObject({ level: 'error', text: 'boom' });
  });

  it('H4: lines cap at 5000 (top truncation)', async () => {
    const { result } = renderHook(() => useGatewayWebSocket('foo'));
    const ws = await flushAndOpen();
    act(() => {
      for (let i = 0; i < 5001; i++) {
        ws.emitMessage({ type: 'log_line', ts: '', level: 'info', text: `n${i}` });
      }
    });
    await waitFor(() => expect(result.current.lines.length).toBe(5000));
    // top got truncated → first surviving line should be n1, last n5000
    expect(result.current.lines[0].text).toBe('n1');
    expect(result.current.lines[4999].text).toBe('n5000');
  });

  it('H5: dropped_marker accumulates droppedCount', async () => {
    const { result } = renderHook(() => useGatewayWebSocket('foo'));
    const ws = await flushAndOpen();
    act(() => {
      ws.emitMessage({ type: 'dropped_marker', count: 7 });
      ws.emitMessage({ type: 'dropped_marker', count: 3 });
    });
    await waitFor(() => expect(result.current.droppedCount).toBe(10));
  });

  it('H6: re-subscribes when keywords/levelMin change without reconnect', async () => {
    const { rerender } = renderHook(
      (props: { keywords: string[]; levelMin: string }) =>
        useGatewayWebSocket('foo', props),
      { initialProps: { keywords: ['a'], levelMin: 'info' } },
    );
    const ws = await flushAndOpen();
    act(() => {
      ws.emitOpen();
      ws.emitMessage({ type: 'session' });
    });
    const initialCount = ws.sent.length;
    rerender({ keywords: ['b'], levelMin: 'warn' });
    await waitFor(() => expect(ws.sent.length).toBeGreaterThan(initialCount));
    const last = JSON.parse(ws.sent[ws.sent.length - 1]);
    expect(last).toEqual({ type: 'subscribe', keywords: ['b'], level_min: 'warn' });
    // still the same socket — no reconnect
    expect(getRegistry()?.last).toBe(ws);
  });

  it('H7: PartySocket gets backoff config matching D-07', async () => {
    renderHook(() => useGatewayWebSocket('foo'));
    await flushAndOpen();
    expect(getRegistry()?.lastOpts).toMatchObject({
      minReconnectionDelay: 1000,
      maxReconnectionDelay: 30000,
      reconnectionDelayGrowFactor: 1.5,
    });
  });

  it('H8: cleanup closes the socket', async () => {
    const { unmount } = renderHook(() => useGatewayWebSocket('foo'));
    const ws = await flushAndOpen();
    unmount();
    expect(ws.closed).toBe(true);
  });

  it('H9: paused buffers lines and flushes on resume', async () => {
    const { result, rerender } = renderHook(
      (props: { paused: boolean }) => useGatewayWebSocket('foo', props),
      { initialProps: { paused: true } },
    );
    const ws = await flushAndOpen();
    act(() => {
      ws.emitMessage({ type: 'log_line', ts: '', level: 'info', text: 'a' });
      ws.emitMessage({ type: 'log_line', ts: '', level: 'info', text: 'b' });
    });
    expect(result.current.lines.length).toBe(0);
    rerender({ paused: false });
    await waitFor(() => expect(result.current.lines.length).toBe(2));
    expect(result.current.lines.map((l) => l.text)).toEqual(['a', 'b']);
  });
});
