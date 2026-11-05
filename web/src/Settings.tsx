import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { GenreRow, Provider, Run, Settings as SettingsData, SettingsValues } from './api'
import { Link } from './router'

const INTERVALS: [number, string][] = [[15, '15分'], [30, '30分'], [60, '1時間'], [120, '2時間'],
  [180, '3時間'], [360, '6時間'], [720, '12時間'], [1440, '24時間']]
const RETENTION: [number, string][] = [[6, '6時間'], [12, '12時間'], [24, '24時間'], [48, '2日'],
  [72, '3日'], [168, '7日']]
const PROVIDERS: [Provider, string][] = [['openai', 'OpenAI互換API'], ['anthropic', 'Claude API'],
  ['claude_cli', 'Claude Code'], ['codex_cli', 'Codex'], ['gemini_cli', 'Gemini CLI']]
const ERRORS: Record<string, string> = {
  no_model: 'モデル未設定',
  http_400: 'AIがリクエストを拒否',
  http_401: 'APIキーが無効',
  http_403: 'APIキーが無効',
  http_404: 'モデルが見つかりません',
  http_429: 'AIの利用制限',
  cli_not_found: 'CLIが見つかりません',
  cli_error: 'CLIでエラー',
  connection_error: 'AIに接続できません',
  timeout: 'AIが応答しません',
  search_unavailable: '検索に接続できません',
  search_format: '検索がJSONに未対応',
  search_limited: '検索の回数制限',
  search_timeout: '検索が応答しません',
  search_error: '検索でエラー',
  interrupted: '中断',
}
const timeFormat = new Intl.DateTimeFormat('ja-JP', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })

type Section = 'genres' | 'update' | 'ai'
type Form = { [K in keyof SettingsValues]: string }

const toForm = (settings: SettingsValues): Form => ({
  interval_minutes: String(settings.interval_minutes), items_per_run: String(settings.items_per_run),
  retention_hours: String(settings.retention_hours), llm_provider: settings.llm_provider,
  llm_model: settings.llm_model,
})

const toValues = (form: Form): SettingsValues => ({
  interval_minutes: Number(form.interval_minutes), items_per_run: Number(form.items_per_run),
  retention_hours: Number(form.retention_hours), llm_provider: form.llm_provider as Provider,
  llm_model: form.llm_model.trim(),
})

/** Posts per update for each genre: largest remainder over genres that have a weight. */
function planned(genres: GenreRow[], weights: Record<string, string>, total: number): Record<string, number> {
  const active = genres.filter((genre) => Number(weights[genre.id]) > 0)
  const sum = active.reduce((acc, genre) => acc + Number(weights[genre.id]), 0)
  const counts: Record<string, number> = Object.fromEntries(genres.map((genre) => [genre.id, 0]))
  if (!sum || total <= 0) return counts
  const exact = active.map((genre, index) => ({ id: genre.id, index, value: total * Number(weights[genre.id]) / sum }))
  exact.forEach((entry) => { counts[entry.id] = Math.floor(entry.value) })
  let left = total - exact.reduce((acc, entry) => acc + Math.floor(entry.value), 0)
  for (const entry of [...exact].sort((a, b) => (b.value % 1) - (a.value % 1) || a.index - b.index)) {
    if (left-- <= 0) break
    counts[entry.id] += 1
  }
  return counts
}

function runText(run: Run | null): string {
  if (!run) return '未更新'
  if (run.status === 'running') return '更新中'
  const when = timeFormat.format(new Date(run.finished_at ?? run.started_at))
  const errors = [run.search_error, run.error].filter((code): code is string => Boolean(code))
  const problem = errors.map((code) => `　${ERRORS[code] ?? code}`).join('')
  if (run.status === 'error') return `${when}　失敗${problem}`
  const count = run.posted < run.target ? `${run.posted}/${run.target}件` : `${run.posted}件`
  const found = run.found < run.found_target ? `　候補 ${run.found}/${run.found_target}` : ''
  return `${when}　${count}${found}${problem}`
}

function options(list: [number, string][], current: string, unit: string) {
  const all = list.some(([value]) => String(value) === current) ? list : [...list, [Number(current), `${current}${unit}`] as [number, string]]
  return all.map(([value, label]) => <option key={value} value={value}>{label}</option>)
}

const blurOnEnter = (event: React.KeyboardEvent<HTMLInputElement>) => {
  if (event.key === 'Enter') event.currentTarget.blur()
}

