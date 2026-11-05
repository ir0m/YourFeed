import { useEffect, useState } from 'react'
import { api, ApiError } from './api'
import type { Genre } from './api'
import { Link, navigate } from './router'

const STANCES = ['事実のみ', '事実中心', '半々', '解釈多め']

interface Form {
  name: string
  sites: string
  focus_terms: string
  discovery_percent: string
  exclude_terms: string
  min_chars: string
  max_chars: string
  stance: number
}

const EMPTY: Form = {
  name: '', sites: '', focus_terms: '', discovery_percent: '20', exclude_terms: '',
  min_chars: '100', max_chars: '160', stance: 1,
}

const toForm = (genre: Genre): Form => ({
  name: genre.name, sites: genre.sites.join('\n'), focus_terms: genre.focus_terms.join('\n'),
  discovery_percent: String(genre.discovery_percent), exclude_terms: genre.exclude_terms.join('\n'),
  min_chars: String(genre.min_chars), max_chars: String(genre.max_chars), stance: genre.stance,
})

const lines = (value: string) => value.split('\n').map((line) => line.trim()).filter(Boolean)

/** Same rule as the server: a host with a dot, over http(s), without credentials. */
function validSite(value: string): boolean {
  try {
    const url = new URL(value.includes('://') ? value : `https://${value}`)
    return ['http:', 'https:'].includes(url.protocol) && url.hostname.includes('.') && !url.username
      && !/\s/.test(value)
  } catch {
    return false
  }
}

export default function GenreEdit({ id }: { id: string }) {
  const isNew = id === 'new'
  const [form, setForm] = useState<Form | null>(isNew ? EMPTY : null)
  const [invalid, setInvalid] = useState<string[]>([])
  const [message, setMessage] = useState('')
  const [saving, setSaving] = useState(false)
  const [missing, setMissing] = useState(false)

  useEffect(() => {
    if (isNew) { setForm(EMPTY); return }
    api.genre(id).then((genre) => setForm(toForm(genre))).catch(() => setMissing(true))
  }, [id])

  const back = <nav className="top top-start"><Link to="/settings">戻る</Link></nav>
  if (missing) return <main className="page settings">{back}<p>見つかりません。</p></main>
  if (!form) return <main className="page settings" aria-busy="true">{back}</main>

  const set = (key: keyof Form) => (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm({ ...form, [key]: event.target.value })

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!form) return
    const sites = lines(form.sites)
    const wrong = sites.filter((site) => !validSite(site))
    setInvalid(wrong)
    const minChars = Number(form.min_chars)
    const maxChars = Number(form.max_chars)
    if (wrong.length) {
      setMessage('')
      return
    }
    if (maxChars < minChars + 20) {
      setMessage('文字数の幅は20字以上にしてください')
      return
    }
    const values = {
      name: form.name.trim(), sites, focus_terms: lines(form.focus_terms),
      discovery_percent: Number(form.discovery_percent), exclude_terms: lines(form.exclude_terms),
      min_chars: minChars, max_chars: maxChars, stance: form.stance,
    }
    setSaving(true)
    setMessage('')
    try {
      if (isNew) await api.createGenre(values)
      else await api.saveGenre(id, values)
      navigate('/settings', true)
    } catch (error) {
      setMessage(error instanceof ApiError && error.status === 409 ? '同じ名前のジャンルがあります' : '保存できません')
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    if (!form || !confirm(`「${form.name}」を削除しますか`)) return
    try {
      await api.deleteGenre(id)
      navigate('/settings', true)
    } catch {
      setMessage('削除できません')
    }
  }

  const hasTerms = lines(form.focus_terms).length > 0

  return <main className="page settings">
    {back}
    <form onSubmit={save}>
      <div className="group">
        <label className="field"><span>名前</span>
          <input className="text" required maxLength={40} value={form.name} onChange={set('name')} />
        </label>
      </div>

      <div className="group">
        <label className="field field-top"><span>サイト</span>
          <textarea rows={Math.max(2, lines(form.sites).length + 1)} spellCheck={false} wrap="off"
            value={form.sites} onChange={set('sites')} aria-invalid={invalid.length > 0} />
        </label>
        {invalid.length > 0 && <ul className="field-note" role="alert">
          {invalid.map((site) => <li key={site}>読み取れません　{site}</li>)}
        </ul>}
      </div>

      <div className="group">
        <label className="field field-top"><span>注目する語</span>
          <textarea rows={3} value={form.focus_terms} onChange={set('focus_terms')} />
        </label>
        {hasTerms && <label className="field"><span>注目語以外</span>
          <span className="with-unit"><input type="number" min={0} max={100} required value={form.discovery_percent}
            onChange={set('discovery_percent')} />%</span>
        </label>}
        <label className="field field-top"><span>除外する語</span>
          <textarea rows={2} value={form.exclude_terms} onChange={set('exclude_terms')} />
        </label>
      </div>

      <div className="group">
        <div className="field"><span>文字数</span>
          <span className="with-unit">
            <input type="number" min={40} max={800} required aria-label="最小の文字数" value={form.min_chars}
              onChange={set('min_chars')} />〜
            <input type="number" min={40} max={800} required aria-label="最大の文字数" value={form.max_chars}
              onChange={set('max_chars')} />字
          </span>
        </div>
        <div className="field" role="radiogroup" aria-labelledby="stance-label"><span id="stance-label">事実と解釈</span>
          <span className="segments">
            {STANCES.map((label, index) => <label key={label} className={form.stance === index ? 'selected' : ''}>
              <input type="radio" name="stance" checked={form.stance === index}
                onChange={() => setForm({ ...form, stance: index })} />
              {label}
            </label>)}
          </span>
        </div>
      </div>

      <div className="actions">
        <button type="submit" className="primary" disabled={saving}>{saving ? '保存中' : '保存'}</button>
        <span className="message" role="status">{message}</span>
        {!isNew && <button type="button" className="danger" onClick={() => void remove()}>削除</button>}
      </div>
    </form>
  </main>
}
