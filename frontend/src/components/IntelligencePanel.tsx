

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  listDocuments,
  downloadDocument,
  type TenderDocument,
} from '../services/documents'
import { listCompanies, type Company } from '../services/companies'
import {
  getEvidenceMatchingRun,
  listEvidenceMatchingRuns,
  type EvidenceMatchingRun,
} from '../services/evidenceMatching'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  streamTenderQuestion,
  listQAConversations,
  readQAConversation,
  deleteQAConversation,
  getQARetrievalStatus,
  generateReadinessReport,
  getProcessingRun,
  getProcessingItemExtraction,
  listProcessingRuns,
  runCompliance,
  runEligibility,
  runTenderAnalysis,
  startProcessing,
  getLatestVerificationChecklist,
  updateVerificationItem,
  type VerificationChecklist,
  type VerificationItem,
  type VerificationStatus,
  type AnalysisResponse,
  type BidReadinessReport,
  type ComplianceResponse,
  type EligibilityResponse,
  type ProcessingRun,
  type ProcessingRunDetail,
  type ProcessingItemExtraction,
  type QAResponse,
  type QAConversationSummary,
  type QARetrievalStatus,
} from '../services/intelligence'
import type { TenderWorkspaceView } from '../types/workspace'

// ---------------------------------------------------------
// Helpers
// ---------------------------------------------------------

