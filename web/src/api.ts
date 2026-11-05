export interface Card {
  id: string
  title: string
  body: string
  added_at: string
}

export interface Detail {
  id: string
  title: string
  facts: string
  interpretation: string
  genre: string
  original_title: string
  url: string
  source: string
  published_at: string | null
  added_at: string
}

export interface Page {
  items: Card[]
  next_cursor: string | null
}

export type Provider = 'openai' | 'anthropic' | 'claude_cli' | 'codex_cli' | 'gemini_cli'

export interface SettingsValues {
  interval_minutes: number
  items_per_run: number
  retention_hours: number
  llm_provider: Provider
  llm_model: string
}

export interface Run {
  started_at: string
  finished_at: string | null
  status: 'running' | 'ok' | 'error'
  target: number
  posted: number
  found: number
  found_target: number
  error: string | null
  search_error: string | null
}

export interface GenreRow {
  id: string
  name: string
  weight: number
}

export interface Settings extends SettingsValues {
  default_models: Partial<Record<Provider, string>>
  openai_base_url: string
  keys: { openai: boolean; anthropic: boolean }
  refresh_requested_at: string | null
  last_run: Run | null
  genres: GenreRow[]
}

export interface GenreValues {
  name: string
  latest: boolean
  sites: string[]
  focus_terms: string[]
  exclude_terms: string[]
  discovery_percent: number
  min_chars: number
  max_chars: number
  stance: number
}

export interface Genre extends GenreValues {
  id: string
  weight: number
}

export class ApiError extends Error {
  constructor(public status: number) {
    super(`HTTP ${status}`)
  }
}

async function request<T>(path: string, init?: { method: string; body?: unknown }): Promise<T> {
  const response = await fetch(path, {
    method: init?.method ?? 'GET',
    credentials: 'same-origin',
    cache: 'no-store',
    headers: init?.body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: init?.body === undefined ? undefined : JSON.stringify(init.body),
  })
  if (!response.ok) throw new ApiError(response.status)
  return (response.status === 204 ? undefined : await response.json()) as T
}

export const api = {
  timeline: (cursor?: string) =>
    request<Page>(`/v1/timeline${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`),
  item: (id: string) => request<Detail>(`/v1/timeline/${encodeURIComponent(id)}`),
  settings: () => request<Settings>('/v1/settings'),
  saveSettings: (values: SettingsValues) => request<Settings>('/v1/settings', { method: 'PUT', body: values }),
  refresh: () => request<void>('/v1/refresh', { method: 'POST' }),
  genre: (id: string) => request<Genre>(`/v1/genres/${encodeURIComponent(id)}`),
  createGenre: (values: GenreValues) =>
    request<Genre>('/v1/genres', { method: 'POST', body: values }),
  saveGenre: (id: string, values: GenreValues) =>
    request<Genre>(`/v1/genres/${encodeURIComponent(id)}`, { method: 'PUT', body: values }),
  setWeight: (id: string, weight: number) =>
    request<{ genres: GenreRow[] }>(`/v1/genres/${encodeURIComponent(id)}`, { method: 'PATCH', body: { weight } }),
  deleteGenre: (id: string) => request<void>(`/v1/genres/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}
