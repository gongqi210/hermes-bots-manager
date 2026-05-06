import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import FeishuGroupPolicyPanel from './FeishuGroupPolicyPanel';

vi.mock('@/api/wizard', () => ({
  updateBotFeishuPolicy: vi.fn(),
}));

const { messageMock } = vi.hoisted(() => ({
  messageMock: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
}));
vi.mock('antd', async () => {
  const actual = await vi.importActual<typeof import('antd')>('antd');
  return { ...actual, message: messageMock };
});

vi.mock('@/hooks/useRole', () => ({ useRole: vi.fn() }));

import { updateBotFeishuPolicy } from '@/api/wizard';
import { useRole } from '@/hooks/useRole';

const mockedUpdate = vi.mocked(updateBotFeishuPolicy);
const mockedUseRole = vi.mocked(useRole);

function renderPanel(opts?: {
  current?: 'mention' | 'all' | 'block';
  role?: 'Owner' | 'Admin' | 'Editor' | 'Viewer';
}) {
  mockedUseRole.mockReturnValue(opts?.role ?? 'Owner');
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <FeishuGroupPolicyPanel
        botName="foo"
        currentStrategy={opts?.current ?? 'mention'}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockedUpdate.mockReset();
  mockedUseRole.mockReset();
});
afterEach(() => {
  messageMock.success.mockReset();
  messageMock.error.mockReset();
});

describe('<FeishuGroupPolicyPanel>', () => {
  it('renders three radio options', () => {
    renderPanel();
    expect(screen.getByTestId('group-policy-radio-mention')).toBeTruthy();
    expect(screen.getByTestId('group-policy-radio-all')).toBeTruthy();
    expect(screen.getByTestId('group-policy-radio-block')).toBeTruthy();
    expect(document.body.textContent).toContain('仅 @ 时响应');
    expect(document.body.textContent).toContain('响应所有群消息');
    expect(document.body.textContent).toContain('不响应群聊');
  });

  it('preselects current strategy', () => {
    renderPanel({ current: 'all' });
    const radio = document.querySelector(
      'input[value="all"]',
    ) as HTMLInputElement;
    expect(radio.checked).toBe(true);
  });

  it('calls updateBotFeishuPolicy with selected strategy on save', async () => {
    const user = userEvent.setup();
    mockedUpdate.mockResolvedValue({} as never);
    renderPanel({ current: 'mention' });
    await user.click(screen.getByTestId('group-policy-radio-block'));
    await user.click(screen.getByTestId('btn-save-group-policy'));
    await waitFor(() => {
      expect(mockedUpdate).toHaveBeenCalledWith('foo', {
        group_strategy: 'block',
      });
    });
  });

  it('disables save button + radios for Viewer role', () => {
    renderPanel({ role: 'Viewer' });
    const save = screen.getByTestId(
      'btn-save-group-policy',
    ) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    document
      .querySelectorAll('input[type="radio"]')
      .forEach((i) => expect((i as HTMLInputElement).disabled).toBe(true));
  });
});
