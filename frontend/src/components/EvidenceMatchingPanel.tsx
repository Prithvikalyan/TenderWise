import { useEffect, useMemo, useState } from 'react'
import { listCompanies, type Company } from '../services/companies'
import {
  createEvidenceMatchingRun,
  getEvidenceMatchingRun,
  listEvidenceMatchingRuns,
  type EvidenceMatchingRun,
  type EvidenceMatchStatus,
  type EvidenceRequirementMatch,
} from '../services/evidenceMatching'

export interface EvidenceMatchingContext { companyId: string; matchingRunId: string }

const statusLabels: Record<EvidenceMatchStatus, string> = {
  candidate_found: 'Candidate found',
  potential_match: 'Potential match',
  missing_evidence: 'Evidence not located',
  expired_evidence: 'Expired evidence',
  manual_verification_required: 'Manual verification',
  processing_required: 'Processing required',
  not_company_evidence: 'Tender action — not company evidence',
}

function statusTone(status: EvidenceMatchStatus): string {
  if (status === 'candidate_found') return 'match-status match-status-candidate'
  if (status === 'expired_evidence' || status === 'missing_evidence') return 'match-status match-status-warning'
  if (status === 'manual_verification_required' || status === 'processing_required' || status === 'not_company_evidence') return 'match-status match-status-review'
  return 'match-status match-status-potential'
}

function formatDate(value: string | null): string {
  if (!value) return 'Not recorded'
  const date = new Date(`${value}T00:00:00`)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)
}

function sourceLocation(location: Record<string, unknown>, fallback: string): string {
  const page = location.page
  const paragraph = location.paragraph_index
  const block = location.block_index
  if (typeof page === 'number' && typeof paragraph === 'number') return `Page ${page}, paragraph ${paragraph + 1}`
  if (typeof page === 'number' && typeof block === 'number') return `Page ${page}, block ${block}`
  if (typeof page === 'number') return `Page ${page}`
  if (typeof paragraph === 'number') return `Paragraph ${paragraph + 1}`
  const table = location.table_index
  if (typeof table === 'number') return `Table ${table + 1}`
  const bodyOrder = location.body_order_index
  if (typeof bodyOrder === 'number') return `Document order ${bodyOrder + 1}`
  return fallback
}

const processingStateLabels: Record<string, string> = {
  not_queued: 'Not queued for processing',
  pending: 'Queued / pending',
  processing: 'Currently processing',
  failed: 'Latest processing attempt failed',
  completed_without_text: 'Processed, but no usable text was extracted',
}

function relevanceLabel(score: number): string {
  if (score >= 0.75) return 'High text relevance'
  if (score >= 0.45) return 'Moderate text relevance'
  return 'Low text relevance'
}

