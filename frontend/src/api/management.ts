// Phase 5: typed REST client for management endpoints (model-config / workspace
// / skills / health) plus the audit list API.

import { apiClient } from '@/api/client';
import type {
  AllowlistOut,
  AllowlistPreset,
  AllowlistPresetsOut,
  AuditEntry,
  ChatgptAuthStartOut,
  HealthOut,
  ModelConfigOut,
  ModelConfigUpdateIn,
  SkillItem,
  SkillsOut,
  SkillsUpdateIn,
  WorkspaceOut,
  WorkspaceUpdateIn,
} from '@/api/types';

export async function getModelConfig(botName: string): Promise<ModelConfigOut> {
  const { data } = await apiClient.get<ModelConfigOut>(
    `/bots/${encodeURIComponent(botName)}/model-config`,
  );
  return data;
}

export async function putModelConfig(
  botName: string,
  payload: ModelConfigUpdateIn,
): Promise<ModelConfigOut> {
  const { data } = await apiClient.put<ModelConfigOut>(
    `/bots/${encodeURIComponent(botName)}/model-config`,
    payload,
  );
  return data;
}

export async function startChatgptAuth(
  botName: string,
): Promise<ChatgptAuthStartOut> {
  const { data } = await apiClient.post<ChatgptAuthStartOut>(
    `/bots/${encodeURIComponent(botName)}/model-config/chatgpt-auth/start`,
  );
  return data;
}

export async function getWorkspace(botName: string): Promise<WorkspaceOut> {
  const { data } = await apiClient.get<WorkspaceOut>(
    `/bots/${encodeURIComponent(botName)}/workspace`,
  );
  return data;
}

export async function putWorkspace(
  botName: string,
  payload: WorkspaceUpdateIn,
): Promise<WorkspaceOut> {
  const { data } = await apiClient.put<WorkspaceOut>(
    `/bots/${encodeURIComponent(botName)}/workspace`,
    payload,
  );
  return data;
}

export async function getSkills(botName: string): Promise<SkillsOut> {
  const { data } = await apiClient.get<SkillsOut>(
    `/bots/${encodeURIComponent(botName)}/skills`,
  );
  return data;
}

export async function putSkills(
  botName: string,
  payload: SkillsUpdateIn,
): Promise<SkillsOut> {
  const { data } = await apiClient.put<SkillsOut>(
    `/bots/${encodeURIComponent(botName)}/skills`,
    payload,
  );
  return data;
}

export async function uploadSkill(botName: string, file: File): Promise<SkillItem> {
  const form = new FormData();
  form.append('file', file);
  const { data } = await apiClient.post<SkillItem>(
    `/bots/${encodeURIComponent(botName)}/skills/upload`,
    form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );
  return data;
}

export async function getHealth(botName: string): Promise<HealthOut> {
  const { data } = await apiClient.get<HealthOut>(
    `/bots/${encodeURIComponent(botName)}/health`,
  );
  return data;
}

export interface AuditListParams {
  limit?: number;
  actor_id?: number;
  target_type?: string;
  target_id?: string;
  result?: string;
}

export async function listAudit(params?: AuditListParams): Promise<AuditEntry[]> {
  const { data } = await apiClient.get<AuditEntry[]>('/audit', { params });
  return data;
}

// ── Workspace Library (Mode B) ────────────────────────────────────────────────

export interface WorkspaceLibraryItem {
  id: number;
  path: string;
  label: string | null;
  registered_by: number | null;
  registered_at: string;
}

export interface WorkspaceReuseOption {
  bot_name: string;
  cwd: string;
}

export async function getWorkspaceLibrary(): Promise<WorkspaceLibraryItem[]> {
  const { data } = await apiClient.get<WorkspaceLibraryItem[]>('/workspace-library');
  return data;
}

export async function addWorkspaceLibraryEntry(payload: {
  path: string;
  label?: string;
}): Promise<WorkspaceLibraryItem> {
  const { data } = await apiClient.post<WorkspaceLibraryItem>('/workspace-library', payload);
  return data;
}

export async function deleteWorkspaceLibraryEntry(id: number): Promise<void> {
  await apiClient.delete(`/workspace-library/${id}`);
}

export async function getWorkspaceReuseOptions(
  botName: string,
): Promise<WorkspaceReuseOption[]> {
  const { data } = await apiClient.get<WorkspaceReuseOption[]>(
    `/bots/${encodeURIComponent(botName)}/workspace-options/reuse`,
  );
  return data;
}

// Phase 5 plan 05-05 — allowlist preset endpoints.
export async function getAllowlistPresets(
  botName: string,
): Promise<AllowlistPresetsOut> {
  const { data } = await apiClient.get<AllowlistPresetsOut>(
    `/bots/${encodeURIComponent(botName)}/allowlist/presets`,
  );
  return data;
}

export async function putAllowlistPreset(
  botName: string,
  preset: AllowlistPreset,
): Promise<AllowlistOut> {
  const { data } = await apiClient.put<AllowlistOut>(
    `/bots/${encodeURIComponent(botName)}/allowlist/preset`,
    { preset },
  );
  return data;
}
