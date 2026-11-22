import { useEffect, useState } from 'react'
import Brand from '../components/Brand'
import DocumentManager from '../components/DocumentManager'
import IntelligencePanel from '../components/IntelligencePanel'
import EvidenceMatchingPanel, { type EvidenceMatchingContext } from '../components/EvidenceMatchingPanel'
import StatusBadge from '../components/StatusBadge'
import TenderForm from '../components/TenderForm'
import { deleteTender, getTender, updateTender, type TenderDetail, type TenderUpdate } from '../services/tenders'
import { tenderWorkspaceViews, type TenderWorkspaceView } from '../types/workspace'

const viewContent: Record<TenderWorkspaceView, { eyebrow: string; title: string; description: string }> = {
  overview: {
    eyebrow: 'Workspace overview',
    title: 'Your tender workspace',
    description: 'A clear view of the tender, source documents and analysis work still to complete.',
  },
  documents: {
    eyebrow: 'Source material',
    title: 'Documents and extraction',
    description: 'Upload, organize and process tender files before running analysis or asking questions.',
  },
  'tender-analysis': {
    eyebrow: 'Analysis agent · 01',
    title: 'Tender analysis',
    description: 'Understand the procurement scope, important dates, BOQ, commercial terms and tender risks.',
  },
  'eligibility-analysis': {
    eyebrow: 'Analysis agent · 02',
    title: 'Eligibility analysis',
    description: 'Review bidder qualification criteria, required supporting evidence and assessment gaps.',
  },
  'compliance-analysis': {
    eyebrow: 'Analysis agent · 03',
    title: 'Compliance analysis',
    description: 'Review submission requirements, technical compliance, deadlines and post-award obligations.',
  },
  qa: {
    eyebrow: 'Tender assistant',
    title: 'Ask questions about this tender',
    description: 'Get focused answers from the processed tender documents, with citations you can verify.',
  },
  evidence: {
    eyebrow: 'Company document discovery',
    title: 'Company evidence matching',
    description: 'Find potentially relevant company documents for tender requirements. Candidate matches are not compliance decisions.',
  },
  readiness: {
    eyebrow: 'Readiness review',
    title: 'Bid readiness',
    description: 'Review the readiness summary, prioritized actions and persistent manual verification checklist.',
  },
}

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

