import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api } from './api'
import type { Card, Detail } from './api'
import { Link, navigate } from './router'

const UPDATING_MS = 5 * 1000
const IDLE_MS = 60 * 1000

const dateFormat = new Intl.DateTimeFormat('ja-JP', { year: 'numeric', month: 'numeric', day: 'numeric' })

function DetailDialog({ id, onClose, onClosed }: { id: string | null; onClose: () => void; onClosed: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'missing'>('loading')

  useLayoutEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (id && !dialog.open) dialog.showModal()
    if (!id && dialog.open) {
      dialog.close()
      onClosed()
    }
  }, [id])

  useEffect(() => {
    if (!id) return
    let cancelled = false
    setState('loading')
    api.item(id).then((item) => {
      if (!cancelled) { setDetail(item); setState('ready') }
    }).catch(() => { if (!cancelled) setState('missing') })
    return () => { cancelled = true }
  }, [id])

  return <dialog ref={ref} className="detail" aria-labelledby="detail-title"
    onCancel={(event) => { event.preventDefault(); onClose() }}>
    <button className="close" onClick={onClose} aria-label="閉じる">×</button>
    {state === 'missing' && <p>表示できません。</p>}
    {state === 'ready' && detail && <>
      <h2 id="detail-title">{detail.title}</h2>
      <p className="detail-text">{detail.facts}</p>
      {detail.interpretation && <div className="detail-interpretation">
        <p className="detail-label">解釈</p>
        <p className="detail-text">{detail.interpretation}</p>
      </div>}
      <div className="detail-meta">
        {detail.published_at && <p>{dateFormat.format(new Date(detail.published_at))}</p>}
        <p>{detail.original_title}</p>
        <p><a href={detail.url} target="_blank" rel="noopener noreferrer">{detail.source} ↗</a></p>
      </div>
    </>}
  </dialog>
}

export default function Timeline({ path }: { path: string }) {
  const [cards, setCards] = useState<Card[]>([])
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState(false)
  const cursor = useRef<string | null>(null)
  const seen = useRef(new Set<string>())
  const inFlight = useRef(false)
  const loaded = useRef(false)
  const updating = useRef(false)
  const keep = useRef<{ height: number; scrollY: number } | null>(null)
  const sentinel = useRef<HTMLDivElement>(null)
  const opener = useRef<{ element: HTMLElement | null; top: number; scrollY: number } | null>(null)
  const detailId = path.startsWith('/items/') ? path.split('/')[2] : null

  const load = useCallback(async (fromTop: boolean) => {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true)
    setError(false)
    try {
      const page = await api.timeline(fromTop ? undefined : cursor.current ?? undefined)
      const fresh = page.items.filter((item) => !seen.current.has(item.id))
      fresh.forEach((item) => seen.current.add(item.id))
      updating.current = page.updating
      if (fromTop && loaded.current) {
        if (!fresh.length) return
        // New posts go on top without moving what is being read.
        keep.current = window.scrollY > 0
          ? { height: document.documentElement.scrollHeight, scrollY: window.scrollY } : null
        setCards((current) => [...fresh, ...current])
      } else {
        cursor.current = page.next_cursor
        setDone(!page.next_cursor)
        if (fresh.length) setCards((current) => [...current, ...fresh])
      }
      loaded.current = true
    } catch {
      setError(true)
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }, [])

  useEffect(() => { void load(false) }, [load])

  useLayoutEffect(() => {
    const saved = keep.current
    if (!saved) return
    keep.current = null
    const grown = document.documentElement.scrollHeight - saved.height
    window.scrollTo({ top: saved.scrollY + grown, behavior: 'instant' })
  }, [cards])

  useEffect(() => {
    if (!sentinel.current) return
    const observer = new IntersectionObserver((entries) => {
      if (entries[0]?.isIntersecting && !busy && !done && !error && loaded.current) void load(false)
    }, { rootMargin: '500px 0px' })
    observer.observe(sentinel.current)
    return () => observer.disconnect()
  }, [cards.length, busy, done, error, load])

  useEffect(() => {
    let timer = 0
    let stopped = false
    const check = async () => { if (!document.hidden && loaded.current) await load(true) }
    const tick = async () => {
      await check()
      if (!stopped) timer = window.setTimeout(tick, updating.current ? UPDATING_MS : IDLE_MS)
    }
    timer = window.setTimeout(tick, UPDATING_MS)
    const visible = () => { void check() }
    document.addEventListener('visibilitychange', visible)
    return () => { stopped = true; window.clearTimeout(timer); document.removeEventListener('visibilitychange', visible) }
  }, [load])

  function open(id: string, element: HTMLElement) {
    opener.current = { element, top: element.getBoundingClientRect().top, scrollY: window.scrollY }
    navigate(`/items/${id}`)
  }

  function close() {
    if (opener.current) history.back()
    else navigate('/', true)
  }

  function restore() {
    const saved = opener.current
    if (!saved) return
    const element = saved.element
    const delta = element?.isConnected ? element.getBoundingClientRect().top - saved.top : null
    window.scrollTo({ top: delta === null ? saved.scrollY : window.scrollY + delta, behavior: 'instant' })
    element?.focus({ preventScroll: true })
    opener.current = null
  }

  return <main className="page" aria-busy={busy}>
    <nav className="top"><Link to="/settings">設定</Link></nav>
    <section className="cards">
      {cards.map((card) => <article className="card" key={card.id}>
        <button className="card-open" onClick={(event) => open(card.id, event.currentTarget)}>
          <span className="card-title">{card.title}</span>
          <span className="card-body">{card.body}</span>
        </button>
      </article>)}
    </section>
    {error && <div className="notice" role="alert">
      <span>接続できません。</span>
      <button onClick={() => void load(false)}>再試行</button>
    </div>}
    <div ref={sentinel} className="end" />
    <DetailDialog id={detailId} onClose={close} onClosed={restore} />
  </main>
}
