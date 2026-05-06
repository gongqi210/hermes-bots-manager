export type Role = 'Owner' | 'Admin' | 'Editor' | 'Viewer';

export interface UserOut {
  id: number;
  username: string;
  role: Role;
  created_at: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: 'bearer';
  access_expires_in: number;
  refresh_expires_in: number;
}

export interface LoginResponse {
  user: UserOut;
  tokens: TokenPair;
}

export interface AccessTokenOut {
  access_token: string;
  token_type: 'bearer';
  access_expires_in: number;
}

export interface ApiError {
  detail: string;
}

const RANK: Record<Role, number> = { Viewer: 0, Editor: 1, Admin: 2, Owner: 3 };

export function roleAtLeast(current: Role | null | undefined, minimum: Role): boolean {
  if (!current) return false;
  return RANK[current] >= RANK[minimum];
}

// --- Bot domain types (Phase 2-05) ---
// Backend contract: backend/src/app/schemas/bot.py + plan 02-04 SUMMARY.

export type BotStatus = 'green' | 'yellow' | 'red' | 'grey';

export interface BotOut {
  id: number;
  name: string;
  feishu_app_id: string | null;
  feishu_app_secret_last4: string | null;
  model_name: string | null;
  tags: string[];
  skills_count: number;
  today_message_count: number;
  last_heartbeat_at: string | null;
  status: BotStatus;
  why: string;
  last_active_at: string | null;
  created_at: string;
  domain?: 'feishu' | 'lark';
  connection_mode?: 'websocket';
  group_strategy?: 'mention' | 'block' | 'all';
}

export interface BotCreateIn {
  name: string;
  feishu_app_id?: string | null;
  feishu_app_secret?: string | null;
  tags?: string[];
}

export interface BotCloneIn {
  new_name: string;
}

export interface BotRenameIn {
  new_name: string;
}

export interface BotDeleteIn {
  confirm_name: string;
}

// --- Wizard types (Phase 3) ---

export interface BotWizardCreateIn extends BotCreateIn {
  domain: 'feishu' | 'lark';
  connection_mode: 'websocket';
  group_strategy: 'mention' | 'block' | 'all';
}

export interface WizardSSEEvent {
  step: 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7;
  status: 'pending' | 'running' | 'success' | 'error' | 'done';
  message: string;
  duration_ms?: number;
  error?: string;
  fix_hint?: string;
}

export interface AppIdCheckResult {
  available: boolean;
  conflict_bot: string | null;
}

export interface BotSecretResetIn {
  feishu_app_secret: string; // plaintext; backend schema uses SecretStr
}

export interface BotFeishuPolicyPayload {
  group_strategy: 'mention' | 'block' | 'all';
}

export interface BotFeishuCredentialsIn {
  feishu_app_id: string;
  feishu_app_secret: string; // plaintext → backend SecretStr
  domain?: 'feishu' | 'lark';
  connection_mode?: 'websocket';
  group_strategy?: 'mention' | 'block' | 'all';
}

export interface LarkInitSSEEvent {
  type: 'line' | 'url' | 'missing' | 'timeout' | 'done';
  text?: string;
  url?: string;
}

// --- Gateway / Pairing / Allowlist / WS-token / Onboarding (Phase 4) ---

export type GatewayState =
  | 'running'
  | 'starting'
  | 'stopped'
  | 'error'
  | 'unconfigured';

export interface GatewayStatusOut {
  bot_name: string;
  state: GatewayState;
  why: string;
  last_state_changed_at: string | null;
  pid: number | null;
  active_profile: string | null;
  is_active_profile: boolean;
}

export interface GatewayActionResponse {
  bot_name: string;
  action: 'start' | 'stop' | 'restart';
  new_state: GatewayState;
  recent_log_tail: string[];
}

export interface AllowlistOut {
  bot_name: string;
  users: string[];
}

export interface WSTokenResponse {
  token: string;
  expires_in: number;
}

export type PairingStatus = 'pending' | 'approved' | 'rejected' | 'expired';

export interface PairingOut {
  id: number;
  bot_id: number;
  bot_name: string | null;
  platform: string;
  code_last4: string;
  feishu_user_id: string | null;
  status: PairingStatus;
  intercepted_at: string;
  expires_at: string;
  processed_at: string | null;
  seconds_to_expiry: number | null;
}

export interface PairingActionResponse {
  id: number;
  status: 'approved' | 'rejected';
  message: string;
}

// --- Phase 5 management types ---

export interface ModelProviderOption {
  slug: string;
  name: string;
  is_current: boolean;
  is_user_defined: boolean;
  is_configured: boolean;
  models: string[];
  total_models: number;
  source: string;
  base_url: string | null;
  api_mode: string | null;
  auth_type: string | null;
}

export interface ModelConfigOut {
  bot_name: string;
  provider: string | null;
  model: string | null;
  base_url: string | null;
  api_mode: string | null;
  is_chatgpt_auth: boolean;
  provider_authorized: boolean;
  providers: ModelProviderOption[];
}

export interface ModelConfigUpdateIn {
  provider: string;
  model: string;
  base_url?: string | null;
  api_mode?: string | null;
}

export interface ChatgptAuthStartOut {
  authorization_url: string;
  process_id: number;
  message: string;
}

export type WorkspaceStatus = 'ok' | 'warning' | 'error' | 'unset';

export interface WorkspaceOut {
  bot_name: string;
  cwd: string | null;
  exists: boolean;
  is_directory: boolean;
  readable: boolean;
  writable: boolean;
  status: WorkspaceStatus;
  message: string;
}

export interface WorkspaceUpdateIn {
  cwd: string | null;
}

export interface SkillItem {
  name: string;
  category: string | null;
  description: string | null;
  source: 'profile' | 'global' | 'uploaded';
  enabled: boolean;
  dangerous: boolean;
  shadowed_source?: string | null;
  missing_deps?: string[];
  requires_tools?: string[];
}

export interface SkillsOut {
  bot_name: string;
  skills: SkillItem[];
  disabled: string[];
}

export interface SkillsUpdateIn {
  disabled: string[];
  confirm_name?: string | null;
}

export interface HealthOut {
  bot_name: string;
  gateway_state: GatewayState;
  gateway_why: string;
  model_configured: boolean;
  provider_authorized: boolean;
  workspace_status: WorkspaceStatus;
  skills_enabled: number;
  skills_total: number;
  dangerous_skill_count: number;
  shadowed_skill_count: number;
  allowlist_preset: 'open' | 'owner_admin' | 'custom';
  overall: 'ok' | 'warning' | 'error';
}

export type AllowlistPreset = 'open' | 'owner_admin' | 'custom';

export interface AllowlistPresetsOut {
  bot_name: string;
  open: string[];
  owner_admin: string[];
  custom: string[];
  owner_admin_warning: string | null;
}

export interface AuditEntry {
  id: number;
  actor_id: number | null;
  actor_ip: string | null;
  method: string;
  path: string;
  target_type: string | null;
  target_id: string | null;
  result: string;
  error: string | null;
  created_at: string;
}

export interface OnboardingRunOut {
  id: number;
  user_id: number;
  bot_id: number | null;
  started_at: string;
  login_at: string | null;
  wizard_done_at: string | null;
  gateway_running_at: string | null;
  first_pairing_approved_at: string | null;
  first_message_at: string | null;
  total_duration_ms: number | null;
  status: 'in_progress' | 'success' | 'failed' | 'expired';
  last_step: string | null;
}
