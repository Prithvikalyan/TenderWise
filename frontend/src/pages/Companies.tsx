import { useCallback, useEffect, useMemo, useState } from 'react'
import Brand from '../components/Brand'
import {
  COMPANY_DOCUMENT_CATEGORIES,
  archiveCompanyDocument,
  createCompany,
  getCompanyExtractionUnits,
  getCompanyProcessingRun,
  listCompanies,
  listCompanyDocuments,
  listCompanyProcessingRuns,
  queueCompanyProcessingRun,
  updateCompanyDocumentVersionMetadata,
  uploadCompanyDocument,
  type Company,
  type CompanyDocument,
  type CompanyDocumentCategory,
  type CompanyDocumentVersion,
  type CompanyExtractionUnit,
  type CompanyProcessingRunDetail,
} from '../services/companies'

const categoryLabels: Record<CompanyDocumentCategory, string> = {
  business_registration: 'Business registration',
  gst_certificate: 'GST certificate',
  pan_card: 'PAN card',
  audited_financial_statements: 'Audited financial statements',
  turnover_certificate: 'Turnover certificate',
  purchase_order: 'Purchase order',
  work_order: 'Work order',
  completion_certificate: 'Completion certificate',
  non_blacklisting_declaration: 'Non-blacklisting declaration',
  technical_certification: 'Technical certification',
  license: 'License',
  other: 'Other',
}

type VersionProcessing = { status: string; extractionUnitCount: number; runId: string; safeMessage: string | null }
type PreviewState = { document: CompanyDocument; version: CompanyDocumentVersion; units: CompanyExtractionUnit[]; total: number }

function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function titleFromFilename(filename: string): string {
  return filename.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim()
}

function getSourcePage(unit: CompanyExtractionUnit): string {
  const page = unit.source_ref.page
  return typeof page === 'number' ? `Page ${page}` : 'Source location unavailable'
}

function isTerminal(status: string): boolean {
  return ['completed', 'partially_completed', 'failed'].includes(status)
}

function isTableRows(value: unknown): Array<{ cells?: Array<{ text?: string }> }> {
  if (!value || typeof value !== 'object' || !('rows' in value)) return []
  const rows = (value as { rows?: unknown }).rows
  return Array.isArray(rows) ? rows as Array<{ cells?: Array<{ text?: string }> }> : []
}