export default function Settings() {
  const [data, setData] = useState<SettingsData | null>(null)
  const [form, setForm] = useState<Form | null>(null)
  const [weights, setWeights] = useState<Record<string, string>>({})
  const [failed, setFailed] = useState<Section | 'load' | null>(null)
  const [waiting, setWaiting] = useState(false)
  const saved = useRef<Form | null>(null)
  const poll = useRef<number | null>(null)

  useEffect(() => {
    api.settings().then((settings) => {
      setData(settings)
      setForm(toForm(settings))
      saved.current = toForm(settings)
      setWeights(Object.fromEntries(settings.genres.map((genre) => [genre.id, String(genre.weight)])))
    }).catch(() => setFailed('load'))
    return () => { if (poll.current) window.clearInterval(poll.current) }
  }, [])

  if (failed === 'load') return <main className="page"><p>接続できません。</p></main>
  if (!data || !form) return <main className="page" aria-busy="true" />

  async function commit(next: Form, section: Section) {
    if (saved.current && JSON.stringify(next) === JSON.stringify(saved.current)) return
    const target = document.querySelector<HTMLFormElement>(`form[data-section="${section}"]`)
    if (target && !target.checkValidity()) {
      setFailed(section)
      return
    }
    try {
      const result = await api.saveSettings(toValues(next))
      saved.current = toForm(result)
      setData(result)
      setFailed(null)
    } catch {
      setFailed(section)
    }
  }

  const change = (key: keyof Form, section: Section, now = false) =>
    (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      const next = { ...form, [key]: event.target.value }
      setForm(next)
      if (now) void commit(next, section)
    }
  const blur = (section: Section) => () => { if (form) void commit(form, section) }

  async function saveWeight(genre: GenreRow) {
    const value = Number(weights[genre.id])
    if (!Number.isInteger(value) || value < 0 || value > 100) {
      setFailed('genres')
      return
    }
    if (value === genre.weight) return
    try {
      const result = await api.setWeight(genre.id, value)
      setData((current) => current && { ...current, genres: result.genres })
      setFailed(null)
    } catch {
      setFailed('genres')
    }
  }

  async function refresh() {
    setWaiting(true)
    try {
      await api.refresh()
    } catch {
      setWaiting(false)
      return
    }
    const requested = Date.now()
    if (poll.current) window.clearInterval(poll.current)
    poll.current = window.setInterval(async () => {
      try {
        const latest = await api.settings()
        setData((current) => current && { ...current, last_run: latest.last_run })
        const run = latest.last_run
        const finished = run && run.status !== 'running' && new Date(run.started_at).getTime() >= requested - 60_000
        if (finished || Date.now() - requested > 30 * 60_000) {
          if (poll.current) window.clearInterval(poll.current)
          setWaiting(false)
        }
      } catch { /* keep polling */ }
    }, 5000)
  }

  const provider = form.llm_provider as Provider
  const counts = planned(data.genres, weights, Number(form.items_per_run) || 0)
  const heading = (title: string, section: Section) => <div className="group-head">
    <h2>{title}</h2>
    {failed === section && <span className="message" role="alert">保存できません</span>}
  </div>

  return <main className="page settings">
    <nav className="top top-start"><Link to="/">戻る</Link></nav>

    <section className="group">
      {heading('ジャンル', 'genres')}
      {data.genres.length > 0 && <>
        <div className="genre-columns" aria-hidden="true"><span /><span>比率</span><span>件数</span></div>
        <ul className="genres">
          {data.genres.map((genre) => <li key={genre.id} className={counts[genre.id] ? '' : 'off'}>
            <Link to={`/settings/genres/${genre.id}`} className="genre-name">{genre.name}</Link>
            <input type="number" min={0} max={100} required aria-label={`${genre.name}の比率`}
              value={weights[genre.id] ?? ''} onKeyDown={blurOnEnter}
              onChange={(event) => setWeights({ ...weights, [genre.id]: event.target.value })}
              onBlur={() => void saveWeight(genre)} />
            <span className="count">{counts[genre.id]}件</span>
          </li>)}
        </ul>
      </>}
      <Link to="/settings/genres/new" className="button">ジャンルを追加</Link>
    </section>

    <form className="group" data-section="update" onSubmit={(event) => event.preventDefault()}>
      {heading('更新', 'update')}
      <label className="field"><span>更新間隔</span>
        <select value={form.interval_minutes} onChange={change('interval_minutes', 'update', true)}>
          {options(INTERVALS, form.interval_minutes, '分')}
        </select>
      </label>
      <label className="field"><span>1回の件数</span>
        <span className="with-unit"><input type="number" min={1} max={50} required value={form.items_per_run}
          onChange={change('items_per_run', 'update')} onBlur={blur('update')} onKeyDown={blurOnEnter} />件</span>
      </label>
      <label className="field"><span>表示期間</span>
        <select value={form.retention_hours} onChange={change('retention_hours', 'update', true)}>
          {options(RETENTION, form.retention_hours, '時間')}
        </select>
      </label>
    </form>

    <form className="group" data-section="ai" onSubmit={(event) => event.preventDefault()}>
      {heading('AI', 'ai')}
      <label className="field"><span>接続先</span>
        <select value={form.llm_provider} onChange={change('llm_provider', 'ai', true)}>
          {PROVIDERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label className="field"><span>モデル</span>
        <input className="text" spellCheck={false} value={form.llm_model} placeholder={data.default_models[provider] ?? ''}
          onChange={change('llm_model', 'ai')} onBlur={blur('ai')} onKeyDown={blurOnEnter} />
      </label>
      {provider === 'openai' && <div className="field"><span>URL</span>
        <span className="value url">{data.openai_base_url}</span>
      </div>}
      {(provider === 'openai' || provider === 'anthropic') && <div className="field"><span>APIキー</span>
        <span className="value">{data.keys[provider] ? '設定済み' : '未設定'}</span>
      </div>}
    </form>

    <section className="group status">
      <span>{waiting ? '更新中' : runText(data.last_run)}</span>
      <button type="button" onClick={() => void refresh()} disabled={waiting}>今すぐ更新</button>
    </section>
  </main>
}
