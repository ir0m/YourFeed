import { useEffect, useState } from 'react'
import { api, ApiError } from './api'
import type { Persona } from './api'
import { Link, navigate } from './router'

interface Form {
  name: string
  profile: string
  topics: string
  tone: string
  min_chars: string
  max_chars: string
}

const EMPTY: Form = { name: '', profile: '', topics: '', tone: '', min_chars: '40', max_chars: '140' }

const toForm = (persona: Persona): Form => ({
  name: persona.name, profile: persona.profile, topics: persona.topics, tone: persona.tone,
  min_chars: String(persona.min_chars), max_chars: String(persona.max_chars),
})

export default function PersonaEdit({ id }: { id: string }) {
  const isNew = id === 'new'
  const [form, setForm] = useState<Form | null>(isNew ? EMPTY : null)
  const [message, setMessage] = useState('')
  const [saving, setSaving] = useState(false)
  const [missing, setMissing] = useState(false)

  useEffect(() => {
    if (isNew) { setForm(EMPTY); return }
    api.persona(id).then((persona) => setForm(toForm(persona))).catch(() => setMissing(true))
  }, [id])

  const back = <nav className="top top-start"><Link to="/settings">戻る</Link></nav>
  if (missing) return <main className="page settings">{back}<p>見つかりません。</p></main>
  if (!form) return <main className="page settings" aria-busy="true">{back}</main>

  const set = (key: keyof Form) => (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm({ ...form, [key]: event.target.value })

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!form) return
    const minChars = Number(form.min_chars)
    const maxChars = Number(form.max_chars)
    if (maxChars < minChars + 10) {
      setMessage('文字数の幅は10字以上にしてください')
      return
    }
    const values = {
      name: form.name.trim(), profile: form.profile.trim(), topics: form.topics.trim(), tone: form.tone.trim(),
      min_chars: minChars, max_chars: maxChars,
    }
    setSaving(true)
    setMessage('')
    try {
      if (isNew) await api.createPersona(values)
      else await api.savePersona(id, values)
      navigate('/settings', true)
    } catch (error) {
      setMessage(error instanceof ApiError && error.status === 409 ? '同じ名前の人物がいます' : '保存できません')
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    if (!form || !confirm(`「${form.name}」を削除しますか。投稿も消えます`)) return
    try {
      await api.deletePersona(id)
      navigate('/settings', true)
    } catch {
      setMessage('削除できません')
    }
  }

  return <main className="page settings">
    {back}
    <form onSubmit={save}>
      <div className="group">
        <label className="field"><span>名前</span>
          <input className="text" required maxLength={40} value={form.name} onChange={set('name')} />
        </label>
        <label className="field field-top"><span>プロフィール</span>
          <textarea rows={4} maxLength={1000} value={form.profile} onChange={set('profile')}
            placeholder="例：東京で働く30代のエンジニア。休日は自転車で遠出する。" />
        </label>
        <label className="field field-top"><span>投稿内容</span>
          <textarea rows={4} maxLength={1000} value={form.topics} onChange={set('topics')}
            placeholder="例：仕事の小さな発見、週末に走った道、季節の食べもの" />
        </label>
        <label className="field field-top"><span>口調</span>
          <textarea rows={3} maxLength={1000} value={form.tone} onChange={set('tone')}
            placeholder="例：くだけた話し言葉。ときどき自分にツッコミを入れる。" />
        </label>
      </div>

      <div className="group">
        <div className="field"><span>文字数</span>
          <span className="with-unit">
            <input type="number" min={10} max={500} required aria-label="最小の文字数" value={form.min_chars}
              onChange={set('min_chars')} />〜
            <input type="number" min={10} max={500} required aria-label="最大の文字数" value={form.max_chars}
              onChange={set('max_chars')} />字
          </span>
        </div>
        <p className="note">投稿はAIが書きます。人物は架空で、記事の検索はしません。</p>
      </div>

      <div className="actions">
        <button type="submit" className="primary" disabled={saving}>{saving ? '保存中' : '保存'}</button>
        <span className="message" role="status">{message}</span>
        {!isNew && <button type="button" className="danger" onClick={() => void remove()}>削除</button>}
      </div>
    </form>
  </main>
}