export default function Companies({ onBack }: { onBack: () => void }) {
  const [companies, setCompanies] = useState<Company[]>([])
  const [companyId, setCompanyId] = useState('')
  const [documents, setDocuments] = useState<CompanyDocument[]>([])
  const [processing, setProcessing] = useState<Record<string, VersionProcessing>>({})
  const [runs, setRuns] = useState<CompanyProcessingRunDetail[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [loadingCompanies, setLoadingCompanies] = useState(true)
  const [loadingDocuments, setLoadingDocuments] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [createName, setCreateName] = useState('')
  const [createBusy, setCreateBusy] = useState(false)
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadTitle, setUploadTitle] = useState('')
  const [uploadCategory, setUploadCategory] = useState<CompanyDocumentCategory>('other')
  const [uploadIssueDate, setUploadIssueDate] = useState('')
  const [uploadExpiryDate, setUploadExpiryDate] = useState('')
  const [uploadFinancialYear, setUploadFinancialYear] = useState('')
  const [uploadIssuer, setUploadIssuer] = useState('')
  const [uploadReference, setUploadReference] = useState('')
  const [uploadNotes, setUploadNotes] = useState('')
  const [uploadBusy, setUploadBusy] = useState(false)
  const [processingBusy, setProcessingBusy] = useState(false)
  const [activeRunId, setActiveRunId] = useState('')
  const [preview, setPreview] = useState<PreviewState | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [editing, setEditing] = useState<{ document: CompanyDocument; version: CompanyDocumentVersion } | null>(null)
  const [editIssueDate, setEditIssueDate] = useState('')
  const [editExpiryDate, setEditExpiryDate] = useState('')
  const [editFinancialYear, setEditFinancialYear] = useState('')
  const [editIssuer, setEditIssuer] = useState('')
  const [editReference, setEditReference] = useState('')
  const [editNotes, setEditNotes] = useState('')
  const [editBusy, setEditBusy] = useState(false)
  const [archivingDocumentId, setArchivingDocumentId] = useState('')

  const activeCompany = companies.find((company) => company.id === companyId) ?? null
  const selectableDocuments = useMemo(
    () => documents.filter((document) => !document.is_archived && document.current_version),
    [documents],
  )
  const selectedCount = selected.length
  const unprocessedCount = selectableDocuments.filter((document) => {
    const version = document.current_version
    const status = version ? processing[version.id]?.status : undefined
    return !status || status === 'failed'
  }).length

  const refreshCompanyData = useCallback(async (id: string) => {
    setLoadingDocuments(true)
    setError('')
    try {
      const [documentResult, runResult] = await Promise.all([
        listCompanyDocuments(id, { limit: 100, offset: 0 }),
        listCompanyProcessingRuns(id, { page: 1, limit: 20 }),
      ])
      setDocuments(documentResult.items)
      const details = await Promise.all(runResult.items.map((run) => getCompanyProcessingRun(id, run.id).catch(() => null)))
      const validRuns = details.filter((run): run is CompanyProcessingRunDetail => run !== null)
        .sort((a, b) => b.created_at.localeCompare(a.created_at))
      setRuns(validRuns)
      const statusByVersion: Record<string, VersionProcessing> = {}
      for (const run of validRuns) {
        for (const item of run.items) {
          const versionId = item.company_document_version_id
          if (!versionId || statusByVersion[versionId]) continue
          statusByVersion[versionId] = {
            status: item.status,
            extractionUnitCount: item.extraction_unit_count,
            runId: run.id,
            safeMessage: item.safe_message,
          }
        }
      }
      setProcessing(statusByVersion)
      setSelected((previous) => previous.filter((versionId) => documentResult.items.some((document) => document.current_version?.id === versionId)))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Company documents could not be loaded.')
    } finally {
      setLoadingDocuments(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    listCompanies({ limit: 100, offset: 0 }).then((result) => {
      if (!active) return
      setCompanies(result.items)
      if (!companyId && result.items.length) setCompanyId(result.items.find((item) => !item.is_archived)?.id ?? '')
      setLoadingCompanies(false)
    }).catch((reason: unknown) => {
      if (!active) return
      setError(reason instanceof Error ? reason.message : 'Companies could not be loaded.')
      setLoadingCompanies(false)
    })
    return () => { active = false }
  }, []) // Load companies once; selecting a company is handled below.

  useEffect(() => {
    if (!companyId) {
      setDocuments([])
      setRuns([])
      setProcessing({})
      return
    }
    void refreshCompanyData(companyId)
  }, [companyId, refreshCompanyData])

  useEffect(() => {
    if (!activeRunId || !companyId) return
    let active = true
    const poll = async () => {
      try {
        const run = await getCompanyProcessingRun(companyId, activeRunId)
        if (!active) return
        setRuns((previous) => [run, ...previous.filter((item) => item.id !== run.id)])
        setProcessing((previous) => {
          const next = { ...previous }
          for (const item of run.items) {
            if (!item.company_document_version_id) continue
            next[item.company_document_version_id] = {
              status: item.status,
              extractionUnitCount: item.extraction_unit_count,
              runId: run.id,
              safeMessage: item.safe_message,
            }
          }
          return next
        })
        if (isTerminal(run.status)) {
          setActiveRunId('')
          setNotice(`Processing run ${run.status.replace('_', ' ')}.`)
          await refreshCompanyData(companyId)
        }
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : 'Processing status could not be refreshed.')
      }
    }
    void poll()
    const timer = window.setInterval(() => { void poll() }, 1800)
    return () => { active = false; window.clearInterval(timer) }
  }, [activeRunId, companyId, refreshCompanyData])

  async function handleCreateCompany(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!createName.trim()) return
    setCreateBusy(true)
    setError('')
    try {
      const company = await createCompany({ legal_name: createName.trim() })
      setCompanies((previous) => [company, ...previous])
      setCompanyId(company.id)
      setCreateOpen(false)
      setCreateName('')
      setNotice('Company profile created.')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Company could not be created.')
    } finally {
      setCreateBusy(false)
    }
  }

  async function handleUpload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!companyId || !uploadFile || !uploadTitle.trim()) return
    setUploadBusy(true)
    setError('')
    try {
      await uploadCompanyDocument(companyId, {
        file: uploadFile,
        title: uploadTitle.trim(),
        category: uploadCategory,
        issue_date: uploadIssueDate || undefined,
        expiry_date: uploadExpiryDate || undefined,
        financial_year: uploadFinancialYear || undefined,
        issuer: uploadIssuer || undefined,
        reference_number: uploadReference || undefined,
        notes: uploadNotes || undefined,
      })
      setUploadFile(null)
      setUploadTitle('')
      setUploadIssueDate('')
      setUploadExpiryDate('')
      setUploadFinancialYear('')
      setUploadIssuer('')
      setUploadReference('')
      setUploadNotes('')
      setNotice('Document uploaded. Processing has not started; select it below when ready.')
      await refreshCompanyData(companyId)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Document upload failed.')
    } finally {
      setUploadBusy(false)
    }
  }

  async function handleQueue() {
    if (!companyId || !selected.length) return
    setProcessingBusy(true)
    setError('')
    try {
      const run = await queueCompanyProcessingRun(companyId, selected)
      setActiveRunId(run.id)
      setSelected([])
      setNotice(`${run.requested_document_count} document version(s) added to the processing queue.`)
      await refreshCompanyData(companyId)
      setActiveRunId(run.id)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Documents could not be queued.')
    } finally {
      setProcessingBusy(false)
    }
  }

  function toggleSelected(versionId: string, checked: boolean) {
    setSelected((previous) => checked ? [...new Set([...previous, versionId])] : previous.filter((item) => item !== versionId))
  }

  function selectUnprocessed() {
    const ids = selectableDocuments.map((document) => document.current_version!.id).filter((id) => {
      const status = processing[id]?.status
      return !status || status === 'failed'
    })
    setSelected(ids)
  }

  async function openExtraction(document: CompanyDocument, version: CompanyDocumentVersion) {
    setPreviewBusy(true)
    setPreview(null)
    setError('')
    try {
      const result = await getCompanyExtractionUnits(companyId, document.id, version.id)
      setPreview({ document, version, units: result.items, total: result.total })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Extracted content could not be loaded.')
    } finally {
      setPreviewBusy(false)
    }
  }

  function startEdit(document: CompanyDocument, version: CompanyDocumentVersion) {
    setEditing({ document, version })
    setEditIssueDate(version.issue_date ?? '')
    setEditExpiryDate(version.expiry_date ?? '')
    setEditFinancialYear(version.financial_year ?? '')
    setEditIssuer(version.issuer ?? '')
    setEditReference(version.reference_number ?? '')
    setEditNotes(version.notes ?? '')
  }

  async function removeCompanyDocument(document: CompanyDocument) {
    if (!companyId || archivingDocumentId) return
    const confirmed = window.confirm(
      `Remove "${document.title}" from the active company document library?\n\n` +
      'It will no longer be used for evidence matching. Existing file versions and processing history will be retained.'
    )
    if (!confirmed) return

    setArchivingDocumentId(document.id)
    setError('')
    try {
      await archiveCompanyDocument(companyId, document.id)
      if (document.current_version) {
        setSelected((previous) => previous.filter((versionId) => versionId !== document.current_version?.id))
      }
      if (preview?.document.id === document.id) setPreview(null)
      if (editing?.document.id === document.id) setEditing(null)
      setNotice(`"${document.title}" was removed from the active company document library. Its history was retained.`)
      await refreshCompanyData(companyId)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Company document could not be removed.')
    } finally {
      setArchivingDocumentId('')
    }
  }

  async function saveMetadata(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!editing || !companyId) return
    setEditBusy(true)
    setError('')
    try {
      await updateCompanyDocumentVersionMetadata(companyId, editing.document.id, editing.version.id, {
        issue_date: editIssueDate || null,
        expiry_date: editExpiryDate || null,
        financial_year: editFinancialYear || null,
        issuer: editIssuer || null,
        reference_number: editReference || null,
        notes: editNotes || null,
      })
      setEditing(null)
      setNotice('Document version metadata updated.')
      await refreshCompanyData(companyId)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Document metadata could not be updated.')
    } finally {
      setEditBusy(false)
    }
  }

  return (
    <div className="page-wrap">
      <header className="topbar">
        <Brand />
        <div className="topbar-actions">
          <span className="topbar-label">Company document repository</span>
          <button className="button button-secondary button-small" onClick={onBack}>← Tender workspaces</button>
        </div>
      </header>

      <main className="dashboard-main company-page">
        <section className="page-heading">
          <div>
            <p className="eyebrow">Reusable company evidence</p>
            <h1>Company documents</h1>
            <p className="page-subtitle">Manage company records, process selected versions, and inspect extracted evidence.</p>
          </div>
          <button className="button button-primary" onClick={() => setCreateOpen(true)}>+ New company</button>
        </section>

        {error && <div className="company-alert company-alert-error" role="alert"><span>{error}</span><button className="text-button" onClick={() => setError('')}>Dismiss</button></div>}
        {notice && <div className="company-alert company-alert-success" role="status"><span>{notice}</span><button className="text-button" onClick={() => setNotice('')}>Dismiss</button></div>}

        {loadingCompanies ? (
          <div className="loading-state" role="status"><span className="spinner" /> Loading companies…</div>
        ) : companies.length === 0 ? (
          <section className="content-card company-empty">
            <div className="empty-illustration" aria-hidden="true">C</div>
            <h2>Create your first company profile</h2>
            <p>Company documents are stored in a reusable repository, separate from individual tender workspaces.</p>
            <button className="button button-primary" onClick={() => setCreateOpen(true)}>Create company</button>
          </section>
        ) : (
          <>
            <section className="content-card company-selector-card">
              <label className="company-selector-label" htmlFor="company-select">Company profile</label>
              <select id="company-select" value={companyId} onChange={(event) => { setActiveRunId(''); setSelected([]); setCompanyId(event.target.value) }}>
                {companies.filter((company) => !company.is_archived).map((company) => (
                  <option key={company.id} value={company.id}>{company.display_name || company.legal_name}</option>
                ))}
              </select>
              {activeCompany && <p className="company-selector-meta">{activeCompany.legal_name}{activeCompany.registration_number ? ` · ${activeCompany.registration_number}` : ''}</p>}
            </section>

            {activeCompany ? (
              <>
                <section className="company-stats-grid" aria-label="Company document summary">
                  <div className="content-card company-stat"><span>Uploaded documents</span><strong>{documents.length}</strong></div>
                  <div className="content-card company-stat"><span>Ready to process</span><strong>{unprocessedCount}</strong></div>
                  <div className="content-card company-stat"><span>Processing runs</span><strong>{runs.length}</strong></div>
                </section>

                <section className="content-card company-upload-card">
                  <div className="company-section-heading">
                    <div><p className="eyebrow">Add to repository</p><h2>Upload company document</h2></div>
                    <span className="company-note">Upload does not automatically start processing.</span>
                  </div>
                  <form className="company-upload-form" onSubmit={handleUpload}>
                    <label className="field field-full"><span>Document file <b>*</b></span><input type="file" accept=".pdf,.docx,.xlsx,.csv" onChange={(event) => {
                      const file = event.target.files?.[0] ?? null
                      setUploadFile(file)
                      if (file && !uploadTitle) setUploadTitle(titleFromFilename(file.name))
                    }} required /></label>
                    <label className="field"><span>Document title <b>*</b></span><input value={uploadTitle} onChange={(event) => setUploadTitle(event.target.value)} required maxLength={255} placeholder="e.g. GST registration" /></label>
                    <label className="field"><span>Category <b>*</b></span><select value={uploadCategory} onChange={(event) => setUploadCategory(event.target.value as CompanyDocumentCategory)}>{COMPANY_DOCUMENT_CATEGORIES.map((category) => <option key={category} value={category}>{categoryLabels[category]}</option>)}</select></label>
                    <label className="field"><span>Issue date</span><input type="date" value={uploadIssueDate} onChange={(event) => setUploadIssueDate(event.target.value)} /></label>
                    <label className="field"><span>Expiry date</span><input type="date" value={uploadExpiryDate} onChange={(event) => setUploadExpiryDate(event.target.value)} /></label>
                    <label className="field"><span>Financial year</span><input value={uploadFinancialYear} onChange={(event) => setUploadFinancialYear(event.target.value)} maxLength={20} placeholder="e.g. 2025-26" /></label>
                    <label className="field"><span>Issuer</span><input value={uploadIssuer} onChange={(event) => setUploadIssuer(event.target.value)} maxLength={255} /></label>
                    <label className="field"><span>Reference number</span><input value={uploadReference} onChange={(event) => setUploadReference(event.target.value)} maxLength={255} /></label>
                    <label className="field field-full"><span>Notes</span><textarea value={uploadNotes} onChange={(event) => setUploadNotes(event.target.value)} maxLength={4000} rows={2} /></label>
                    <div className="company-form-actions field-full"><button className="button button-primary" type="submit" disabled={uploadBusy || !uploadFile}>{uploadBusy ? 'Uploading…' : 'Upload document'}</button></div>
                  </form>
                </section>

                <section className="content-card company-documents-card">
                  <div className="company-section-heading company-documents-heading">
                    <div><p className="eyebrow">Document library</p><h2>Uploaded documents</h2><p className="company-section-copy">Select document versions and send them to the shared extraction worker.</p></div>
                    <div className="company-selection-actions">
                      <button className="button button-secondary button-small" onClick={selectUnprocessed} disabled={!unprocessedCount}>Select unprocessed</button>
                      <button className="button button-secondary button-small" onClick={() => setSelected([])} disabled={!selected.length}>Clear</button>
                      <button className="button button-primary button-small" onClick={() => void handleQueue()} disabled={!selected.length || processingBusy}>{processingBusy ? 'Queueing…' : `Process selected (${selectedCount})`}</button>
                    </div>
                  </div>
                  {loadingDocuments ? <div className="loading-state company-loading" role="status"><span className="spinner" /> Loading company documents…</div> : documents.length === 0 ? (
                    <div className="company-empty-inline"><h3>No company documents yet</h3><p>Upload a reusable document above to add it to this company repository.</p></div>
                  ) : (
                    <div className="company-documents-list">
                      {documents.map((document) => {
                        const version = document.current_version
                        const state = version ? processing[version.id] : undefined
                        const canSelect = Boolean(version && !document.is_archived && (!state || state.status === 'failed'))
                        return (
                          <article className="company-document-row" key={document.id}>
                            <div className="company-document-check">{canSelect && version ? <input type="checkbox" aria-label={`Select ${document.title}`} checked={selected.includes(version.id)} onChange={(event) => toggleSelected(version.id, event.target.checked)} /> : <span className="company-check-spacer" />}</div>
                            <div className="company-file-icon" aria-hidden="true">{version?.extension.replace('.', '').toUpperCase().slice(0, 4) || 'DOC'}</div>
                            <div className="company-document-main">
                              <strong>{document.title}</strong>
                              <span>{version?.original_filename ?? 'No uploaded version'} · {version ? formatBytes(version.size_bytes) : '—'} · {categoryLabels[document.category]}</span>
                              <small>{version ? `Version ${version.version_number} · Uploaded ${formatDate(version.created_at)} · Expires ${formatDate(version.expiry_date)}` : 'No version uploaded'}</small>
                            </div>
                            <div className="company-document-status">
                              {state ? <span className={`processing-status status-${state.status}`}>{state.status.replace('_', ' ')}</span> : <span className="processing-status status-unprocessed">Not processed</span>}
                              {state?.status === 'completed' && <small>{state.extractionUnitCount} extraction units</small>}
                              {state?.safeMessage && <small className="company-status-error">{state.safeMessage}</small>}
                            </div>
                            <div className="company-document-actions">
                              {version && state?.status === 'completed' && <button className="text-button" onClick={() => void openExtraction(document, version)}>View extraction</button>}
                              {version && <button className="text-button" onClick={() => startEdit(document, version)}>Edit metadata</button>}
                              <button
                                className="text-button text-button-danger"
                                onClick={() => void removeCompanyDocument(document)}
                                disabled={Boolean(archivingDocumentId)}
                                aria-label={`Remove ${document.title}`}
                              >
                                {archivingDocumentId === document.id ? 'Removing…' : 'Remove'}
                              </button>
                            </div>
                          </article>
                        )
                      })}
                    </div>
                  )}
                </section>

                <section className="content-card company-runs-card">
                  <div className="company-section-heading"><div><p className="eyebrow">Worker activity</p><h2>Processing history</h2></div><button className="button button-secondary button-small" onClick={() => void refreshCompanyData(companyId)}>Refresh status</button></div>
                  {runs.length === 0 ? <p className="company-section-copy">No processing runs have been created for this company.</p> : <div className="company-runs-list">{runs.slice(0, 8).map((run) => (
                    <div className="company-run-row" key={run.id}><div><strong>{run.requested_document_count} document version(s)</strong><small>{formatDate(run.created_at)} · Run {run.id.slice(0, 8)}</small></div><span className={`processing-status status-${run.status}`}>{run.status.replace('_', ' ')}</span><span className="company-run-counts">{run.item_counts.completed} complete · {run.item_counts.failed} failed</span></div>
                  ))}</div>}
                </section>
              </>
            ) : <div className="company-empty-inline">Select an active company or create a new profile.</div>}
          </>
        )}
        <p className="page-footnote">Extracted content is evidence for review, not automatic proof of eligibility or compliance.</p>
      </main>

      {createOpen && <div className="dialog-backdrop" role="presentation"><section className="dialog-card" role="dialog" aria-modal="true" aria-labelledby="create-company-title"><div className="dialog-heading"><div><p className="eyebrow">Company repository</p><h2 id="create-company-title">Create company profile</h2></div><button className="icon-button" aria-label="Close" onClick={() => setCreateOpen(false)}>×</button></div><p className="dialog-copy">Create the company once, then store reusable documents and their revisions here.</p><form onSubmit={handleCreateCompany} className="tender-form"><label className="field field-full"><span>Registered legal name <b>*</b></span><input autoFocus value={createName} onChange={(event) => setCreateName(event.target.value)} required minLength={2} maxLength={255} placeholder="Company legal name" /></label><div className="form-actions field-full"><button type="button" className="button button-secondary" onClick={() => setCreateOpen(false)}>Cancel</button><button className="button button-primary" type="submit" disabled={createBusy}>{createBusy ? 'Creating…' : 'Create company'}</button></div></form></section></div>}

      {editing && <div className="dialog-backdrop" role="presentation"><section className="dialog-card" role="dialog" aria-modal="true" aria-labelledby="edit-company-metadata-title"><div className="dialog-heading"><div><p className="eyebrow">Version {editing.version.version_number}</p><h2 id="edit-company-metadata-title">Edit document metadata</h2></div><button className="icon-button" aria-label="Close" onClick={() => setEditing(null)}>×</button></div><p className="dialog-copy">{editing.version.original_filename}. Updating metadata does not modify the original uploaded file.</p><form className="tender-form" onSubmit={saveMetadata}><label className="field"><span>Issue date</span><input type="date" value={editIssueDate} onChange={(event) => setEditIssueDate(event.target.value)} /></label><label className="field"><span>Expiry date</span><input type="date" value={editExpiryDate} onChange={(event) => setEditExpiryDate(event.target.value)} /></label><label className="field"><span>Financial year</span><input value={editFinancialYear} onChange={(event) => setEditFinancialYear(event.target.value)} maxLength={20} placeholder="e.g. 2025-26" /></label><label className="field"><span>Issuer</span><input value={editIssuer} onChange={(event) => setEditIssuer(event.target.value)} maxLength={255} /></label><label className="field field-full"><span>Reference number</span><input value={editReference} onChange={(event) => setEditReference(event.target.value)} maxLength={255} /></label><label className="field field-full"><span>Notes</span><textarea value={editNotes} onChange={(event) => setEditNotes(event.target.value)} rows={3} maxLength={4000} /></label><div className="form-actions field-full"><button type="button" className="button button-secondary" onClick={() => setEditing(null)}>Cancel</button><button className="button button-primary" type="submit" disabled={editBusy}>{editBusy ? 'Saving…' : 'Save metadata'}</button></div></form></section></div>}

      {previewBusy && <div className="dialog-backdrop" role="presentation"><section className="dialog-card" role="dialog" aria-modal="true" aria-label="Loading extracted content"><div className="loading-state"><span className="spinner" /> Loading extracted content…</div></section></div>}
      {preview && <div className="dialog-backdrop" role="presentation"><section className="dialog-card dialog-wide extraction-dialog" role="dialog" aria-modal="true" aria-labelledby="extraction-preview-title"><div className="dialog-heading"><div><p className="eyebrow">Extraction preview · {preview.total} units</p><h2 id="extraction-preview-title">{preview.document.title}</h2><p className="company-section-copy">{preview.version.original_filename} · Version {preview.version.version_number}</p></div><button className="icon-button" aria-label="Close extraction preview" onClick={() => setPreview(null)}>×</button></div><div className="extraction-unit-list">{preview.units.map((unit) => {
        const rows = isTableRows(unit.table_data)
        const page = getSourcePage(unit)
        return <article className="extraction-unit" key={unit.id}><div className="extraction-unit-heading"><span>{unit.unit_type === 'table' ? 'Table' : 'Text block'} · Unit {unit.unit_index + 1}</span><span>{page}</span></div>{unit.content_text && <pre>{unit.content_text}</pre>}{rows.length > 0 && <div className="extraction-table-scroll"><table className="extraction-table"><tbody>{rows.map((row, index) => <tr key={`${unit.id}-${index}`}>{(row.cells ?? []).map((cell, cellIndex) => <td key={`${unit.id}-${index}-${cellIndex}`}>{cell.text ?? ''}</td>)}</tr>)}</tbody></table></div>}<details><summary>Source and extractor details</summary><pre>{JSON.stringify({ source_ref: unit.source_ref, metadata: unit.metadata }, null, 2)}</pre></details></article>
      })}</div></section></div>}
    </div>
  )
}
