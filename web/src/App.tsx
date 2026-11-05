import GenreEdit from './GenreEdit'
import { usePath } from './router'
import Settings from './Settings'
import Timeline from './Timeline'

export default function App() {
  const path = usePath()
  if (path.startsWith('/settings/genres/')) return <GenreEdit id={path.split('/')[3]} />
  if (path === '/settings') return <Settings />
  return <Timeline path={path} />
}