function RequirementResult({ item }: { item: EvidenceRequirementMatch }) {
  return (
    <details className="evidence-match-row">
      <summary className="evidence-requirement-summary">
        <div className="evidence-match-heading">
          <div>
            <p className="eyebrow">{item.requirement_type} · {item.evidence_kind.replace(/_/g, ' ')}</p>
            <h3>{item.requirement}</h3>
            <p className="evidence-match-explanation">{item.explanation}</p>
          </div>
          <span className={statusTone(item.status)}>{statusLabels[item.status]}</span>
        </div>
        <span className="evidence-expand-hint">Expand requirement details</span>
      </summary>
      <div className="evidence-requirement-details">
      {item.required_evidence.length > 0 && (
        <div className="evidence-match-subsection">
          <strong>Evidence requested by the tender</strong>
          <ul>{item.required_evidence.map((value, index) => <li key={`${item.requirement_id}-req-${index}`}>{value}</li>)}</ul>
        </div>
      )}

      {item.tender_sources.length > 0 && (
        <div className="evidence-match-subsection">
          <strong>Tender sources</strong>
          <div className="evidence-source-list">
            {item.tender_sources.map((source) => (
              <div className="evidence-source-chip" key={`${source.agent}-${source.source_id}`}>
                <span>{source.document_name}</span>
                <small>{source.agent} · {source.citation || sourceLocation(source.location, 'Location unavailable')}</small>
              </div>
            ))}
          </div>
        </div>
      )}

      {item.experience_evidence && (
        <div className="evidence-match-subsection">
          <strong>Contract reference grouping · review aid</strong>
          <p className="evidence-match-explanation">
            {item.experience_evidence.distinct_referenced_contract_count} distinct explicit contract/order reference(s) identified;{' '}
            {item.experience_evidence.unreferenced_document_count} matched document(s) have no identifiable reference.
          </p>
          {item.experience_evidence.contract_groups.map((group) => (
            <div className="evidence-source-chip" key={group.reference_number}>
              <span><b>Reference: {group.reference_number}</b></span>
              <small>
                {group.has_order_document ? 'Order document found' : 'No order document in this group'} ·{' '}
                {group.has_completion_certificate ? 'Completion certificate found' : 'No completion certificate in this group'}
              </small>
              {group.documents.map((document) => (
                <small key={document.version_id}>
                  {document.document_title} · {document.original_filename} · Version {document.version_number}
                  {document.synthetic_test_data ? ' · Synthetic test data' : ''}
                </small>
              ))}
            </div>
          ))}
          {item.experience_evidence.unreferenced_documents.map((document) => (
            <div className="evidence-source-chip" key={document.version_id}>
              <span>{document.document_title} · {document.original_filename} · Version {document.version_number}</span>
              <small>No explicit contract reference identified{document.synthetic_test_data ? ' · Synthetic test data' : ''}</small>
            </div>
          ))}
          <p className="evidence-review-note">{item.experience_evidence.note}</p>
        </div>
      )}

      <div className="evidence-match-subsection">
        <strong>Company evidence candidates ({item.candidates.length})</strong>
        {item.candidates.length === 0 ? (
          <p className="evidence-empty-candidates">No extracted company document was identified as a relevant candidate.</p>
        ) : (
          <div className="evidence-candidate-list">
            {item.candidates.map((candidate) => (
              <div className="evidence-candidate" key={`${candidate.company_document_id}-${candidate.version_id}`}>
                <div className="evidence-candidate-heading">
                  <div>
                    <b>{candidate.document_title}</b>
                    <small>{candidate.original_filename} · Version {candidate.version_number} · {candidate.category.replace(/_/g, ' ')}</small>
                  </div>
                  <span className="evidence-score">{relevanceLabel(candidate.relevance_score)}</span>
                </div>
                <div className="evidence-candidate-meta">
                  <span>Issued: {formatDate(candidate.issue_date)}</span>
                  <span>Expires: {formatDate(candidate.expiry_date)}</span>
                  {candidate.issuer && <span>Issuer: {candidate.issuer}</span>}
                  {candidate.reference_number && <span>Ref: {candidate.reference_number}</span>}
                  {candidate.financial_year && <span>FY: {candidate.financial_year}</span>}
                  <span>{candidate.category_match ? 'Evidence category compatible' : 'Content-only match'}</span>
                </div>
                {candidate.synthetic_test_data && <p className="evidence-warning">Synthetic test data detected — do not use as genuine procurement evidence.</p>}
                {candidate.evidence_state === 'expired_by_metadata' && <p className="evidence-warning">The recorded expiry date is in the past.</p>}
                {candidate.evidence_state === 'validity_not_recorded' && <p className="evidence-review-note">No expiry date is recorded. Check the original file and tender conditions.</p>}
                {candidate.matched_terms.length > 0 && <p className="evidence-matched-terms">Matched terms: {candidate.matched_terms.join(', ')}</p>}
                <p className="evidence-review-note">{candidate.review_note}</p>
                {candidate.units.map((unit) => (
                  <details className="evidence-unit-detail" key={unit.extraction_unit_id}>
                    <summary>{unit.unit_type === 'table' ? 'Table' : 'Extracted text'} · {sourceLocation(unit.source_ref, 'Source location unavailable')}</summary>
                    <pre>{unit.excerpt}</pre>
                  </details>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>

      {item.pending_versions.length > 0 && (
        <div className="evidence-match-subsection">
          <strong>Relevant latest versions awaiting usable extraction ({item.pending_versions.length})</strong>
          <div className="evidence-source-list">
            {item.pending_versions.map((version) => (
              <div className="evidence-source-chip" key={version.version_id}>
                <span><b>{version.document_title}</b></span>
                <small>{version.original_filename} · Version {version.version_number} · {version.category.replace(/_/g, ' ')}</small>
                <small>Suggested type: {version.inferred_evidence_kind.replace(/_/g, ' ')} · {processingStateLabels[version.processing_state] ?? version.processing_state}</small>
              </div>
            ))}
          </div>
        </div>
      )}
      </div>
    </details>
  )
}

function dateTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

export default function EvidenceMatchingPanel({ tenderId, onRunSelected }: { tenderId: string; onRunSelected?: (context: EvidenceMatchingContext | null) => void }) {
  const [companies, setCompanies] = useState<Company[]>([])
  const [companyId, setCompanyId] = useState(() => window.localStorage.getItem(`tenderwise:evidence-company:${tenderId}`) ?? '')
  const [loadingCompanies, setLoadingCompanies] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [run, setRun] = useState<EvidenceMatchingRun | null>(null)
  const [savedRuns, setSavedRuns] = useState<Array<Omit<EvidenceMatchingRun, 'results'>>>([])
  const [loadingSavedRuns, setLoadingSavedRuns] = useState(false)
  const [loadingSavedRun, setLoadingSavedRun] = useState(false)

  const activeCompanies = useMemo(() => companies.filter((company) => !company.is_archived), [companies])
  const selectedCompany = activeCompanies.find((company) => company.id === companyId) ?? null

  useEffect(() => {
    if (companyId) window.localStorage.setItem(`tenderwise:evidence-company:${tenderId}`, companyId)
    else window.localStorage.removeItem(`tenderwise:evidence-company:${tenderId}`)
  }, [companyId, tenderId])

  useEffect(() => {
    if (!companyId) {
      setSavedRuns([])
      setRun(null)
      onRunSelected?.(null)
      return
    }
    let current = true
    setLoadingSavedRuns(true)
    setError('')
    listEvidenceMatchingRuns(tenderId, companyId, { page: 1, limit: 20 })
      .then(async (result) => {
        if (!current) return
        const items = result.items
          .filter((item) => item.tender_id === tenderId && item.company_id === companyId)
          .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
        setSavedRuns(items)
        if (items.length) {
          const latest = await getEvidenceMatchingRun(tenderId, companyId, items[0].id)
          if (!current) return
          setRun(latest)
          onRunSelected?.({ companyId, matchingRunId: latest.id })
        } else {
          setRun(null)
          onRunSelected?.(null)
        }
      })
      .catch((reason: unknown) => {
        if (current) {
          setSavedRuns([])
          setRun(null)
          setError(reason instanceof Error ? reason.message : 'Saved matching runs could not be loaded.')
        }
      })
      .finally(() => { if (current) setLoadingSavedRuns(false) })
    return () => { current = false }
  }, [companyId, tenderId])

  useEffect(() => {
    let current = true
    listCompanies({ limit: 100, offset: 0 })
      .then((result) => {
        if (!current) return
        setCompanies(result.items)
      })
      .catch((reason: unknown) => {
        if (current) setError(reason instanceof Error ? reason.message : 'Company profiles could not be loaded.')
      })
      .finally(() => { if (current) setLoadingCompanies(false) })
    return () => { current = false }
  }, [])

  async function runMatching() {
    if (!companyId) return
    setBusy(true)
    setError('')
    try {
      const result = await createEvidenceMatchingRun(tenderId, companyId)
      if (result.company_id !== companyId) {
        throw new Error('The API returned results for a different company profile. The results were not displayed.')
      }
      setRun(result)
      setSavedRuns((existing) => [result, ...existing.filter((item) => item.id !== result.id)])
      onRunSelected?.({ companyId, matchingRunId: result.id })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Evidence matching could not be completed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="content-card evidence-matching-panel">
      <div className="company-section-heading">
        <div>
          <p className="eyebrow">Company evidence intelligence</p>
          <h2>Tender requirement matching</h2>
          <p className="company-section-copy">Compare cited eligibility and compliance requirements with processed company document versions.</p>
        </div>
        <div className="evidence-match-controls">
          <label className="evidence-company-select">
            <span>Company profile</span>
            <select value={companyId} onChange={(event) => { setCompanyId(event.target.value); setRun(null); setSavedRuns([]); onRunSelected?.(null) }} disabled={loadingCompanies || !activeCompanies.length}>
              <option value="">Choose a company profile…</option>
              {activeCompanies.map((company) => <option value={company.id} key={company.id}>{company.display_name || company.legal_name}</option>)}
            </select>
          </label>
          <button className="button button-primary" onClick={() => void runMatching()} disabled={!selectedCompany || busy || loadingCompanies}>
            {busy ? 'Matching requirements…' : 'Match company evidence'}
          </button>
        </div>
      </div>

      {loadingCompanies ? <div className="loading-state company-loading" role="status"><span className="spinner" /> Loading company profiles…</div> : activeCompanies.length === 0 ? (
        <div className="company-empty-inline"><h3>No active company profile</h3><p>Create a company and upload/Process its documents before running evidence matching.</p><a className="text-button" href="/companies">Open company documents</a></div>
      ) : (
        <p className="evidence-match-guidance">Choose the exact company profile before matching. Matching is strictly scoped to that company and searches only its active document versions. It is document discovery, not a decision that a requirement is satisfied.</p>
      )}

      {companyId && (
        <div className="evidence-history-bar">
          <label className="evidence-company-select">
            <span>Saved assessments</span>
            <select value={run?.id ?? ''} disabled={loadingSavedRuns || loadingSavedRun || savedRuns.length === 0} onChange={async (event) => {
              const runId = event.target.value
              if (!runId) return
              setLoadingSavedRun(true)
              setError('')
              try {
                const saved = await getEvidenceMatchingRun(tenderId, companyId, runId)
                if (saved.tender_id !== tenderId || saved.company_id !== companyId) throw new Error('The saved assessment does not belong to this tender and company.')
                setRun(saved)
                onRunSelected?.({ companyId, matchingRunId: saved.id })
              } catch (reason) { setError(reason instanceof Error ? reason.message : 'The saved assessment could not be opened.') }
              finally { setLoadingSavedRun(false) }
            }}>
              {savedRuns.length === 0 ? <option value="">No saved assessments</option> : <><option value="">Choose a saved assessment…</option>{savedRuns.map((item) => <option key={item.id} value={item.id}>Assessment · {dateTime(item.created_at)}</option>)}</>}
            </select>
          </label>
          {(loadingSavedRuns || loadingSavedRun) && <span className="evidence-history-status" role="status">Loading saved assessment…</span>}
        </div>
      )}
      {error && <div className="company-alert company-alert-error" role="alert">{error}</div>}
      {busy && <div className="loading-state company-loading" role="status"><span className="spinner" /> Running eligibility and compliance analysis, then matching company evidence…</div>}

      {run && (
        <div className="evidence-match-results">
          <p className="evidence-processing-note">Matching is scoped to <strong>{selectedCompany?.display_name || selectedCompany?.legal_name || 'selected company'}</strong>. Only this company's active document versions are searched.</p>
          <div className="evidence-run-summary">
            <div><p className="eyebrow">Assessment · {dateTime(run.created_at)}</p><h3>Evidence discovery results</h3><p>{run.summary}</p></div>
            <div className="evidence-run-stats">
              <span><b>{run.candidate_found_count}</b>Strong candidates</span>
              <span><b>{run.potential_match_count}</b>Potential matches</span>
              <span><b>{run.missing_evidence_count}</b>Not located</span>
              <span><b>{run.expired_evidence_count}</b>Expired candidates</span>
              <span><b>{run.manual_review_count}</b>Manual review</span>
              <span><b>{run.processing_required_count}</b>Need processing</span>
            </div>
          </div>
          <details className="evidence-coverage-details"><summary>Coverage and matching statistics</summary><p className="evidence-processing-note">{run.eligibility_requirement_count} eligibility and {run.compliance_requirement_count} compliance requirements · {run.requirements_with_candidates} requirements with candidates · {run.candidate_occurrence_count} requirement-to-document matches · {run.unique_candidate_document_count} unique candidate versions ({run.non_synthetic_candidate_document_count} non-synthetic, {run.synthetic_candidate_document_count} synthetic) · {run.unique_expired_document_count} unique expired versions · {run.non_company_evidence_count} tender actions.</p><p className="evidence-processing-note">Company coverage: {run.company_document_count} active document record(s); {run.latest_version_count} latest uploaded version(s), {run.processed_company_document_count} with usable extraction and {run.unprocessed_company_document_count} needing processing; {run.documents_without_versions_count} active record(s) have no uploaded version. Re-run matching after evidence or metadata changes.</p></details>
          <div className="evidence-match-list">
            {run.results.map((item) => <RequirementResult item={item} key={`${item.requirement_type}-${item.requirement_id}`} />)}
            {run.results.length === 0 && <div className="company-empty-inline"><h3>No cited requirements found</h3><p>Process tender documents and verify the eligibility/compliance analysis before matching.</p></div>}
          </div>
        </div>
      )}

      <p className="page-footnote">A candidate is not proof of authenticity, validity, threshold compliance or tender acceptance. Missing repository evidence is not proof of non-compliance.</p>
    </section>
  )
}
