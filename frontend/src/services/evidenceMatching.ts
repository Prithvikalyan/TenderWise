export type EvidenceMatchStatus =
  | 'candidate_found'
  | 'potential_match'
  | 'missing_evidence'
  | 'expired_evidence'
  | 'manual_verification_required'
  | 'processing_required'
  | 'not_company_evidence'

export interface TenderEvidenceCitation {
  agent: 'eligibility' | 'compliance'
  source_id: string
  document_name: string
  citation: string
  location: Record<string, unknown>
}

export interface CompanyEvidenceUnitReference {
  extraction_unit_id: string
  unit_index: number
  unit_type: string
  source_ref: Record<string, unknown>
  excerpt: string
}

export interface CompanyEvidencePendingVersion {
  company_document_id: string
  document_title: string
  category: string
  version_id: string
  version_number: number
  original_filename: string
  inferred_evidence_kind: string
  processing_state: 'not_queued' | 'pending' | 'processing' | 'failed' | 'completed_without_text'
}

export interface ExperienceEvidenceDocument {
  version_id: string
  document_title: string
  original_filename: string
  category: string
  version_number: number
  evidence_role: 'order_document' | 'completion_certificate'
  synthetic_test_data: boolean
}

export interface ExperienceContractGroup {
  reference_number: string
  documents: ExperienceEvidenceDocument[]
  has_order_document: boolean
  has_completion_certificate: boolean
}

export interface ExperienceEvidenceSummary {
  distinct_referenced_contract_count: number
  unreferenced_document_count: number
  contract_groups: ExperienceContractGroup[]
  unreferenced_documents: ExperienceEvidenceDocument[]
  note: string
}

export interface CompanyEvidenceCandidate {
  company_document_id: string
  document_title: string
  category: string
  version_id: string
  version_number: number
  original_filename: string
  issue_date: string | null
  expiry_date: string | null
  financial_year: string | null
  issuer: string | null
  reference_number: string | null
  relevance_score: number
  matched_terms: string[]
  evidence_kind: string
  category_match: boolean
  synthetic_test_data: boolean
  evidence_state: 'not_expired_by_metadata' | 'expired_by_metadata' | 'validity_not_recorded'
  review_note: string
  units: CompanyEvidenceUnitReference[]
}

export interface EvidenceRequirementMatch {
  requirement_id: string
  requirement_type: 'eligibility' | 'compliance'
  requirement: string
  category: string
  evidence_kind: string
  status: EvidenceMatchStatus
  explanation: string
  required_evidence: string[]
  tender_sources: TenderEvidenceCitation[]
  candidates: CompanyEvidenceCandidate[]
  pending_versions: CompanyEvidencePendingVersion[]
  experience_evidence: ExperienceEvidenceSummary | null
}

export interface EvidenceMatchingRun {
  id: string
  tender_id: string
  company_id: string
  status: 'completed'
  eligibility_requirement_count: number
  compliance_requirement_count: number
  candidate_found_count: number
  potential_match_count: number
  requirements_with_candidates: number
  candidate_occurrence_count: number
  unique_candidate_document_count: number
  non_synthetic_candidate_document_count: number
  synthetic_candidate_document_count: number
  company_document_count: number
  latest_version_count: number
  documents_without_versions_count: number
  processed_company_document_count: number
  unprocessed_company_document_count: number
  missing_evidence_count: number
  expired_evidence_count: number
  unique_expired_document_count: number
  manual_review_count: number
  processing_required_count: number
  non_company_evidence_count: number
  summary: string
  created_at: string
  results: EvidenceRequirementMatch[]
}

export interface EvidenceMatchingRunList {
  items: Omit<EvidenceMatchingRun, 'results'>[]
  total: number
  page: number
  limit: number
  offset: number
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail)) {
      const first = body.detail[0] as { msg?: unknown } | undefined
      if (typeof first?.msg === 'string') return first.msg
    }
  } catch {
    // Keep a status-based error when the response is not JSON.
  }
  return `Request failed (${response.status}).`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    })
  } catch {
    throw new Error('Could not reach the TenderWise API. Check that the backend is running.')
  }
  if (!response.ok) throw new Error(await readError(response))
  return (await response.json()) as T
}

function matchingPath(tenderId: string, companyId: string): string {
  return `/api/tenders/${encodeURIComponent(tenderId)}/companies/${encodeURIComponent(companyId)}/evidence-matching-runs`
}

/** Creates a synchronous matching snapshot. The backend runs both requirement agents first. */
export function createEvidenceMatchingRun(tenderId: string, companyId: string): Promise<EvidenceMatchingRun> {
  return request<EvidenceMatchingRun>(matchingPath(tenderId, companyId), { method: 'POST' })
}

export function listEvidenceMatchingRuns(
  tenderId: string,
  companyId: string,
  params: { page?: number; limit?: number } = {},
): Promise<EvidenceMatchingRunList> {
  const query = new URLSearchParams({ page: String(params.page ?? 1), limit: String(params.limit ?? 10) })
  return request<EvidenceMatchingRunList>(`${matchingPath(tenderId, companyId)}?${query.toString()}`)
}

export function getEvidenceMatchingRun(
  tenderId: string,
  companyId: string,
  runId: string,
): Promise<EvidenceMatchingRun> {
  return request<EvidenceMatchingRun>(`${matchingPath(tenderId, companyId)}/${encodeURIComponent(runId)}`)
}
