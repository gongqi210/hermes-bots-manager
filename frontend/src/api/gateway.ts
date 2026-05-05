// Phase 4-07: Typed REST client for /api/v1/bots/*/gateway/* endpoints,
// allowlist, and ws-token issuance. apiClient.baseURL is `/api/v1`.

import { apiClient } from '@/api/client';
import type {
  AllowlistOut,
  GatewayActionResponse,
  GatewayStatusOut,
  WSTokenResponse,
} from '@/api/types';

export async function getGatewayStatus(botName: string): Promise<GatewayStatusOut> {
  const { data } = await apiClient.get<GatewayStatusOut>(
    `/bots/${encodeURIComponent(botName)}/gateway/status`,
  );
  return data;
}

export async function gatewayAction(
  botName: string,
  action: 'start' | 'stop' | 'restart',
): Promise<GatewayActionResponse> {
  const { data } = await apiClient.post<GatewayActionResponse>(
    `/bots/${encodeURIComponent(botName)}/gateway/${action}`,
  );
  return data;
}

export async function getAllowlist(botName: string): Promise<AllowlistOut> {
  const { data } = await apiClient.get<AllowlistOut>(
    `/bots/${encodeURIComponent(botName)}/allowlist`,
  );
  return data;
}

export async function putAllowlist(
  botName: string,
  users: string[],
): Promise<AllowlistOut> {
  const { data } = await apiClient.put<AllowlistOut>(
    `/bots/${encodeURIComponent(botName)}/allowlist`,
    { users },
  );
  return data;
}

export async function fetchWsToken(botName: string): Promise<WSTokenResponse> {
  const { data } = await apiClient.post<WSTokenResponse>('/ws-token', {
    bot_name: botName,
  });
  return data;
}

export function downloadLogsUrl(
  botName: string,
  hours: 1 | 6 | 24 | 72,
): string {
  const base = apiClient.defaults.baseURL ?? '';
  return `${base}/bots/${encodeURIComponent(botName)}/logs/download?hours=${hours}`;
}
