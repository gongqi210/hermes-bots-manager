import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { GatewayStatusPill } from './GatewayStatusPill';
import type { GatewayStatusOut } from '@/api/types';

function mk(overrides: Partial<GatewayStatusOut> = {}): GatewayStatusOut {
  return {
    bot_name: 'foo',
    state: 'running',
    why: 'ok',
    last_state_changed_at: null,
    pid: 1,
    active_profile: 'foo',
    is_active_profile: true,
    ...overrides,
  };
}

describe('<GatewayStatusPill>', () => {
  it('P1: running state renders 运行中 label', () => {
    render(<GatewayStatusPill status={mk({ state: 'running' })} />);
    expect(screen.getByTestId('gateway-status-pill')).toBeTruthy();
    expect(screen.getByText('运行中')).toBeTruthy();
  });

  it('P2: error state renders 异常 label', () => {
    render(
      <GatewayStatusPill
        status={mk({ state: 'error', why: '启动失败：xxx' })}
      />,
    );
    expect(screen.getByText('异常')).toBeTruthy();
  });

  it('P3: stopped state with last_state_changed_at renders relative time', () => {
    const past = new Date(Date.now() - 5 * 60_000).toISOString();
    render(
      <GatewayStatusPill
        status={mk({ state: 'stopped', last_state_changed_at: past })}
      />,
    );
    expect(screen.getByText('已停止')).toBeTruthy();
    // dayjs relative-time renders something like "5 分钟前"
    expect(screen.getAllByText(/分钟前/).length).toBeGreaterThan(0);
  });

  it('P4: unconfigured state renders 未配置', () => {
    render(<GatewayStatusPill status={mk({ state: 'unconfigured' })} />);
    expect(screen.getByText('未配置')).toBeTruthy();
  });

  it('P5: starting state renders 启动中', () => {
    render(<GatewayStatusPill status={mk({ state: 'starting' })} />);
    expect(screen.getByText('启动中')).toBeTruthy();
  });
});
