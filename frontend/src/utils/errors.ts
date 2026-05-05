/**
 * Extract a user-facing message from any error thrown by axios or thrown manually.
 *
 * Priority (W5 harmonization across all modals):
 *   1. response.data.detail (FastAPI HTTPException convention)
 *   2. Error.message
 *   3. fallback "Unknown error"
 */
export const extractErrorMessage = (e: unknown): string => {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  if (typeof detail === 'string' && detail.length > 0) return detail;
  const msg = (e as { message?: unknown })?.message;
  if (typeof msg === 'string' && msg.length > 0) return msg;
  return 'Unknown error';
};
