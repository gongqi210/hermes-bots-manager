export interface ProviderSearchSource {
  slug: string;
  name: string;
  source?: string | null;
}

interface ProviderSelectOption {
  label?: unknown;
  value?: unknown;
  searchText?: unknown;
}

const OPENAI_CODEX_ALIASES = [
  'chatgpt',
  'chatgpt codex',
  'codex auth',
  'openai codex',
];

export function providerSearchText(provider: ProviderSearchSource): string {
  const aliases = provider.slug === 'openai-codex' ? OPENAI_CODEX_ALIASES : [];
  return [provider.name, provider.slug, provider.source, ...aliases]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
}

export function providerOptionMatchesSearch(
  input: string,
  option?: ProviderSelectOption,
): boolean {
  const needle = input.trim().toLowerCase();
  if (!needle) return true;
  const value = String(option?.value ?? '').toLowerCase();
  const aliases = value === 'openai-codex' ? OPENAI_CODEX_ALIASES : [];
  const haystack = [option?.label, option?.value, option?.searchText, ...aliases]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return haystack.includes(needle);
}
