import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ModelConfigPage from './ModelConfigPage';
import { providerOptionMatchesSearch } from './modelConfigSearch';

vi.mock('@/api/management', async () => {
  const actual = await vi.importActual<typeof import('@/api/management')>(
    '@/api/management',
  );
  return {
    ...actual,
    getModelConfig: vi.fn(),
    putModelConfig: vi.fn(),
    startChatgptAuth: vi.fn(),
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

import {
  getHealth,
  getModelConfig,
  putModelConfig,
  startChatgptAuth,
} from '@/api/management';
const mockedGet = vi.mocked(getModelConfig);
const mockedPut = vi.mocked(putModelConfig);
const mockedStartChatgptAuth = vi.mocked(startChatgptAuth);
const mockedHealth = vi.mocked(getHealth);

function renderPage(initial: Partial<Awaited<ReturnType<typeof getModelConfig>>> = {}) {
  mockedGet.mockResolvedValue({
    bot_name: 'foo',
    provider: null,
    model: null,
    base_url: null,
    api_mode: null,
    is_chatgpt_auth: false,
    provider_authorized: false,
    providers: [],
    ...initial,
  });
  mockedHealth.mockResolvedValue({
    bot_name: 'foo',
    gateway_state: 'stopped',
    gateway_why: '未运行',
    model_configured: !!initial.provider,
    provider_authorized: true,
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
  mockedStartChatgptAuth.mockReset();
  mockedHealth.mockReset();
  vi.spyOn(window, 'open').mockImplementation(() => null);
});
afterEach(() => {
  messageMock.success.mockReset();
  messageMock.error.mockReset();
  vi.restoreAllMocks();
});

describe('<ModelConfigPage>', () => {
  it('matches OpenAI Codex when searching ChatGPT Codex', () => {
    expect(
      providerOptionMatchesSearch('chatgpt codex', {
        label: 'OpenAI Codex (openai-codex)',
        value: 'openai-codex',
      }),
    ).toBe(true);
  });

  it('renders form fields and warns when not configured', async () => {
    renderPage({});
    expect(await screen.findByTestId('model-config-page')).toBeTruthy();
    expect(screen.getByTestId('select-provider')).toBeTruthy();
    expect(screen.getByTestId('input-model')).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByTestId('model-not-configured')).toBeTruthy(),
    );
  });

  it('ChatGPT auth button opens the Codex browser authorization URL', async () => {
    const user = userEvent.setup();
    mockedStartChatgptAuth.mockResolvedValue({
      authorization_url: 'https://auth.openai.com/oauth/authorize?state=abc',
      process_id: 123,
      message: 'ok',
    });
    renderPage({
      provider: 'openai-codex',
      model: 'gpt-5.5',
      providers: [
        {
          slug: 'openai-codex',
          name: 'OpenAI Codex',
          is_current: true,
          is_user_defined: false,
          is_configured: true,
          models: ['gpt-5.5'],
          total_models: 1,
          source: 'hermes',
          base_url: 'https://chatgpt.com/backend-api/codex',
          api_mode: 'codex_responses',
          auth_type: 'oauth_external',
        },
      ],
    });
    await user.click(await screen.findByTestId('btn-chatgpt-auth'));
    await waitFor(() =>
      expect(mockedStartChatgptAuth).toHaveBeenCalledWith('foo'),
    );
    expect(mockedPut).not.toHaveBeenCalled();
    expect(window.open).toHaveBeenCalledWith(
      'https://auth.openai.com/oauth/authorize?state=abc',
      '_blank',
      'noopener,noreferrer',
    );
    await waitFor(() =>
      expect(messageMock.success).toHaveBeenCalledWith(
        '已打开 Codex auth 授权页，请在浏览器中完成授权',
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
      providers: [
        {
          slug: 'openai-codex',
          name: 'OpenAI Codex',
          is_current: true,
          is_user_defined: false,
          is_configured: true,
          models: ['gpt-5.5'],
          total_models: 1,
          source: 'hermes',
          base_url: 'https://chatgpt.com/backend-api/codex',
          api_mode: 'codex_responses',
          auth_type: 'oauth_external',
        },
      ],
    });
    expect(await screen.findByTestId('chatgpt-auth-tag')).toBeTruthy();
    expect(await screen.findByTestId('provider-transport-summary')).toBeTruthy();
  });

  it('saves only provider and model while transport stays Hermes-managed', async () => {
    const user = userEvent.setup();
    mockedPut.mockResolvedValue({
      bot_name: 'foo',
      provider: 'openai-codex',
      model: 'gpt-5.5',
      base_url: 'https://chatgpt.com/backend-api/codex',
      api_mode: 'codex_responses',
      is_chatgpt_auth: true,
      provider_authorized: true,
      providers: [],
    });
    renderPage({
      provider: 'openai-codex',
      model: 'gpt-5.5',
      base_url: 'https://chatgpt.com/backend-api/codex',
      api_mode: 'codex_responses',
      providers: [
        {
          slug: 'openai-codex',
          name: 'OpenAI Codex',
          is_current: true,
          is_user_defined: false,
          is_configured: true,
          models: ['gpt-5.5', 'gpt-5.4'],
          total_models: 2,
          source: 'hermes',
          base_url: 'https://chatgpt.com/backend-api/codex',
          api_mode: 'codex_responses',
          auth_type: 'oauth_external',
        },
      ],
    });
    await user.click(await screen.findByTestId('btn-save-model-config'));
    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('foo', {
        provider: 'openai-codex',
        model: 'gpt-5.5',
      }),
    );
  });
});
