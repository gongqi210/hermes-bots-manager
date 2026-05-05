import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/api/client', () => {
  const get = vi.fn();
  const post = vi.fn();
  return {
    apiClient: { get, post, defaults: { baseURL: '/api/v1' } },
  };
});

import { apiClient } from '@/api/client';
import { listMyRuns, markMessageReceived } from './onboarding';

const mockedGet = vi.mocked(apiClient.get);
const mockedPost = vi.mocked(apiClient.post);

beforeEach(() => {
  mockedGet.mockReset();
  mockedPost.mockReset();
});

describe('onboarding api client', () => {
  it('O1: listMyRuns() calls GET /onboarding/runs with default limit=10', async () => {
    mockedGet.mockResolvedValueOnce({ data: [] });
    await listMyRuns();
    expect(mockedGet).toHaveBeenCalledWith('/onboarding/runs', {
      params: { limit: 10 },
    });
  });

  it('O2: listMyRuns(20) passes ?limit=20', async () => {
    mockedGet.mockResolvedValueOnce({ data: [] });
    await listMyRuns(20);
    expect(mockedGet).toHaveBeenCalledWith('/onboarding/runs', {
      params: { limit: 20 },
    });
  });

  it('O3: markMessageReceived(123) calls POST /onboarding/123/mark-message-received', async () => {
    mockedPost.mockResolvedValueOnce({ data: { id: 123, status: 'success' } });
    const out = await markMessageReceived(123);
    expect(mockedPost).toHaveBeenCalledWith(
      '/onboarding/123/mark-message-received',
    );
    expect(out).toEqual({ id: 123, status: 'success' });
  });
});
