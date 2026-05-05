import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ModelConfigPage from './ModelConfigPage';

vi.mock('@/api/management', async () => {
  const actual = await vi.importActual<typeof import('@/api/management')>(
    '@/api/management',
  );
  return {
    ...actual,
    getModelConfig: vi.fn(),
    putModelConfig: vi.fn(),
    getHealth: vi.fn(),
  };
});

const { messageMock } = vi.hoisted(() => ({
  messageMock: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
vi.mock('antd', async () => {
  const actual = await vi.importActual<typeof import('antd')>('antd');
  return { ...actual, message: messageMock };
});

import { getHealth, getModelConfig, putModelConfig } from '@/api/management';
const mockedGet = vi.mocked(getModelConfig);
const mockedPut = vi.mocked(putModelConfig);
const mockedHealth = vi.mocked(getHealth);

function renderPage(initial: Partial<Awaited<ReturnType<typeof getModelConfig>>> = {}) {
  mockedGet.mockResolvedValue({
    bot_name: 'foo',
    provider: null,
    model: null,
    base_url: null,
    api_mode: null,
    is_chatgpt_auth: false,
    ...initial,
  });
  mockedHealth.mockResolvedValue({
    bot_name: 'foo',
    gateway_state: 'stopped',
    gateway_why: '未运行',
    model_configured: !!initial.provider,
    workspace_status: 'unset',
    skills_enabled: 0,
    skills_total: 0,
    dangerous_skill_count: 0,
    shadowed_skill_count: 0,
    allowlist_preset: 'custom',
    overall: 'warning',
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ModelConfigPage botName="foo" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockedGet.mockReset();
  mockedPut.mockReset();
  mockedHealth.mockReset();
});
afterEach(() => {
  messageMock.success.mockReset();
  messageMock.error.mockReset();
});

describe('<ModelConfigPage>', () => {
  it('renders form fields and warns when not configured', async () => {
    renderPage({});
    expect(await screen.findByTestId('model-config-page')).toBeTruthy();
    expect(screen.getByTestId('input-provider')).toBeTruthy();
    expect(screen.getByTestId('input-model')).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByTestId('model-not-configured')).toBeTruthy(),
    );
  });

  it('one-click ChatGPT auth shortcut posts the canonical default', async () => {
    const user = userEvent.setup();
    mockedPut.mockResolvedValue({
      bot_name: 'foo',
      provider: 'openai-codex',
      model: 'gpt-5.5',
      base_url: 'https://chatgpt.com/backend-api/codex',
      api_mode: 'codex_responses',
      is_chatgpt_auth: true,
    });
    renderPage({});
    await user.click(await screen.findByTestId('btn-chatgpt-auth'));
    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('foo', {
        provider: 'openai-codex',
        model: 'gpt-5.5',
        base_url: 'https://chatgpt.com/backend-api/codex',
        api_mode: 'codex_responses',
      }),
    );
    await waitFor(() =>
      expect(messageMock.success).toHaveBeenCalledWith(
        '已写入 ChatGPT auth 订阅模型配置',
      ),
    );
  });

  it('shows ChatGPT auth tag when backend reports is_chatgpt_auth', async () => {
    renderPage({
      provider: 'openai-codex',
      model: 'gpt-5.5',
      base_url: 'https://chatgpt.com/backend-api/codex',
      api_mode: 'codex_responses',
      is_chatgpt_auth: true,
    });
    expect(await screen.findByTestId('chatgpt-auth-tag')).toBeTruthy();
  });
});
