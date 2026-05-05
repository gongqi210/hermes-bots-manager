// Tests for LogStreamView (04-08 Task 2).
// Mocks the useGatewayWebSocket hook so we can drive lines/dropped/error states
// directly from the test.

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type LogLine = { ts: string; level: string; text: string };

let hookState: {
  lines: LogLine[];
  droppedCount: number;
  isConnected: boolean;
  error: string | null;
  clear: () => void;
  lastOpts: Record<string, unknown> | null;
};

vi.mock('@/api/useGatewayWebSocket', () => ({
  useGatewayWebSocket: (_botName: string, opts: Record<string, unknown>) => {
    hookState.lastOpts = opts;
    return {
      lines: hookState.lines,
      droppedCount: hookState.droppedCount,
      isConnected: hookState.isConnected,
      error: hookState.error,
      clear: hookState.clear,
    };
  },
}));

vi.mock('@/api/gateway', () => ({
  downloadLogsUrl: (name: string, hours: number) =>
    `/api/v1/bots/${name}/logs/download?hours=${hours}`,
}));

import LogStreamView from './LogStreamView';

beforeEach(() => {
  hookState = {
    lines: [],
    droppedCount: 0,
    isConnected: true,
    error: null,
    clear: vi.fn(),
    lastOpts: null,
  };
});

afterEach(() => vi.clearAllMocks());

describe('LogStreamView', () => {
  it('L1: renders monospace pre with lines in order', () => {
    hookState.lines = [
      { ts: '2026-05-04T01:02:03Z', level: 'info', text: 'first' },
      { ts: '2026-05-04T01:02:04Z', level: 'error', text: 'second' },
    ];
    render(<LogStreamView botName="foo" />);
    const pre = screen.getByTestId('log-pre');
    expect(pre.tagName).toBe('PRE');
    expect((pre as HTMLElement).style.fontFamily).toBe('monospace');
    expect(pre.textContent).toContain('first');
    expect(pre.textContent).toContain('second');
    expect(pre.textContent!.indexOf('first')).toBeLessThan(
      pre.textContent!.indexOf('second'),
    );
  });

  it('L2: pause toggle flips paused option passed to hook', () => {
    render(<LogStreamView botName="foo" />);
    expect(hookState.lastOpts?.paused).toBe(false);
    const sw = screen.getByTestId('pause-toggle');
    // The Switch root is a button; click toggles checked.
    fireEvent.click(sw);
    expect(hookState.lastOpts?.paused).toBe(true);
  });

  it('L3: keyword filter updates hook keywords prop', () => {
    render(<LogStreamView botName="foo" />);
    const input = screen.getByPlaceholderText(
      '关键词过滤（多个用逗号分隔）',
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'error,timeout' } });
    expect(hookState.lastOpts?.keywords).toEqual(['error', 'timeout']);
  });

  it('L4: auto-scroll button is rendered', () => {
    render(<LogStreamView botName="foo" />);
    expect(screen.getByTestId('autoscroll-btn')).toBeTruthy();
  });

  it('L5: download dropdown contains 4 options for 1h/6h/24h/72h', async () => {
    render(<LogStreamView botName="foo" />);
    const btn = screen.getByTestId('btn-download');
    fireEvent.mouseEnter(btn);
    // AntD Dropdown menu mounts on hover/click; trigger click for jsdom.
    fireEvent.click(btn);
    // The labels come straight from i18n.
    expect(await screen.findByText('最近 1 小时')).toBeTruthy();
    expect(screen.getByText('最近 6 小时')).toBeTruthy();
    expect(screen.getByText('最近 24 小时')).toBeTruthy();
    expect(screen.getByText('最近 72 小时')).toBeTruthy();
  });

  it('L6: droppedCount > 0 surfaces alert', () => {
    hookState.droppedCount = 47;
    render(<LogStreamView botName="foo" />);
    expect(screen.getByText(/已丢弃 47 行/)).toBeTruthy();
  });

  it('L7: error renders red alert', () => {
    hookState.error = 'supervisor not running';
    render(<LogStreamView botName="foo" />);
    expect(screen.getByText('supervisor not running')).toBeTruthy();
  });

  it('L8: truncated banner appears at 5000 lines', () => {
    hookState.lines = Array.from({ length: 5000 }, (_, i) => ({
      ts: '',
      level: 'info',
      text: `line${i}`,
    }));
    render(<LogStreamView botName="foo" />);
    expect(
      screen.getByText('实时窗口仅保留最近 5000 行 / 需完整请下载'),
    ).toBeTruthy();
  });

  it('L9: level coloring applied via data-level attribute and color style', () => {
    hookState.lines = [
      { ts: '', level: 'info', text: 'i' },
      { ts: '', level: 'warn', text: 'w' },
      { ts: '', level: 'error', text: 'e' },
    ];
    render(<LogStreamView botName="foo" />);
    const pre = screen.getByTestId('log-pre');
    const rows = pre.querySelectorAll('div[data-level]');
    expect(rows.length).toBe(3);
    expect(rows[0].getAttribute('data-level')).toBe('info');
    expect(rows[1].getAttribute('data-level')).toBe('warn');
    expect(rows[2].getAttribute('data-level')).toBe('error');
    // warn/error have non-default color
    expect((rows[1] as HTMLElement).style.color).not.toBe('');
    expect((rows[2] as HTMLElement).style.color).not.toBe('');
  });
});
