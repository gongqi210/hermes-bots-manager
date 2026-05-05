import { describe, expect, it } from 'vitest';
import { extractErrorMessage } from './errors';

describe('extractErrorMessage', () => {
  it('returns response.data.detail when present', () => {
    const err = { response: { data: { detail: '已存在' } }, message: 'fallback' };
    expect(extractErrorMessage(err)).toBe('已存在');
  });

  it('falls back to Error.message when no detail', () => {
    const err = new Error('boom');
    expect(extractErrorMessage(err)).toBe('boom');
  });

  it('returns "Unknown error" when neither present', () => {
    expect(extractErrorMessage({})).toBe('Unknown error');
  });

  it('prefers detail even when message also present (W5 regression guard)', () => {
    const err = {
      response: { data: { detail: 'Bot 已存在' } },
      message: 'Request failed with status 409',
    };
    expect(extractErrorMessage(err)).toBe('Bot 已存在');
  });

  it('returns "Unknown error" for null', () => {
    expect(extractErrorMessage(null)).toBe('Unknown error');
  });

  it('returns "Unknown error" for undefined', () => {
    expect(extractErrorMessage(undefined)).toBe('Unknown error');
  });

  it('ignores empty string detail and falls back to message', () => {
    const err = { response: { data: { detail: '' } }, message: 'fallback msg' };
    expect(extractErrorMessage(err)).toBe('fallback msg');
  });
});