function readable(value: string): string {
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function dateTime(value: string | null): string {
  if (!value) return '—'

  const date = new Date(value)

  if (Number.isNaN(date.getTime())) return value

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

// ---------------------------------------------------------
// Status badge
// ---------------------------------------------------------

function Status({ value }: { value: string }) {
  return (
    <span className={`intel-status intel-status-${value}`}>
      {readable(value)}
    </span>
  )
}

// ---------------------------------------------------------
// Main intelligence panel
// ---------------------------------------------------------

export default function IntelligencePanel({
  tenderId,
  evidenceMatchingContext,
  view,
}: {
  tenderId: string
  evidenceMatchingContext?: { companyId: string; matchingRunId: string } | null
  view: Extract<TenderWorkspaceView, 'documents' | 'tender-analysis' | 'eligibility-analysis' | 'compliance-analysis' | 'qa' | 'readiness'> | null
}) {
  // Documents
  const [documents, setDocuments] = useState<TenderDocument[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [loadingDocs, setLoadingDocs] = useState(true)

  // Processing
  const [run, setRun] = useState<ProcessingRunDetail | null>(null)
  const [recentRuns, setRecentRuns] = useState<ProcessingRun[]>([])
  const [processing, setProcessing] = useState(false)
  const [extractionPreviews, setExtractionPreviews] = useState<Record<string, ProcessingItemExtraction>>({})
  const [loadingExtractionItemId, setLoadingExtractionItemId] = useState('')

  // General state
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  // Q&A
  const [question, setQuestion] = useState('')
  const [qaHistory, setQaHistory] = useState<QAResponse[]>([])
  const [qaConversations, setQaConversations] = useState<QAConversationSummary[]>([])
  const [qaConversationId, setQaConversationId] = useState<string | null>(null)
  const [qaConversationsLoading, setQaConversationsLoading] = useState(false)
  const [qaConversationError, setQaConversationError] = useState('')
  const [qaRetrievalStatus, setQaRetrievalStatus] = useState<QARetrievalStatus | null>(null)
  const [qaBusy, setQaBusy] = useState(false)
  const [qaStreamDraft, setQaStreamDraft] = useState('')
  const [qaStreamingQuestion, setQaStreamingQuestion] = useState('')
  const qaHistoryRef = useRef<HTMLDivElement>(null)

  // AI agents
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null)
  const [eligibility, setEligibility] = useState<EligibilityResponse | null>(null)
  const [compliance, setCompliance] = useState<ComplianceResponse | null>(null)

  // Final report and persistent manual checklist
  const [report, setReport] = useState<BidReadinessReport | null>(null)
  const [checklist, setChecklist] = useState<VerificationChecklist | null>(null)
  const [loadingChecklist, setLoadingChecklist] = useState(false)
  const [readinessActionFilter, setReadinessActionFilter] = useState<'all' | 'eligibility' | 'compliance'>('all')

  // Saved company evidence context for repeatable bid-readiness reports
  const [companies, setCompanies] = useState<Company[]>([])
  const [selectedCompanyId, setSelectedCompanyId] = useState(evidenceMatchingContext?.companyId ?? '')
  const [savedMatchingRuns, setSavedMatchingRuns] = useState<Array<Omit<EvidenceMatchingRun, 'results'>>>([])
  const [selectedMatchingRunId, setSelectedMatchingRunId] = useState(evidenceMatchingContext?.matchingRunId ?? '')
  const [loadingCompanies, setLoadingCompanies] = useState(false)
  const [loadingMatchingRuns, setLoadingMatchingRuns] = useState(false)
  const [matchingContextError, setMatchingContextError] = useState('')

  // -------------------------------------------------------
  // Derived values
  // -------------------------------------------------------

  const selectedCount = selectedIds.length

  const activeCompanies = useMemo(
    () => companies.filter((company) => !company.is_archived),
    [companies],
  )
  const selectedCompany = activeCompanies.find((company) => company.id === selectedCompanyId) ?? null
  const selectedMatchingRun = savedMatchingRuns.find((runItem) => runItem.id === selectedMatchingRunId) ?? null
  const reportEvidenceContext = selectedCompanyId && selectedMatchingRunId
    ? { companyId: selectedCompanyId, matchingRunId: selectedMatchingRunId }
    : null

  const activeProcessing = Boolean(
    run && ['pending', 'processing'].includes(run.status),
  )

  useEffect(() => {
    setExtractionPreviews({})
  }, [run?.id])

  const documentName = useMemo(
    () => new Map(documents.map((doc) => [doc.id, doc.original_filename])),
    [documents],
  )

  // -------------------------------------------------------
  // Load documents
  // -------------------------------------------------------

  const loadDocuments = useCallback(async () => {
    setLoadingDocs(true)

    try {
      const result = await listDocuments(tenderId, {
        limit: 100,
        offset: 0,
      })

      setDocuments(result.items)

      setSelectedIds((current) =>
        current.filter((id) =>
          result.items.some((doc) => doc.id === id),
        ),
      )

      setError('')
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : 'Could not load documents.',
      )
    } finally {
      setLoadingDocs(false)
    }
  }, [tenderId])

  // -------------------------------------------------------
  // Load processing history and restore latest run
  // -------------------------------------------------------

  const loadRuns = useCallback(async () => {
    try {
      const result = await listProcessingRuns(tenderId)

      const sortedRuns = [...result.items].sort(
        (a, b) =>
          Date.parse(b.created_at) - Date.parse(a.created_at),
      )

      setRecentRuns(sortedRuns)

      if (sortedRuns.length > 0) {
        const latestRun = await getProcessingRun(
          tenderId,
          sortedRuns[0].id,
        )

        setRun((current) =>
          current?.id === latestRun.id ? current : latestRun,
        )
      } else {
        setRun(null)
      }
    } catch {
      // Processing history is supplementary.
    }
  }, [tenderId])

  // -------------------------------------------------------
  // Load the latest saved pre-submission checklist
  // -------------------------------------------------------

  const loadChecklist = useCallback(async () => {
    setLoadingChecklist(true)
    try {
      setChecklist(await getLatestVerificationChecklist(tenderId))
    } catch {
      // The checklist is supplementary until the first report is generated.
    } finally {
      setLoadingChecklist(false)
    }
  }, [tenderId])

  // -------------------------------------------------------
  // Load reusable company profiles when the readiness view is opened
  // -------------------------------------------------------

  useEffect(() => {
    if (view !== 'readiness') return

    let current = true
    setLoadingCompanies(true)
    setMatchingContextError('')

    listCompanies({ limit: 100, offset: 0 })
      .then((result) => {
        if (current) setCompanies(result.items)
      })
      .catch((reason: unknown) => {
        if (current) {
          setMatchingContextError(
            reason instanceof Error ? reason.message : 'Company profiles could not be loaded.',
          )
        }
      })
      .finally(() => {
        if (current) setLoadingCompanies(false)
      })

    return () => { current = false }
  }, [view])

  // Restore the latest matching context when the user arrives from Evidence Matching.
  useEffect(() => {
    if (!evidenceMatchingContext) return
    setSelectedCompanyId(evidenceMatchingContext.companyId)
    setSelectedMatchingRunId(evidenceMatchingContext.matchingRunId)
  }, [evidenceMatchingContext?.companyId, evidenceMatchingContext?.matchingRunId])

  // -------------------------------------------------------
  // Load saved evidence-matching runs for the selected company and tender
  // -------------------------------------------------------

  useEffect(() => {
    if (view !== 'readiness' || !selectedCompanyId) {
      setSavedMatchingRuns([])
      if (!selectedCompanyId) setSelectedMatchingRunId('')
      return
    }

    let current = true
    setLoadingMatchingRuns(true)
    setMatchingContextError('')

    listEvidenceMatchingRuns(tenderId, selectedCompanyId, { page: 1, limit: 50 })
      .then(async (result) => {
        if (!current) return
        const scopedRuns = result.items
          .filter((runItem) => runItem.tender_id === tenderId && runItem.company_id === selectedCompanyId)
          .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
        setSavedMatchingRuns(scopedRuns)

        const requestedRunId = evidenceMatchingContext?.companyId === selectedCompanyId
          ? evidenceMatchingContext.matchingRunId
          : ''
        const preferredRunId = requestedRunId || selectedMatchingRunId
        if (preferredRunId && scopedRuns.some((runItem) => runItem.id === preferredRunId)) {
          setSelectedMatchingRunId(preferredRunId)
        } else if (requestedRunId) {
          // A context created a while ago may fall outside the first page of runs.
          try {
            const savedRun = await getEvidenceMatchingRun(tenderId, selectedCompanyId, requestedRunId)
            if (!current) return
            if (savedRun.tender_id === tenderId && savedRun.company_id === selectedCompanyId) {
              setSavedMatchingRuns((existing) => [
                savedRun,
                ...existing.filter((runItem) => runItem.id !== savedRun.id),
              ])
              setSelectedMatchingRunId(savedRun.id)
            } else {
              setSelectedMatchingRunId('')
            }
          } catch {
            if (current) setSelectedMatchingRunId('')
          }
        } else {
          setSelectedMatchingRunId('')
        }
      })
      .catch((reason: unknown) => {
        if (current) {
          setSavedMatchingRuns([])
          setSelectedMatchingRunId('')
          setMatchingContextError(
            reason instanceof Error ? reason.message : 'Saved company matching runs could not be loaded.',
          )
        }
      })
      .finally(() => {
        if (current) setLoadingMatchingRuns(false)
      })

    return () => { current = false }
  }, [view, tenderId, selectedCompanyId, evidenceMatchingContext?.companyId, evidenceMatchingContext?.matchingRunId])

  // -------------------------------------------------------
  // Initial loading
  // -------------------------------------------------------

  useEffect(() => {
    void loadDocuments()
    void loadRuns()
    void loadChecklist()
  }, [loadDocuments, loadRuns, loadChecklist])

  // -------------------------------------------------------
  // Automatic processing status polling
  // -------------------------------------------------------

  useEffect(() => {
    if (!run || !['pending', 'processing'].includes(run.status)) {
      return
    }

    let active = true

    const timer = window.setInterval(() => {
      void getProcessingRun(tenderId, run.id)
        .then((updated) => {
          if (!active) return

          setRun(updated)

          if (
            !['pending', 'processing'].includes(updated.status)
          ) {
            void loadDocuments()
            void loadRuns()
          }
        })
        .catch((reason: unknown) => {
          if (active) {
            setError(
              reason instanceof Error
                ? reason.message
                : 'Could not refresh processing status.',
            )
          }
        })
    }, 2500)

    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [run?.id, run?.status, tenderId, loadDocuments, loadRuns])

  async function handleViewExtraction(itemId: string) {
    if (!run || loadingExtractionItemId) return
    if (extractionPreviews[itemId]) {
      setExtractionPreviews((current) => {
        const next = { ...current }
        delete next[itemId]
        return next
      })
      return
    }

    setLoadingExtractionItemId(itemId)
    setError('')
    try {
      const preview = await getProcessingItemExtraction(tenderId, run.id, itemId)
      setExtractionPreviews((current) => ({ ...current, [itemId]: preview }))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load extracted document text.')
    } finally {
      setLoadingExtractionItemId('')
    }
  }

  // -------------------------------------------------------
  // Start document processing
  // -------------------------------------------------------

  async function handleStartProcessing() {
    if (
      !selectedCount ||
      selectedCount > 20 ||
      activeProcessing
    ) {
      return
    }

    setError('')
    setNotice('')
    setProcessing(true)

    try {
      const created = await startProcessing(
        tenderId,
        selectedIds,
      )

      const detail = await getProcessingRun(
        tenderId,
        created.id,
      )

      setRun(detail)

      setNotice(
        'Extraction run created. The worker will process the selected documents.',
      )

      await loadRuns()
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : 'Could not start document extraction.',
      )
    } finally {
      setProcessing(false)
    }
  }

  // -------------------------------------------------------
  // Shared AI execution handler
  // -------------------------------------------------------

  async function execute<T>(
    label: string,
    action: () => Promise<T>,
    save: (result: T) => void,
  ) {
    setBusy(label)
    setError('')
    setNotice('')

    try {
      const result = await action()

      save(result)

      setNotice(`${label} completed.`)
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : `${label} failed.`,
      )
    } finally {
      setBusy('')
    }
  }

  // -------------------------------------------------------
  // Ask tender question with server-sent token streaming
  // -------------------------------------------------------

  async function handleAsk(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const value = question.trim()
    if (value.length < 3 || qaBusy) return

    setError('')
    setNotice('')
    setQaConversationError('')
    setQaStreamDraft('')
    setQaStreamingQuestion(value)
    setQaBusy(true)

    try {
      const result = await streamTenderQuestion(tenderId, value, (delta) => {
        setQaStreamDraft((current) => current + delta)
      }, qaConversationId)
      setQaHistory((current) => [...current, result])
      if (result.conversation_id) setQaConversationId(result.conversation_id)
      setQuestion('')
      setNotice('Answer completed. Source references were checked before the response was saved.')
      try {
        const conversations = await listQAConversations(tenderId)
        setQaConversations(conversations)
      } catch (refreshReason) {
        setQaConversationError(refreshReason instanceof Error ? refreshReason.message : 'The conversation list could not be refreshed.')
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Tender Q&A could not be completed.')
    } finally {
      setQaBusy(false)
      setQaStreamDraft('')
      setQaStreamingQuestion('')
    }
  }

  async function openQAConversation(conversationId: string) {
    if (!conversationId || qaBusy) return
    setQaConversationError('')
    setQaConversationsLoading(true)
    try {
      const conversation = await readQAConversation(tenderId, conversationId)
      const answers: QAResponse[] = []
      let unmatchedQuestion = ''
      for (const message of conversation.messages) {
        if (message.role === 'user') {
          unmatchedQuestion = message.content
        } else if (message.role === 'assistant' && message.response_json) {
          answers.push(message.response_json)
          unmatchedQuestion = ''
        }
      }
      if (unmatchedQuestion) {
        answers.push({
          question: unmatchedQuestion,
          answer: 'This question does not have a saved final answer. The previous response may have been interrupted. Please submit it again.',
          sources: [],
          evidence_found: false,
          retrieval_mode: 'none',
          citation_validation_status: 'no_sources',
        })
      }
      setQaHistory(answers)
      setQaConversationId(conversation.id)
      setQuestion('')
    } catch (reason) {
      setQaConversationError(reason instanceof Error ? reason.message : 'The conversation could not be loaded.')
    } finally {
      setQaConversationsLoading(false)
    }
  }

  async function startNewQAConversation() {
    if (qaBusy) return
    setQaConversationId(null)
    setQaHistory([])
    setQuestion('')
    setQaConversationError('')
  }

  async function removeQAConversation() {
    if (!qaConversationId || qaBusy) return
    setQaConversationsLoading(true)
    setQaConversationError('')
    try {
      await deleteQAConversation(tenderId, qaConversationId)
      const remaining = await listQAConversations(tenderId)
      setQaConversations(remaining)
      setQaConversationId(null)
      setQaHistory([])
    } catch (reason) {
      setQaConversationError(reason instanceof Error ? reason.message : 'The conversation could not be deleted.')
    } finally {
      setQaConversationsLoading(false)
    }
  }

  useEffect(() => {
    if (view !== 'qa') return
    let active = true
    setQaConversationsLoading(true)
    Promise.allSettled([listQAConversations(tenderId), getQARetrievalStatus()])
      .then(([conversationsResult, statusResult]) => {
        if (!active) return
        if (conversationsResult.status === 'fulfilled') {
          setQaConversations(conversationsResult.value)
          setQaConversationError('')
        } else {
          setQaConversationError(
            conversationsResult.reason instanceof Error
              ? conversationsResult.reason.message
              : 'Saved Q&A conversations could not be loaded.',
          )
        }
        // Diagnostics are helpful but must not prevent users from opening
        // saved conversations when this optional endpoint is unavailable.
        if (statusResult.status === 'fulfilled') setQaRetrievalStatus(statusResult.value)
        else setQaRetrievalStatus(null)
      })
      .finally(() => { if (active) setQaConversationsLoading(false) })
    return () => { active = false }
    // Re-fetch only when entering Q&A or changing tender. Selecting a conversation
    // is handled explicitly by openQAConversation to avoid effect loops.
  }, [tenderId, view])

  useEffect(() => {
    const history = qaHistoryRef.current
    if (history) history.scrollTop = history.scrollHeight
  }, [qaHistory, qaStreamDraft, qaBusy])

  // -------------------------------------------------------
  // Refresh all workspace information
  // -------------------------------------------------------

  async function handleRefresh() {
    setError('')
    setNotice('')

    await Promise.all([
      loadDocuments(),
      loadRuns(),
    ])

    setNotice('Workspace information refreshed.')
  }

  // -------------------------------------------------------
  // Focused workspace views
  // -------------------------------------------------------

  if (!view) return null

  const eligibilityCounts = eligibility ? {
    total: eligibility.requirements.length,
    met: eligibility.requirements.filter((item) => item.status === 'met').length,
    missing: eligibility.requirements.filter((item) => item.status === 'evidence_missing').length,
    review: eligibility.requirements.filter((item) => item.status === 'needs_verification').length,
    notMet: eligibility.requirements.filter((item) => item.status === 'not_met').length,
  } : null

  const complianceCounts = compliance ? {
    total: compliance.requirements.length,
    compliant: compliance.requirements.filter((item) => item.status === 'compliant').length,
    missing: compliance.requirements.filter((item) => item.status === 'evidence_missing').length,
    review: compliance.requirements.filter((item) => item.status === 'needs_verification').length,
    notCompliant: compliance.requirements.filter((item) => item.status === 'non_compliant').length,
  } : null

  return (
    <section className={`content-card intelligence-panel intel-view-${view}`} aria-label="Tender intelligence tools">
      {error && <p className="form-error intel-message" role="alert">{error}</p>}
      {notice && <p className="document-feedback intel-message" role="status">{notice}</p>}

      {/* DOCUMENT EXTRACTION */}
      {view === 'documents' && (
        <section className="intel-work-view">
          <div className="intel-section-heading">
            <div>
              <h3>Process uploaded documents</h3>
              <p>Select source files and explicitly start extraction. Uploading a file does not automatically process it.</p>
            </div>
            <button className="button button-secondary button-small" type="button" onClick={() => void handleRefresh()}>Refresh</button>
          </div>

          {loadingDocs ? (
            <p className="muted-cell">Loading documents…</p>
          ) : documents.length === 0 ? (
            <div className="intel-empty-state">
              <span className="intel-empty-symbol">01</span>
              <strong>No tender documents uploaded yet</strong>
              <p>Upload the tender notice, annexures, BOQ and amendments in the Documents section above.</p>
            </div>
          ) : (
            <>
              <div className="intel-document-toolbar">
                <span>{documents.length} available documents</span>
                <button type="button" className="text-button" onClick={() => setSelectedIds(selectedIds.length === documents.length ? [] : documents.map((doc) => doc.id))} disabled={activeProcessing}>
                  {selectedIds.length === documents.length ? 'Clear selection' : 'Select all'}
                </button>
              </div>
              <div className="intel-document-list">
                {documents.map((doc) => (
                  <label className={`intel-document-option${selectedIds.includes(doc.id) ? ' is-selected' : ''}`} key={doc.id}>
                    <input
                      type="checkbox"
                      checked={selectedIds.includes(doc.id)}
                      disabled={activeProcessing}
                      onChange={(event) => setSelectedIds((current) => event.target.checked ? [...current, doc.id] : current.filter((id) => id !== doc.id))}
                    />
                    <span className="intel-document-type">{doc.extension.replace('.', '').toUpperCase()}</span>
                    <span className="intel-document-description">
                      <strong>{doc.original_filename}</strong>
                      <small>{readable(doc.category)} · {(doc.size_bytes / 1024).toFixed(0)} KB · Added {dateTime(doc.created_at)}</small>
                    </span>
                  </label>
                ))}
              </div>
              <div className="intel-inline-actions">
                <span>{selectedCount} selected · Maximum 20 per run</span>
                <button className="button button-primary" type="button" onClick={() => void handleStartProcessing()} disabled={processing || activeProcessing || selectedCount === 0 || selectedCount > 20}>
                  {processing ? 'Starting extraction…' : 'Process selected documents'}
                </button>
              </div>
            </>
          )}

          {run && (
            <div className="intel-run-card">
              <div className="intel-run-heading"><strong>Latest processing run</strong><Status value={run.status} /></div>
              <p>Created {dateTime(run.created_at)} · {run.requested_document_count} document(s)</p>
              <div className="intel-counts">
                <span>Pending: {run.item_counts.pending}</span><span>Processing: {run.item_counts.processing}</span><span>Completed: {run.item_counts.completed}</span><span>Failed: {run.item_counts.failed}</span>
              </div>
              <ul className="intel-run-items">
                {run.items.map((item) => {
                  const preview = extractionPreviews[item.id]
                  return (
                    <li className="intel-run-item" key={item.id}>
                      <div className="intel-run-item-heading">
                        <span>{item.document_id ? documentName.get(item.document_id) || 'Document' : 'Unavailable document'}</span>
                        <Status value={item.status} />
                        {item.status === 'completed' && (
                          <button
                            type="button"
                            className="text-button intel-preview-button"
                            disabled={Boolean(loadingExtractionItemId)}
                            onClick={() => void handleViewExtraction(item.id)}
                          >
                            {loadingExtractionItemId === item.id ? 'Loading text…' : preview ? 'Hide extracted text' : 'View extracted text'}
                          </button>
                        )}
                      </div>
                      {item.safe_message && <small>{item.safe_message}</small>}
                      {preview && (
                        <div className="intel-extraction-preview">
                          <div className="intel-extraction-preview-heading">
                            <strong>{preview.filename}</strong>
                            <span>{preview.extraction_unit_count} extraction unit(s)</span>
                          </div>
                          {!preview.has_extracted_text ? (
                            <p className="intel-extraction-empty">
                              Processing completed, but no readable text or table values were saved. Check whether this file contains selectable text or requires OCR.
                            </p>
                          ) : (
                            <>
                              {preview.units.map((unit) => (
                                <article className="intel-extraction-unit" key={`${unit.unit_index}-${unit.citation}`}>
                                  <div><span>{readable(unit.unit_type)}</span><small>{unit.citation}</small></div>
                                  <pre>{unit.content_text}</pre>
                                </article>
                              ))}
                              {preview.units_truncated && <p className="intel-extraction-empty">Showing the first 40 extraction units. The full extraction remains stored in TenderWise.</p>}
                            </>
                          )}
                        </div>
                      )}
                    </li>
                  )
                })}
              </ul>
              {activeProcessing && <p className="muted-cell">Status refreshes automatically. Keep the document worker running.</p>}
            </div>
          )}

          {recentRuns.length > 0 && (
            <details className="intel-disclosure">
              <summary>Recent processing history ({recentRuns.length})</summary>
              <ul className="intel-disclosure-list">
                {recentRuns.slice(0, 5).map((item) => <li key={item.id}><span>{dateTime(item.created_at)}</span> · <Status value={item.status} /> · {item.requested_document_count} document(s)</li>)}
              </ul>
            </details>
          )}
        </section>
      )}

      {/* TENDER ANALYSIS — dedicated screen */}
      {view === 'tender-analysis' && (
        <section className="intel-work-view">
          <div className="agent-run-panel">
            <div className="agent-run-mark agent-run-mark-tender">TA</div>
            <div className="agent-run-copy"><strong>Tender Analysis Agent</strong><p>Extracts scope of work, milestones, commercial terms, BOQ details, risks and information gaps from the tender documents.</p></div>
            <button className="button button-primary" type="button" disabled={busy !== ''} onClick={() => void execute('Tender analysis', () => runTenderAnalysis(tenderId), setAnalysis)}>{busy === 'Tender analysis' ? 'Analysing…' : analysis ? 'Run analysis again' : 'Run tender analysis'}</button>
          </div>

          {analysis ? (
            <div className="analysis-result-panel">
              <div className="analysis-result-topline"><div><p className="eyebrow">Analysis result</p><h3>Tender analysis findings</h3></div><Status value={analysis.evidence_found ? 'evidence_found' : 'no_evidence'} /></div>
              <div className="analysis-mini-metrics"><div><strong>{analysis.sources.length}</strong><span>Cited sources</span></div><div><strong>{analysis.evidence_found ? 'Yes' : 'No'}</strong><span>Evidence retrieved</span></div></div>
              <details className="intel-disclosure analysis-full-disclosure">
                <summary>Read the complete tender analysis</summary>
                <div className="analysis-markdown-wrap"><MarkdownView content={analysis.report} /></div>
              </details>
              {analysis.sources.length > 0 && <details className="intel-disclosure"><summary>Review cited sources ({analysis.sources.length})</summary><SourceList tenderId={tenderId} documents={documents} sources={analysis.sources} /></details>}
            </div>
          ) : (
            <div className="intel-empty-state"><span className="intel-empty-symbol">TA</span><strong>Tender analysis has not been run</strong><p>Run the agent after processing your tender documents. The complete report will appear here with expandable source details.</p></div>
          )}
        </section>
      )}

      {/* ELIGIBILITY ANALYSIS — dedicated screen */}
      {view === 'eligibility-analysis' && (
        <section className="intel-work-view">
          <div className="agent-run-panel">
            <div className="agent-run-mark agent-run-mark-eligibility">EL</div>
            <div className="agent-run-copy"><strong>Eligibility Analysis Agent</strong><p>Identifies bidder qualification conditions and compares the tender's evidence requests with processed bidder documents in this tender workspace.</p></div>
            <button className="button button-primary" type="button" disabled={busy !== ''} onClick={() => void execute('Eligibility analysis', () => runEligibility(tenderId), setEligibility)}>{busy === 'Eligibility analysis' ? 'Assessing…' : eligibility ? 'Run assessment again' : 'Run eligibility analysis'}</button>
          </div>

          {eligibility && eligibilityCounts ? (
            <div className="analysis-result-panel">
              <div className="analysis-result-topline"><div><p className="eyebrow">Assessment result</p><h3>Bidder eligibility</h3></div><Status value={eligibility.overall_status} /></div>
              <p className="analysis-summary-copy">{eligibility.summary}</p>
              <div className="analysis-count-grid">
                <div><strong>{eligibilityCounts.total}</strong><span>Criteria identified</span></div>
                <div><strong>{eligibilityCounts.met}</strong><span>Evidence met</span></div>
                <div><strong>{eligibilityCounts.missing}</strong><span>Evidence missing</span></div>
                <div><strong>{eligibilityCounts.review}</strong><span>Needs manual review</span></div>
              </div>
              <div className="analysis-diagnostics-strip">
                <span><strong>{eligibility.extracted_unit_count}</strong> extracted text units available</span>
                <span><strong>{eligibility.reviewed_source_count}</strong> tender excerpts reviewed</span>
                <span><strong>{eligibility.sources.filter((source) => source.document_role === 'bidder').length}</strong> bidder excerpts</span>
              </div>
              {eligibility.requirements.length > 0 ? (
                <div className="analysis-requirement-section">
                  <div className="analysis-subheading"><h4>Eligibility criteria</h4><span>{eligibilityCounts.total} criteria identified</span></div>
                  <RequirementList items={eligibility.requirements} />
                </div>
              ) : (
                <div className="analysis-no-requirements" role="status">
                  <div className="analysis-no-requirements-icon">!</div>
                  <div>
                    <h4>No eligibility criteria identified</h4>
                    <p>The extraction job may have completed successfully, but the eligibility agent could not map the available source text to a supported qualification criterion. This is an analysis result, not proof that the tender has no eligibility rules.</p>
                    <p><strong>Next step:</strong> Open Document Processing, select a completed tender file and choose <em>View extracted text</em>. Confirm that the registration, experience, turnover and declaration clauses are present before running this assessment again.</p>
                  </div>
                </div>
              )}
              {eligibility.sources.length > 0 && <details className="intel-disclosure"><summary>Review tender and bidder evidence sources ({eligibility.sources.length})</summary><SourceList tenderId={tenderId} documents={documents} sources={eligibility.sources} /></details>}
            </div>
          ) : (
            <div className="intel-empty-state"><span className="intel-empty-symbol">EL</span><strong>Eligibility assessment has not been run</strong><p>Run this agent to identify registration, experience, financial and declaration requirements and review their bidder evidence status.</p></div>
          )}
        </section>
      )}

      {/* COMPLIANCE ANALYSIS — dedicated screen */}
      {view === 'compliance-analysis' && (
        <section className="intel-work-view">
          <div className="agent-run-panel">
            <div className="agent-run-mark agent-run-mark-compliance">CO</div>
            <div className="agent-run-copy"><strong>Bid-stage Compliance Agent</strong><p>Reviews submission deliverables, product specifications, EMD and tender milestones, plus obligations that apply after award.</p></div>
            <button className="button button-primary" type="button" disabled={busy !== ''} onClick={() => void execute('Compliance analysis', () => runCompliance(tenderId), setCompliance)}>{busy === 'Compliance analysis' ? 'Checking…' : compliance ? 'Run assessment again' : 'Run compliance analysis'}</button>
          </div>

          {compliance && complianceCounts ? (
            <div className="analysis-result-panel">
              <div className="analysis-result-topline"><div><p className="eyebrow">Assessment result</p><h3>Bid-stage compliance</h3></div><Status value={compliance.overall_status} /></div>
              <p className="analysis-summary-copy">{compliance.summary}</p>
              <div className="analysis-count-grid">
                <div><strong>{complianceCounts.total}</strong><span>Requirements</span></div>
                <div><strong>{complianceCounts.compliant}</strong><span>Compliant</span></div>
                <div><strong>{complianceCounts.missing}</strong><span>Evidence missing</span></div>
                <div><strong>{complianceCounts.review}</strong><span>Needs review</span></div>
              </div>
              <div className="analysis-requirement-section"><div className="analysis-subheading"><h4>Compliance requirements</h4><span>{complianceCounts.total} requirements identified</span></div><RequirementList items={compliance.requirements} /></div>
              <details className="intel-disclosure">
                <summary>Tender information and key dates ({compliance.tender_information.length})</summary>
                {compliance.tender_information.length > 0 ? <ul className="intel-disclosure-list">{compliance.tender_information.map((item, index) => <li key={`${item.category}-${index}`}><strong>{item.item}:</strong> {item.details}</li>)}</ul> : <p className="intel-empty">No tender information was returned.</p>}
              </details>
              <details className="intel-disclosure">
                <summary>Post-award obligations ({compliance.post_award_obligations.length})</summary>
                {compliance.post_award_obligations.length > 0 ? <ul className="intel-disclosure-list">{compliance.post_award_obligations.map((item, index) => <li key={`${item.category}-${index}`}><strong>{item.obligation}:</strong> {item.details}</li>)}</ul> : <p className="intel-empty">No post-award obligations were returned.</p>}
              </details>
              {compliance.sources.length > 0 && <details className="intel-disclosure"><summary>Review evidence sources ({compliance.sources.length})</summary><SourceList tenderId={tenderId} documents={documents} sources={compliance.sources} /></details>}
            </div>
          ) : (
            <div className="intel-empty-state"><span className="intel-empty-symbol">CO</span><strong>Compliance assessment has not been run</strong><p>Run this agent to organize bid-stage deliverables, technical requirements, EMD, dates and post-award duties into a reviewable checklist.</p></div>
          )}
        </section>
      )}

      {/* DEDICATED, WIDE TENDER Q&A */}
      {view === 'qa' && (
        <section className="qa-workspace-v2">
          <div className="qa-workspace-toolbar qa-workspace-toolbar-advanced">
            <div className="qa-toolbar-title"><span className="qa-live-indicator" /> <strong>Tender-grounded assistant</strong><span className="qa-toolbar-note">Answers are retrieved from the latest successful document processing run</span></div>
            <div className="qa-conversation-controls">
              <label className="qa-conversation-select-label">
                <span className="visually-hidden">Saved conversations</span>
                <select aria-label="Saved conversations" value={qaConversationId ?? ''} disabled={qaBusy || qaConversationsLoading} onChange={(event) => { if (event.target.value) void openQAConversation(event.target.value); else void startNewQAConversation() }}>
                  <option value="">New conversation</option>
                  {qaConversations.map((item) => <option key={item.id} value={item.id}>{item.title} ({item.message_count})</option>)}
                </select>
              </label>
              <button className="button button-secondary button-small" type="button" disabled={qaBusy} onClick={() => void startNewQAConversation()}>New chat</button>
              {qaConversationId && <button className="text-button qa-delete-conversation" type="button" disabled={qaBusy || qaConversationsLoading} onClick={() => void removeQAConversation()}>Delete</button>}
            </div>
          </div>
          <div className="qa-engine-status" role="status">
            <span className={qaRetrievalStatus?.dense_retrieval_ready ? 'qa-engine-dot is-ready' : 'qa-engine-dot'} />
            <strong>{qaRetrievalStatus?.dense_retrieval_ready ? 'Hybrid retrieval ready' : 'Keyword retrieval fallback'}</strong>
            <span>{qaRetrievalStatus?.dense_retrieval_ready ? `Dense search · ${qaRetrievalStatus.embedding_model}` : 'Semantic search requires PostgreSQL pgvector, semantic retrieval enabled and NVIDIA API credentials.'}</span>
            {qaRetrievalStatus?.reranker_enabled && qaRetrievalStatus.reranker_configured && <em>Cross-encoder reranker configured</em>}
            {qaRetrievalStatus && <em>Chunker {qaRetrievalStatus.chunker_version || '—'} · Top {qaRetrievalStatus.candidate_k ?? '—'} · RRF k={qaRetrievalStatus.rrf_k ?? '—'} · HNSW ef={qaRetrievalStatus.hnsw_ef_search ?? '—'}</em>}
          </div>
          {qaConversationError && <p className="qa-conversation-error" role="alert">{qaConversationError}</p>}
          {qaConversationsLoading && <p className="qa-conversation-loading" role="status">Loading Q&A workspace…</p>}

          <div className="qa-history-v2" ref={qaHistoryRef} aria-live="polite">
            {qaHistory.length === 0 && !qaBusy && (
              <div className="qa-welcome-v2">
                <span className="qa-welcome-mark">TW</span>
                <h3>What would you like to know?</h3>
                <p>Ask about eligibility, bid deadlines, EMD, technical specifications, BOQ or submission documents. TenderWise will retrieve relevant clauses and cite the source locations.</p>
                <div className="qa-suggestion-grid">
                  {[
                    'What are the bidder eligibility requirements?',
                    'What is the bid submission deadline and bid opening date?',
                    'Which documents are required to prove similar contract experience?',
                    'What are the EMD amount and validity conditions?',
                    'List the minimum technical specifications for the equipment.',
                    'What are the post-award obligations?',
                  ].map((suggestion) => <button type="button" key={suggestion} onClick={() => setQuestion(suggestion)}>{suggestion}<span aria-hidden="true">↗</span></button>)}
                </div>
              </div>
            )}
            {qaHistory.map((item, index) => (
              <article className="qa-turn-v2" key={`${index}-${item.question}`}>
                <div className="qa-question-row"><span className="qa-avatar-user">YOU</span><div className="qa-question-content"><span className="qa-speaker-label">Your question</span><p>{item.question}</p></div></div>
                <div className="qa-answer-row">
                  <span className="qa-avatar-assistant">T</span>
                  <div className="qa-answer-content">
                    <div className="qa-answer-topline"><strong>TenderWise</strong><Status value={item.evidence_found ? 'evidence_found' : 'no_evidence'} /></div>
                    <MarkdownView content={item.answer} />
                    {item.sources.length > 0 && <details className="qa-source-disclosure"><summary>View {item.sources.length} cited sources</summary><SourceList tenderId={tenderId} documents={documents} sources={item.sources} /></details>}
                    {item.sources.length === 0 && <p className="qa-no-source-note">No supporting source was found in the processed tender evidence.</p>}
                    <div className="qa-answer-provenance">
                      <span>Retrieval: {readable(item.retrieval_mode ?? 'unknown')}</span>
                      <span>Reranking: {readable(item.rerank_mode ?? 'disabled')}</span>
                      <span className={item.citation_validation_status === 'needs_review' ? 'needs-review' : ''}>Citation check: {readable(item.citation_validation_status ?? 'not available')}</span>
                    </div>
                    {item.citation_validation_details && item.citation_validation_details.length > 0 && <details className="qa-claim-check-disclosure"><summary>Claim-level source checks ({item.citation_validation_details.length})</summary><ul>
                      {item.citation_validation_details.map((claim, claimIndex) => <li key={`${claimIndex}-${claim.status}`}><span className={claim.status === 'needs_review' ? 'claim-check-review' : 'claim-check-supported'}>{claim.status === 'needs_review' ? 'Review needed' : 'Source overlap found'}</span><p>{claim.claim}</p><small>Sources: {claim.source_ids.join(', ') || 'No valid source ID'} · Text overlap: {Math.round(claim.token_overlap * 100)}% · Numeric match: {claim.numeric_values_match ? 'yes' : 'no'}</small></li>)}
                    </ul></details>}
                    {item.retrieval_trace && <details className="qa-trace-disclosure"><summary>Retrieval diagnostics</summary><dl>
                      <div><dt>Chunks scanned</dt><dd>{String(item.retrieval_trace.chunks_scanned ?? '—')}</dd></div>
                      <div><dt>Lexical candidates</dt><dd>{String(item.retrieval_trace.lexical_candidates ?? '—')}</dd></div>
                      <div><dt>Dense candidates</dt><dd>{String(item.retrieval_trace.dense_candidates ?? '—')}</dd></div>
                      <div><dt>Fused candidates</dt><dd>{String(item.retrieval_trace.fused_candidates ?? '—')}</dd></div>
                      <div><dt>Embedding coverage</dt><dd>{typeof item.retrieval_trace.embedding_coverage === 'number' ? `${Math.round(item.retrieval_trace.embedding_coverage * 100)}%` : '—'}</dd></div>
                      <div><dt>Retrieval time</dt><dd>{String(item.retrieval_trace.retrieval_ms ?? '—')} ms</dd></div>
                      <div><dt>Rerank time</dt><dd>{String(item.retrieval_trace.rerank_ms ?? '—')} ms</dd></div>
                    </dl></details>}
                  </div>
                </div>
              </article>
            ))}
            {qaBusy && (
              <article className="qa-turn-v2 qa-turn-streaming">
                <div className="qa-question-row"><span className="qa-avatar-user">YOU</span><div className="qa-question-content"><span className="qa-speaker-label">Your question</span><p>{qaStreamingQuestion}</p></div></div>
                <div className="qa-answer-row"><span className="qa-avatar-assistant">T</span><div className="qa-answer-content"><div className="qa-answer-topline"><strong>TenderWise</strong><span className="qa-generating"><span className="spinner" /> Generating answer</span></div>{qaStreamDraft ? <MarkdownView content={qaStreamDraft} /> : <p className="qa-retrieval-progress">Searching tender evidence and preparing a cited answer…</p>}<p className="qa-provisional-note">Live draft · source citations are checked before the final answer is saved.</p></div></div>
              </article>
            )}
          </div>

          <form className="qa-composer-v2" onSubmit={(event) => void handleAsk(event)}>
            <label className="visually-hidden" htmlFor="tender-question-v2">Ask a question about this tender</label>
            <textarea id="tender-question-v2" value={question} onChange={(event) => setQuestion(event.target.value)} rows={3} maxLength={2000} minLength={3} placeholder="Ask about a tender clause, requirement, date or submission document…" disabled={qaBusy} />
            <div className="qa-composer-bottom"><span>Answers cite the processed tender documents. Confirm critical requirements against the original tender.</span><button className="button button-primary" type="submit" disabled={qaBusy || question.trim().length < 3}>{qaBusy ? 'Generating…' : 'Ask TenderWise'} <span aria-hidden="true">↗</span></button></div>
          </form>
        </section>
      )}

      {/* BID READINESS — summary-first dashboard */}
      {view === 'readiness' && (
        <section className="readiness-workspace-v2">
          <div className="readiness-generate-bar">
            <div className="readiness-context-main">
              <div className="readiness-context-copy">
                <strong>Company evidence context</strong>
                <p>Select a company profile and a saved matching run to include previously discovered company-document evidence in this report. Leave both unselected for a tender-only assessment.</p>
              </div>
              <div className="readiness-context-fields">
                <label className="readiness-context-field">
                  <span>Company profile</span>
                  <select
                    value={selectedCompanyId}
                    onChange={(event) => {
                      setSelectedCompanyId(event.target.value)
                      setSelectedMatchingRunId('')
                      setSavedMatchingRuns([])
                      setMatchingContextError('')
                    }}
                    disabled={loadingCompanies || activeCompanies.length === 0}
                  >
                    <option value="">Choose a company profile…</option>
                    {activeCompanies.map((company) => (
                      <option value={company.id} key={company.id}>
                        {company.display_name || company.legal_name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="readiness-context-field">
                  <span>Saved company matching run</span>
                  <select
                    value={selectedMatchingRunId}
                    onChange={(event) => setSelectedMatchingRunId(event.target.value)}
                    disabled={!selectedCompanyId || loadingMatchingRuns || savedMatchingRuns.length === 0}
                  >
                    <option value="">Choose a saved matching run…</option>
                    {savedMatchingRuns.map((runItem) => (
                      <option value={runItem.id} key={runItem.id}>
                        Assessment · {dateTime(runItem.created_at)}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              {loadingCompanies && <p className="readiness-context-status" role="status">Loading company profiles…</p>}
              {selectedCompanyId && loadingMatchingRuns && <p className="readiness-context-status" role="status">Loading saved matching runs…</p>}
              {matchingContextError && <p className="readiness-context-error" role="alert">{matchingContextError}</p>}
              {!loadingCompanies && activeCompanies.length === 0 && !matchingContextError && (
                <p className="readiness-context-status">No active company profiles are available. Create a company profile before adding company evidence.</p>
              )}
              {selectedCompany && !loadingMatchingRuns && savedMatchingRuns.length === 0 && !matchingContextError && (
                <p className="readiness-context-status">No saved matching runs were found for {selectedCompany.display_name || selectedCompany.legal_name} in this tender. Run Company Evidence Matching first.</p>
              )}
              {selectedMatchingRun && (
                <p className="readiness-context-success" role="status">
                  Company evidence included · {selectedCompany?.display_name || selectedCompany?.legal_name} · {dateTime(selectedMatchingRun.created_at)}
                </p>
              )}
              {!selectedMatchingRunId && !matchingContextError && !loadingMatchingRuns && (selectedCompanyId || activeCompanies.length > 0) && (
                <p className="readiness-context-status">{selectedCompanyId ? 'Select a saved run to include company evidence. The report will otherwise use tender evidence only.' : 'No company evidence selected. The report will use tender evidence only.'}</p>
              )}
            </div>
            <button
              className="button button-primary"
              type="button"
              disabled={busy !== ''}
              onClick={() => void execute(
                'Bid-readiness report',
                () => generateReadinessReport(tenderId, reportEvidenceContext),
                (result) => { setReport(result); setChecklist(result.verification_checklist) },
              )}
            >
              {busy === 'Bid-readiness report' ? 'Generating report…' : report ? 'Generate updated report' : 'Generate bid-readiness report'}
            </button>
            {report && <button type="button" className="button button-secondary" onClick={() => window.print()}>Print / save report as PDF</button>}
          </div>

          {report ? (
            <>
              <section className={`readiness-hero-v2 readiness-${report.overall_status}`}>
                <div className="readiness-hero-main">
                  <p className="eyebrow">Overall bid readiness</p>
                  <div className="readiness-status-line"><h3>{readable(report.overall_status)}</h3><Status value={report.overall_status} /></div>
                  <p>{report.summary}</p>
                </div>
                <div className="readiness-hero-side"><span>Bidder evidence</span><strong>{report.bidder_evidence_found ? 'Found' : 'Not confirmed'}</strong><small>{report.evidence_found ? 'Tender evidence was retrieved' : 'Tender evidence was not retrieved'}</small></div>
              </section>

              <div className="readiness-metrics-v2">
                <article><span className="readiness-metric-symbol">✓</span><div><strong>{report.action_items.filter((item) => item.status === 'completed' || item.status === 'verified').length}</strong><span>Completed actions</span></div></article>
                <article><span className="readiness-metric-symbol">!</span><div><strong>{report.action_items.length}</strong><span>Open action items</span></div></article>
                <article><span className="readiness-metric-symbol">⌕</span><div><strong>{report.sources.length}</strong><span>Cited sources</span></div></article>
                <article><span className="readiness-metric-symbol">▤</span><div><strong>{checklist?.item_count ?? report.verification_checklist?.item_count ?? 0}</strong><span>Checklist requirements</span></div></article>
              </div>

              <section className="readiness-actions-section">
                <div className="readiness-section-heading"><div><p className="eyebrow">Recommended next steps</p><h3>Action plan</h3><p>Work through these items before submitting the bid. The checklist below is the persistent manual record.</p></div><span className="readiness-action-total">{report.action_items.length} actions</span></div>
                <div className="readiness-action-filters" role="group" aria-label="Filter action items">
                  {(['all', 'eligibility', 'compliance'] as const).map((filter) => (
                    <button type="button" key={filter} className={readinessActionFilter === filter ? 'is-active' : ''} aria-pressed={readinessActionFilter === filter} onClick={() => setReadinessActionFilter(filter)}>
                      {filter === 'all' ? 'All actions' : readable(filter)}
                      <span>{filter === 'all' ? report.action_items.length : report.action_items.filter((item) => item.category === filter).length}</span>
                    </button>
                  ))}
                </div>
                {report.action_items.filter((item) => readinessActionFilter === 'all' || item.category === readinessActionFilter).length > 0 ? (
                  <div className="readiness-action-grid">
                    {report.action_items.filter((item) => readinessActionFilter === 'all' || item.category === readinessActionFilter).map((item, index) => (
                      <article className="readiness-action-card" key={`${item.category}-${index}`}>
                        <div className="readiness-action-top"><span className="readiness-action-number">{String(index + 1).padStart(2, '0')}</span><Status value={item.status} /></div>
                        <span className={`readiness-action-category readiness-category-${item.category}`}>{readable(item.category)}</span>
                        <p>{item.action}</p>
                        {item.source_ids.length > 0 && <div className="readiness-action-sources">Tender refs: {item.source_ids.join(', ')}</div>}
                      </article>
                    ))}
                  </div>
                ) : <div className="intel-empty-state"><strong>{report.action_items.length === 0 ? 'No open actions were returned' : `No ${readinessActionFilter} actions`}</strong><p>{report.action_items.length === 0 ? 'Review the detailed report and checklist to confirm all conditions.' : 'Choose another action filter to see the remaining recommendations.'}</p></div>}
              </section>

              <details className="intel-disclosure readiness-full-report">
                <summary>Read the complete generated report</summary>
                <div className="readiness-report-markdown"><MarkdownView content={report.report_markdown} /></div>
              </details>
              {report.sources.length > 0 && <details className="intel-disclosure"><summary>Browse all cited sources ({report.sources.length})</summary><SourceList tenderId={tenderId} documents={documents} sources={report.sources} /></details>}
            </>
          ) : (
            <div className="readiness-empty-report"><span className="intel-empty-symbol">BR</span><h3>Your readiness report will appear here</h3><p>Generate a report after running the tender, eligibility and compliance analyses. The full narrative will be available on demand, while this page keeps actions and verification status easy to scan.</p></div>
          )}

          {loadingChecklist ? (
            <p className="muted-cell">Loading saved verification checklist…</p>
          ) : checklist ? (
            <VerificationChecklistView tenderId={tenderId} checklist={checklist} onChecklistUpdated={setChecklist} />
          ) : null}
        </section>
      )}
    </section>
  )
}

function VerificationChecklistView({
  tenderId,
  checklist,
  onChecklistUpdated,
}: {
  tenderId: string
  checklist: VerificationChecklist
  onChecklistUpdated: (checklist: VerificationChecklist) => void
}) {
  const [savingItemId, setSavingItemId] = useState('')
  const [saveError, setSaveError] = useState('')
  const categories = Array.from(new Set(checklist.items.map((item) => item.category)))

  async function saveItem(
    item: VerificationItem,
    payload: { verification_status: VerificationStatus; verification_notes: string; reviewer_name: string },
  ) {
    setSavingItemId(item.id)
    setSaveError('')
    try {
      const updated = await updateVerificationItem(tenderId, checklist.id, item.id, payload)
      onChecklistUpdated(updated)
    } catch (reason) {
      setSaveError(reason instanceof Error ? reason.message : 'Checklist item could not be saved.')
    } finally {
      setSavingItemId('')
    }
  }

  return (
    <section className="intel-verification-checklist readiness-checklist-v2">
      <div className="intel-checklist-heading">
        <div>
          <p className="eyebrow">Persistent manual review</p>
          <h3>Pre-submission verification</h3>
          <p>{checklist.company_id ? 'Company-specific checklist' : 'Tender-only checklist'} · Created {dateTime(checklist.created_at)}</p>
        </div>
        <Status value={checklist.readiness_status} />
      </div>
      <div className="readiness-checklist-metrics">
        <div><strong>{checklist.item_count}</strong><span>Requirements</span></div>
        <div><strong>{checklist.verified_count}</strong><span>Verified manually</span></div>
        <div><strong>{checklist.pending_count}</strong><span>Pending / in progress</span></div>
        <div><strong>{checklist.blocked_count}</strong><span>Blocked</span></div>
      </div>
      <p className="intel-checklist-warning">Only mark a requirement verified after checking the original tender and authentic supporting documents. Reviewer names are traceability text, not authenticated identities. This checklist does not certify bid acceptance.</p>
      {saveError && <p className="form-error" role="alert">{saveError}</p>}
      {categories.length > 0 ? (
        <div className="readiness-checklist-groups">
          {categories.map((category, groupIndex) => {
            const items = checklist.items.filter((item) => item.category === category)
            const verified = items.filter((item) => item.verification_status === 'verified').length
            const blocked = items.filter((item) => item.verification_status === 'blocked').length
            return (
              <details className="readiness-checklist-group" key={category} open={groupIndex === 0}>
                <summary>
                  <span className="readiness-group-title">{readable(category)}</span>
                  <span className="readiness-group-progress">{verified}/{items.length} verified</span>
                  {blocked > 0 && <span className="readiness-group-blocked">{blocked} blocked</span>}
                </summary>
                <div className="intel-verification-list">
                  {items.map((item) => (
                    <VerificationChecklistItem item={item} key={item.id} saving={savingItemId === item.id} onSave={(payload) => void saveItem(item, payload)} />
                  ))}
                </div>
              </details>
            )
          })}
        </div>
      ) : (
        <p className="intel-empty">This checklist contains no requirements.</p>
      )}
    </section>
  )
}

function VerificationChecklistItem({
  item,
  saving,
  onSave,
}: {
  item: VerificationItem
  saving: boolean
  onSave: (payload: { verification_status: VerificationStatus; verification_notes: string; reviewer_name: string }) => void
}) {
  const [status, setStatus] = useState<VerificationStatus>(item.verification_status)
  const [reviewer, setReviewer] = useState(item.reviewer_name ?? '')
  const [notes, setNotes] = useState(item.verification_notes ?? '')

  useEffect(() => {
    setStatus(item.verification_status)
    setReviewer(item.reviewer_name ?? '')
    setNotes(item.verification_notes ?? '')
  }, [item.id, item.updated_at, item.verification_status, item.reviewer_name, item.verification_notes])

  const candidateNames = item.candidate_documents.map((candidate) => {
    const filename = typeof candidate.original_filename === 'string' ? candidate.original_filename : 'Candidate document'
    const synthetic = candidate.synthetic_test_data === true
    const expired = candidate.evidence_state === 'expired_by_metadata'
    const pending = candidate.pending_extraction === true
    return `${filename}${synthetic ? ' · Synthetic test data' : ''}${expired ? ' · Expired' : ''}${pending ? ' · Extraction review required' : ''}`
  })

  return (
    <details className="intel-verification-item">
      <summary className="intel-verification-item-summary">
        <div className="intel-verification-item-title">
          <p className="eyebrow">{readable(item.category)} · {readable(item.evidence_status)}</p>
          <strong>{item.requirement}</strong>
          <span>{item.required_action}</span>
        </div>
        <div className="intel-verification-item-state"><Status value={item.verification_status} /><span className="intel-verification-expand">Review</span></div>
      </summary>
      <div className="intel-verification-item-body">
        {candidateNames.length > 0 && (
          <div className="intel-verification-candidates">
            <strong>Evidence references</strong>
            <ul>{candidateNames.map((name, index) => <li key={`${item.id}-candidate-${index}`}>{name}</li>)}</ul>
          </div>
        )}
        {item.source_ids.length > 0 && <p className="muted-cell">Tender sources: {item.source_ids.join(', ')}</p>}
        <div className="intel-verification-fields">
          <label>
            Verification status
            <select value={status} onChange={(event) => setStatus(event.target.value as VerificationStatus)}>
              <option value="pending">Pending</option>
              <option value="in_progress">In progress</option>
              <option value="verified">Verified manually</option>
              <option value="blocked">Blocked</option>
              <option value="not_applicable">Not applicable</option>
            </select>
          </label>
          <label>
            Reviewer name {status === 'verified' ? '(required)' : '(optional)'}
            <input value={reviewer} onChange={(event) => setReviewer(event.target.value)} maxLength={255} placeholder="Name of person who checked the original documents" />
          </label>
          <label className="intel-verification-notes">
            Review notes
            <textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows={3} maxLength={5000} placeholder="Record what was checked, discrepancies or follow-up needed…" />
          </label>
          <button type="button" className="button button-secondary button-small" disabled={saving || (status === 'verified' && !reviewer.trim())} onClick={() => onSave({ verification_status: status, verification_notes: notes, reviewer_name: reviewer })}>
            {saving ? 'Saving…' : 'Save verification'}
          </button>
        </div>
        {item.verified_at && <small>Manually marked verified by {item.reviewer_name || 'reviewer'} on {dateTime(item.verified_at)}</small>}
      </div>
    </details>
  )
}

// ---------------------------------------------------------
// Click-through source viewer
// ---------------------------------------------------------

type SourceListItem = {
  source_id: string
  document_name: string
  citation: string
  location?: Record<string, unknown>
  unit_type?: string
  document_role?: string
}

function SourcePreviewDialog({
  tenderId,
  source,
  document,
  onClose,
}: {
  tenderId: string
  source: SourceListItem
  document: TenderDocument
  onClose: () => void
}) {
  const [objectUrl, setObjectUrl] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const downloadName = document.original_filename
  const pageValue = source.location?.page
  const page = typeof pageValue === 'number' && Number.isFinite(pageValue) && pageValue > 0
    ? Math.floor(pageValue)
    : 1
  const isPdf = document.extension.toLowerCase() === '.pdf' || document.content_type.toLowerCase() === 'application/pdf'

  useEffect(() => {
    let active = true
    let createdUrl = ''
    setLoading(true)
    setError('')
    downloadDocument(tenderId, document.id)
      .then((blob) => {
        if (!active) return
        const url = URL.createObjectURL(blob)
        createdUrl = url
        setObjectUrl(url)
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : 'The source file could not be opened.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
      if (createdUrl) URL.revokeObjectURL(createdUrl)
    }
  }, [tenderId, document.id])

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  return (
    <div className="source-viewer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section className="source-viewer-dialog" role="dialog" aria-modal="true" aria-labelledby="source-viewer-title">
        <header className="source-viewer-header">
          <div className="source-viewer-file-icon" aria-hidden="true">{document.extension.replace('.', '').toUpperCase().slice(0, 4) || 'FILE'}</div>
          <div className="source-viewer-heading-copy">
            <p className="eyebrow">SOURCE EVIDENCE · {source.source_id}</p>
            <h2 id="source-viewer-title">{document.original_filename}</h2>
            <p>{source.citation}</p>
          </div>
          <button type="button" className="icon-button source-viewer-close" aria-label="Close source viewer" onClick={onClose}>×</button>
        </header>
        {loading ? (
          <div className="source-viewer-state" role="status"><span className="spinner" /> Loading original source…</div>
        ) : error ? (
          <div className="source-viewer-state source-viewer-error" role="alert"><strong>Source unavailable</strong><p>{error}</p></div>
        ) : isPdf && objectUrl ? (
          <div className="source-viewer-frame-wrap">
            <div className="source-viewer-toolbar"><span>Opening page {page}</span><a href={objectUrl} download={downloadName}>Download original</a></div>
            <iframe className="source-viewer-frame" src={`${objectUrl}#page=${page}&toolbar=1&navpanes=0&view=FitH`} title={`Source PDF ${document.original_filename}, page ${page}`} />
          </div>
        ) : objectUrl ? (
          <div className="source-viewer-state">
            <strong>Original file is ready</strong>
            <p>This file type cannot be previewed in the built-in PDF viewer. Download the original and use the citation details below to locate the evidence.</p>
            <a className="button button-primary" href={objectUrl} download={downloadName}>Download original file</a>
            <div className="source-viewer-location"><span>Source location</span><strong>{source.citation}</strong></div>
          </div>
        ) : null}
      </section>
    </div>
  )
}

function SourceList({
  tenderId,
  documents,
  sources,
}: {
  tenderId: string
  documents: TenderDocument[]
  sources: SourceListItem[]
}) {
  const [selectedSource, setSelectedSource] = useState<{ source: SourceListItem; document: TenderDocument } | null>(null)
  const matchingDocuments = (filename: string) => documents.filter((doc) => doc.original_filename === filename)

  if (!sources.length) return null

  return (
    <div className="intel-sources">
      <h4>Sources</h4>
      <ul>
        {sources.map((source) => {
          const matches = matchingDocuments(source.document_name)
          return (
            <li key={`${source.source_id}-${source.document_name}`}>
              <div className="intel-source-row-copy"><strong>{source.source_id}</strong>{' — '}{source.document_name}: {source.citation}</div>
              {matches.length === 1 ? (
                <button type="button" className="source-open-button" onClick={() => setSelectedSource({ source, document: matches[0] })}>Open source <span aria-hidden="true">↗</span></button>
              ) : matches.length > 1 ? (
                <div className="source-ambiguous-actions">
                  <span>{matches.length} files share this name</span>
                  {matches.map((doc) => <button key={doc.id} type="button" className="source-open-button" onClick={() => setSelectedSource({ source, document: doc })}>Open upload from {dateTime(doc.created_at)}</button>)}
                </div>
              ) : (
                <span className="source-unavailable-label">File not currently available in this tender</span>
              )}
            </li>
          )
        })}
      </ul>
      {selectedSource && <SourcePreviewDialog tenderId={tenderId} source={selectedSource.source} document={selectedSource.document} onClose={() => setSelectedSource(null)} />}
    </div>
  )
}

// ---------------------------------------------------------
// Eligibility and compliance requirement list
// ---------------------------------------------------------

function RequirementList({
  items,
}: {
  items: Array<{
    requirement: string
    status: string
    explanation: string
    category?: string
    required_action?: string
    required_evidence?: string[]
    tender_source_ids?: string[]
    bidder_source_ids?: string[]
  }>
}) {
  if (!items.length) return <p className="intel-empty">No requirements were returned.</p>

  return (
    <div className="intel-requirements">
      {items.map((item, index) => (
        <details className="intel-requirement" key={`${item.requirement}-${index}`}>
          <summary>
            <div className="intel-requirement-title">
              {item.category && <span>{readable(item.category)}</span>}
              <strong>{item.requirement}</strong>
            </div>
            <Status value={item.status} />
          </summary>
          <div className="intel-requirement-body">
            <p>{item.explanation}</p>
            {item.required_action && <p><strong>Required action:</strong> {item.required_action}</p>}
            {item.required_evidence && item.required_evidence.length > 0 && (
              <div className="intel-required-evidence"><strong>Evidence requested</strong><ul>{item.required_evidence.map((evidence, evidenceIndex) => <li key={`${index}-${evidenceIndex}`}>{evidence}</li>)}</ul></div>
            )}
            {(item.tender_source_ids?.length || item.bidder_source_ids?.length) ? (
              <p className="intel-requirement-citations"><strong>Source references:</strong> {[
                ...(item.tender_source_ids ?? []).map((sourceId) => `Tender ${sourceId}`),
                ...(item.bidder_source_ids ?? []).map((sourceId) => `Bidder ${sourceId}`),
              ].join(' · ')}</p>
            ) : null}
          </div>
        </details>
      ))}
    </div>
  )
}

// ---------------------------------------------------------
// Full Markdown renderer
// ---------------------------------------------------------

function MarkdownView({
  content,
}: {
  content: string
}) {
  // Some generated reports contain escaped Markdown punctuation such as
  // \*\*bold\*\* and \> blockquotes. Restore those intended Markdown
  // markers before parsing so they render as formatting rather than text.
  const normalizedContent = content.replace(/\\([*_`>#])/g, '$1')

  return (
    <div className="intel-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {normalizedContent}
      </ReactMarkdown>
    </div>
  )
}