export default function TenderWorkspace({
  tenderId,
  view,
  onViewChange,
  onBack,
  onDeleted,
  onUpdated,
}: {
  tenderId: string
  view: TenderWorkspaceView
  onViewChange: (view: TenderWorkspaceView) => void
  onBack: () => void
  onDeleted: () => void
  onUpdated: (name: string) => void
}) {
  const [tender, setTender] = useState<TenderDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const [savingDelete, setSavingDelete] = useState(false)
  const [documentCountError, setDocumentCountError] = useState('')
  const [evidenceMatchingContext, setEvidenceMatchingContext] = useState<EvidenceMatchingContext | null>(null)

  useEffect(() => {
    let current = true
    setLoading(true)
    setError('')
    void getTender(tenderId)
      .then((data) => { if (current) setTender(data) })
      .catch((reason: unknown) => {
        if (current) setError(reason instanceof Error ? reason.message : 'Tender workspace could not be loaded.')
      })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [tenderId])

  async function saveTender(payload: TenderUpdate) {
    const updated = await updateTender(tenderId, payload)
    setTender((current) => current ? { ...current, ...updated } : current)
    setEditing(false)
    onUpdated(updated.name)
  }

  async function refreshDocumentCount() {
    setDocumentCountError('')
    try {
      setTender(await getTender(tenderId))
    } catch {
      setDocumentCountError('Documents changed, but the tender document count could not be refreshed.')
    }
  }

  async function confirmDelete() {
    setSavingDelete(true)
    setDeleteError('')
    try {
      await deleteTender(tenderId)
      onDeleted()
    } catch (reason) {
      setDeleteError(reason instanceof Error ? reason.message : 'Tender workspace could not be deleted.')
      setSavingDelete(false)
    }
  }

  const activeView = viewContent[view]
  const intelligenceViews = ['documents', 'tender-analysis', 'eligibility-analysis', 'compliance-analysis', 'qa', 'readiness']
  const intelligenceView = intelligenceViews.includes(view)
    ? view as Extract<TenderWorkspaceView, 'documents' | 'tender-analysis' | 'eligibility-analysis' | 'compliance-analysis' | 'qa' | 'readiness'>
    : null

  return (
    <div className="page-wrap tender-workspace-shell">
      <header className="topbar">
        <Brand />
        <button className="back-link" onClick={onBack}><span aria-hidden="true">←</span> All tenders</button>
      </header>
      <main className={`workspace-main workspace-main-v2${view === 'qa' ? ' workspace-main-qa' : ''}`}>
        {loading ? (
          <div className="loading-state workspace-loading" role="status"><span className="spinner" /> Loading tender workspace…</div>
        ) : error ? (
          <section className="error-panel" role="alert">
            <p className="eyebrow">Workspace unavailable</p><h1>We couldn’t open this tender</h1><p>{error}</p>
            <button className="button button-secondary" onClick={onBack}>Back to tenders</button>
          </section>
        ) : tender ? (
          <>
            <nav className="breadcrumbs" aria-label="Breadcrumb">
              <button onClick={onBack}>Tender workspaces</button><span aria-hidden="true">/</span><span>{tender.name}</span>
            </nav>

            <section className="workspace-heading workspace-heading-v2">
              <div className="workspace-title-group">
                <p className="eyebrow">Tender workspace</p>
                <h1>{tender.name}</h1>
                <div className="workspace-title-meta">
                  <StatusBadge status={tender.status} />
                  {tender.reference_number && <span>Reference {tender.reference_number}</span>}
                  {tender.issuing_organization && <span>{tender.issuing_organization}</span>}
                </div>
              </div>
              <div className="workspace-actions">
                <button className="button button-secondary" onClick={() => setEditing(true)}>Edit details</button>
                <button className="button button-danger" onClick={() => { setDeleteError(''); setDeleting(true) }}>Delete tender</button>
              </div>
            </section>

            <div className="workspace-layout-v3">
              <aside className="workspace-sidebar-v3" aria-label="Tender workspace navigation">
                <div className="workspace-sidebar-topline">
                  <span className="workspace-sidebar-overline">TENDER WORKSPACE</span>
                  <span className="workspace-sidebar-reference">{tender.reference_number || 'Workspace'}</span>
                </div>
                <nav className="workspace-nav-v3" aria-label="Tender workspace navigation">
                  <div className="workspace-nav-group">
                    <span className="workspace-nav-group-label">WORKSPACE</span>
                    {tenderWorkspaceViews.filter((item) => (['overview', 'documents'] as string[]).includes(item.id)).map((item) => (
                      <button type="button" key={item.id} className={`workspace-nav-v3-item${view === item.id ? ' is-active' : ''}`} aria-current={view === item.id ? 'page' : undefined} title={item.description} onClick={() => onViewChange(item.id)}>
                        <span className={`workspace-nav-v3-icon nav-icon-${item.id}`} aria-hidden="true">{item.id === 'overview' ? '⌂' : '▤'}</span>
                        <span>{item.label}</span>
                        {item.id === 'documents' && <span className="workspace-nav-count-v3" aria-hidden="true">{tender.document_count}</span>} 
                      </button>
                    ))}
                  </div>
                  <div className="workspace-nav-group">
                    <span className="workspace-nav-group-label">ANALYSIS</span>
                    {tenderWorkspaceViews.filter((item) => (['tender-analysis', 'eligibility-analysis', 'compliance-analysis'] as string[]).includes(item.id)).map((item) => (
                      <button type="button" key={item.id} className={`workspace-nav-v3-item${view === item.id ? ' is-active' : ''}`} aria-current={view === item.id ? 'page' : undefined} title={item.description} onClick={() => onViewChange(item.id)}>
                        <span className="workspace-nav-v3-icon" aria-hidden="true">{item.id === 'tender-analysis' ? '◈' : item.id === 'eligibility-analysis' ? '✓' : '≋'}</span>
                        <span>{item.label}</span>
                      </button>
                    ))}
                  </div>
                  <div className="workspace-nav-group">
                    <span className="workspace-nav-group-label">REVIEW & SUBMISSION</span>
                    {tenderWorkspaceViews.filter((item) => (['qa', 'evidence', 'readiness'] as string[]).includes(item.id)).map((item) => (
                      <button type="button" key={item.id} className={`workspace-nav-v3-item${view === item.id ? ' is-active' : ''}`} aria-current={view === item.id ? 'page' : undefined} title={item.description} onClick={() => onViewChange(item.id)}>
                        <span className="workspace-nav-v3-icon" aria-hidden="true">{item.id === 'qa' ? '✳' : item.id === 'evidence' ? '⌕' : '☑'}</span>
                        <span>{item.label}</span>
                      </button>
                    ))}
                  </div>
                </nav>
                <div className="workspace-sidebar-note-v3">
                  <span aria-hidden="true">✦</span>
                  <div><strong>Evidence-led workflow</strong><p>Use source documents to verify each finding before submission.</p></div>
                </div>
              </aside>

              <div className="workspace-content-v3">
            <section className={`workspace-view-header${view === 'overview' ? ' workspace-view-header-overview' : ''}${view === 'qa' ? ' workspace-view-header-qa' : ''}`}>
              <div>
                <p className="eyebrow">{activeView.eyebrow}</p>
                <h2>{activeView.title}</h2>
                <p>{activeView.description}</p>
              </div>
              {view === 'readiness' && evidenceMatchingContext && (
                <div className="workspace-context-pill">Company evidence assessment linked</div>
              )}
            </section>

            {view === 'overview' && (
              <div className="workspace-overview-v2">
                <section className="overview-metrics-v2" aria-label="Tender summary">
                  <article className="overview-metric-v2">
                    <span className="overview-metric-icon">▤</span>
                    <div><strong>{tender.document_count}</strong><span>Uploaded documents</span></div>
                  </article>
                  <article className="overview-metric-v2">
                    <span className="overview-metric-icon">⌁</span>
                    <div><strong>{tender.reference_number || 'Not set'}</strong><span>Tender reference</span></div>
                  </article>
                  <article className="overview-metric-v2">
                    <span className="overview-metric-icon">◷</span>
                    <div><strong>{formatDate(tender.updated_at)}</strong><span>Last updated</span></div>
                  </article>
                </section>

                <div className="overview-columns-v2">
                  <section className="content-card overview-info-v2">
                  <div className="overview-card-heading"><div><p className="eyebrow">Workspace information</p><h3>Tender details</h3></div><button className="text-button" aria-label="Edit tender details" onClick={() => setEditing(true)}>Edit details</button></div>
                    <dl className="overview-details-list">
                      <div><dt>Issuing organization</dt><dd>{tender.issuing_organization || 'Not provided'}</dd></div>
                      <div><dt>Department</dt><dd>{tender.department || 'Not provided'}</dd></div>
                      <div className="overview-detail-wide"><dt>Description</dt><dd>{tender.description || 'No description added.'}</dd></div>
                      <div><dt>Created</dt><dd>{formatDate(tender.created_at)}</dd></div>
                      <div><dt>Last updated</dt><dd>{formatDate(tender.updated_at)}</dd></div>
                    </dl>
                  </section>

                  <aside className="content-card overview-next-v2">
                    <p className="eyebrow">Suggested next step</p>
                    <h3>Prepare the tender evidence</h3>
                    <p>Process the uploaded tender documents before running the analysis agents or asking evidence-based questions.</p>
                    <div className="overview-next-checks">
                      <div><span>01</span><strong>Review source files</strong></div>
                      <div><span>02</span><strong>Run the three analysis agents</strong></div>
                      <div><span>03</span><strong>Match company evidence</strong></div>
                      <div><span>04</span><strong>Generate readiness report</strong></div>
                    </div>
                    <button className="button button-primary" onClick={() => onViewChange('documents')}>Open documents <span aria-hidden="true">→</span></button>
                  </aside>
                </div>

                <section className="overview-tools-v2">
                  <div className="overview-tools-heading"><div><p className="eyebrow">Tender tools</p><h3>Choose where to work</h3></div><span>Each tool opens in its own workspace view</span></div>
                  <div className="overview-tools-grid">
                    {tenderWorkspaceViews.filter((item) => item.id !== 'overview').map((item, index) => (
                      <button type="button" className="overview-tool-card" key={item.id} onClick={() => onViewChange(item.id)}>
                        <span className="overview-tool-number">{String(index + 1).padStart(2, '0')}</span>
                        <strong>{item.label}</strong>
                        <p>{item.description}</p>
                        <span className="overview-tool-open">Open section <span aria-hidden="true">→</span></span>
                      </button>
                    ))}
                  </div>
                </section>
              </div>
            )}

            {view === 'documents' && (
              <section className="workspace-feature-stack">
                <DocumentManager tenderId={tenderId} documentCount={tender.document_count} onDocumentsChanged={refreshDocumentCount} />
                {documentCountError && <p className="form-error document-count-error" role="alert">{documentCountError}</p>}
              </section>
            )}

            <section className="workspace-feature-stack" hidden={view !== 'evidence'}>
              <EvidenceMatchingPanel tenderId={tenderId} onRunSelected={setEvidenceMatchingContext} />
            </section>

            <IntelligencePanel tenderId={tenderId} evidenceMatchingContext={evidenceMatchingContext} view={intelligenceView} />

            {editing && (
              <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setEditing(false) }}>
                <section className="dialog-card dialog-wide" role="dialog" aria-modal="true" aria-labelledby="edit-tender-title">
                  <div className="dialog-heading"><div><p className="eyebrow">Workspace settings</p><h2 id="edit-tender-title">Edit tender details</h2></div><button type="button" className="icon-button" aria-label="Close dialog" onClick={() => setEditing(false)}>×</button></div>
                  <TenderForm tender={tender} submitLabel="Save changes" onCancel={() => setEditing(false)} onSubmit={saveTender} />
                </section>
              </div>
            )}
            {deleting && (
              <div className="dialog-backdrop" role="presentation">
                <section className="dialog-card confirm-card" role="alertdialog" aria-modal="true" aria-labelledby="delete-tender-title">
                  <div className="dialog-heading"><div><p className="eyebrow">Delete workspace</p><h2 id="delete-tender-title">Delete this tender?</h2></div><button type="button" className="icon-button" aria-label="Close dialog" onClick={() => setDeleting(false)}>×</button></div>
                  <p className="dialog-copy">“{tender.name}” and its document metadata will be permanently deleted. This action cannot be undone.</p>
                  {deleteError && <p className="form-error" role="alert">{deleteError}</p>}
                  <div className="form-actions"><button type="button" className="button button-secondary" onClick={() => setDeleting(false)} disabled={savingDelete}>Cancel</button><button type="button" className="button button-danger" onClick={() => void confirmDelete()} disabled={savingDelete}>{savingDelete ? 'Deleting…' : 'Delete tender'}</button></div>
                </section>
              </div>
            )}
              </div>
            </div>
          </>
        ) : null}
      </main>
    </div>
  )
}
