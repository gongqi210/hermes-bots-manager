import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/api/client', () => {
  const get = vi.fn();
  const post = vi.fn();
  const put = vi.fn();
  return {
    apiClient: {
      get,
      post,
      put,
      defaults: { baseURL: '/api/v1' },
    },
  };
});

import { apiClient } from '@/api/client';
import {
  downloadLogsUrl,
  fetchWsToken,
  gatewayAction,
  getAllowlist,
  getGatewayStatus,
  putAllowlist,
} from './gateway';
import type { GatewayActionResponse, GatewayStatusOut } from './types';

const mockedGet = vi.mocked(apiClient.get);
const mockedPost = vi.mocked(apiClient.post);
const mockedPut = vi.mocked(apiClient.put);

beforeEach(() => {
  mockedGet.mockReset();
  mockedPost.mockReset();
  mockedPut.mockReset();
});

describe('gateway api client', () => {
  it('T1: getGatewayStatus calls GET /bots/{name}/gateway/status', async () => {
    const status: GatewayStatusOut = {
      bot_name: 'foo',
      state: 'running',
      why: 'ok',
      last_state_changed_at: null,
      pid: 1234,
      active_profile: 'foo',
      is_active_profile: true,
    };
    mockedGet.mockResolvedValueOnce({ data: status });
    const out = await getGatewayStatus('foo');
    expect(mockedGet).toHaveBeenCalledWith('/bots/foo/gateway/status');
    expect(out).toEqual(status);
  });

  it('T2: gatewayAction calls POST /bots/{name}/gateway/{action}', async () => {
    const resp: GatewayActionResponse = {
      bot_name: 'foo',
      action: 'start',
      new_state: 'starting',
      recent_log_tail: ['a', 'b'],
    };
    mockedPost.mockResolvedValueOnce({ data: resp });
    const out = await gatewayAction('foo', 'start');
    expect(mockedPost).toHaveBeenCalledWith('/bots/foo/gateway/start');
    // Type-check (T5): out is GatewayActionResponse
    expect(out.action).toBe('start');
    expect(out.recent_log_tail).toEqual(['a', 'b']);
  });

  it('T2b: gatewayAction supports stop and restart', async () => {
    mockedPost.mockResolvedValue({
      data: {
        bot_name: 'b',
        action: 'stop',
        new_state: 'stopped',
        recent_log_tail: [],
      },
    });
    await gatewayAction('b', 'stop');
    expect(mockedPost).toHaveBeenLastCalledWith('/bots/b/gateway/stop');
    await gatewayAction('b', 'restart');
    expect(mockedPost).toHaveBeenLastCalledWith('/bots/b/gateway/restart');
  });

  it('T3: allowlist GET + PUT', async () => {
    mockedGet.mockResolvedValueOnce({
      data: { bot_name: 'foo', users: ['ou_a'] },
    });
    const a = await getAllowlist('foo');
    expect(mockedGet).toHaveBeenCalledWith('/bots/foo/allowlist');
    expect(a.users).toEqual(['ou_a']);

    mockedPut.mockResolvedValueOnce({
      data: { bot_name: 'foo', users: ['ou_a'] },
    });
    await putAllowlist('foo', ['ou_a']);
    expect(mockedPut).toHaveBeenCalledWith('/bots/foo/allowlist', {
      users: ['ou_a'],
    });
  });

  it('T4: fetchWsToken posts /ws-token with bot_name', async () => {
    mockedPost.mockResolvedValueOnce({
      data: { token: 'tok', expires_in: 60 },
    });
    const out = await fetchWsToken('foo');
    expect(mockedPost).toHaveBeenCalledWith('/ws-token', { bot_name: 'foo' });
    expect(out).toEqual({ token: 'tok', expires_in: 60 });
  });

  it('encodes bot names with special chars', async () => {
    mockedGet.mockResolvedValueOnce({ data: {} });
    await getGatewayStatus('a/b');
    expect(mockedGet).toHaveBeenCalledWith('/bots/a%2Fb/gateway/status');
  });

  it('downloadLogsUrl builds an absolute path with baseURL', () => {
    expect(downloadLogsUrl('foo', 6)).toBe(
      '/api/v1/bots/foo/logs/download?hours=6',
    );
  });
});
