// Wizard API calls — FEISHU-04, FEISHU-05.
// Phase 3-02 plan deviation note: apiClient is a NAMED export in /api/client.ts,
// so this file uses `{ apiClient }` rather than the plan's default-import snippet.

import { apiClient } from '@/api/client';
import type {
  AppIdCheckResult,
  BotFeishuCredentialsIn,
  BotFeishuPolicyPayload,
  BotOut,
  BotSecretResetIn,
} from '@/api/types';

export async function checkAppIdAvailable(appId: string): Promise<AppIdCheckResult> {
  const res = await apiClient.get<AppIdCheckResult>('/bots/check-app-id', {
    params: { app_id: appId },
  });
  return res.data;
}

export async function updateFeishuCredentials(
  name: string,
  payload: BotFeishuCredentialsIn,
): Promise<BotOut> {
  const res = await apiClient.patch<BotOut>(
    `/bots/${encodeURIComponent(name)}/feishu-credentials`,
    payload,
  );
  return res.data;
}

export async function updateBotFeishuPolicy(
  name: string,
  payload: BotFeishuPolicyPayload,
): Promise<BotOut> {
  const res = await apiClient.patch<BotOut>(
    `/bots/${encodeURIComponent(name)}/feishu-policy`,
    payload,
  );
  return res.data;
}

export async function resetBotSecret(name: string, payload: BotSecretResetIn): Promise<BotOut> {
  const res = await apiClient.patch<BotOut>(`/bots/${name}/secret`, payload);
  return res.data;
}
