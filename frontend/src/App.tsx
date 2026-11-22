import { useCallback, useEffect, useState } from 'react'
import Dashboard from './pages/Dashboard'
import TenderWorkspace from './pages/TenderWorkspace'
import Companies from './pages/Companies'
import { parseTenderWorkspaceView, type TenderWorkspaceView } from './types/workspace'
import './style.css'

type Route = { page: 'dashboard' } | { page: 'companies' } | { page: 'workspace'; tenderId: string; view: TenderWorkspaceView }

function readRoute(): Route {
  if (/^\/companies\/?$/.test(window.location.pathname)) return { page: 'companies' }
  const match = window.location.pathname.match(/^\/tenders\/([^/]+)\/?$/)
  return match
    ? {
        page: 'workspace',
        tenderId: decodeURIComponent(match[1]),
        view: parseTenderWorkspaceView(new URLSearchParams(window.location.search).get('view')),
      }
    : { page: 'dashboard' }
}

export function App() {
  const [route, setRoute] = useState<Route>(readRoute)
  const [notice, setNotice] = useState('')

  const navigate = useCallback((path: string) => {
    window.history.pushState({}, '', path)
    setRoute(readRoute())
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [])

  useEffect(() => {
    const handlePopState = () => setRoute(readRoute())
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  useEffect(() => {
    if (!notice) return
    const timeout = window.setTimeout(() => setNotice(''), 4200)
    return () => window.clearTimeout(timeout)
  }, [notice])

  return (
    <div className="app-shell">
      {route.page === 'dashboard' ? (
        <Dashboard
          onOpenCompanies={() => navigate('/companies')}
          onOpenTender={(id) => navigate(`/tenders/${encodeURIComponent(id)}`)}
          onCreated={async (tender) => {
            setNotice(`Tender “${tender.name}” created.`)
            navigate(`/tenders/${encodeURIComponent(tender.id)}`)
          }}
        />
      ) : route.page === 'companies' ? (
        <Companies onBack={() => navigate('/')} />
      ) : (
        <TenderWorkspace
          key={route.tenderId}
          tenderId={route.tenderId}
          view={route.view}
          onViewChange={(view) => navigate(`/tenders/${encodeURIComponent(route.tenderId)}?view=${view}`)}
          onBack={() => navigate('/')}
          onDeleted={() => {
            setNotice('Tender workspace deleted.')
            navigate('/')
          }}
          onUpdated={(name) => setNotice(`“${name}” was updated.`)}
        />
      )}
      {notice && <div className="toast" role="status">{notice}</div>}
    </div>
  )
}

export default App
