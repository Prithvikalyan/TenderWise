import type { EvidenceMatchingRun } from './evidenceMatching'

import type { TenderDocument } from './documents'

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // Fall through to a status-based message.
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

const base = (tenderId: string) => `/api/tenders/${encodeURIComponent(tenderId)}`

export interface ProcessingRun {
  id: string
  tender_id: string
  status: string
  requested_document_count: number
  created_at: string
  started_at: string | null
  completed_at: string | null
  updated_at: string
}
export interface ProcessingItem {
  id: string
  document_id: string | null
  status: string
  outcome_code: string | null
  failure_class: string | null
  attempt_count: number
  max_attempts: number
  safe_message: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}
export interface ProcessingRunDetail extends ProcessingRun {
  item_counts: { pending: number; processing: number; completed: number; failed: number }
  items: ProcessingItem[]
  total_items: number
  page: number
  limit: number
  offset: number
}
export interface ProcessingRunCreated extends ProcessingRun {
  items: ProcessingItem[]
}
export interface ExtractionPreviewUnit {
  unit_index: number
  unit_type: string
  citation: string
  content_text: string
}
export interface ProcessingItemExtraction {
  run_id: string
  item_id: string
  document_id: string
  filename: string
  item_status: string
  outcome_code: string | null
  extraction_unit_count: number
  has_extracted_text: boolean
  units_truncated: boolean
  units: ExtractionPreviewUnit[]
}

export interface Source {
  source_id: string
  document_name: string
  unit_type: string
  location: Record<string, unknown>
  citation: string
}
export interface QAResponse {
  question: string
  answer: string
  sources: Source[]
  evidence_found: boolean
  retrieval_mode?: 'hybrid' | 'keyword_fallback' | 'none'
  semantic_candidate_count?: number
  conversation_id?: string | null
  rerank_mode?: 'nvidia_cross_encoder' | 'rrf_only' | 'disabled'
  citation_validation_status?: 'valid_structure' | 'needs_review' | 'no_sources'
  citation_validation_details?: Array<{
    claim: string
    source_ids: string[]
    token_overlap: number
    numeric_values_match: boolean
    status: 'support_overlap_found' | 'needs_review'
    validation_method: string
  }>
  retrieval_trace?: Record<string, unknown>
}

export interface QAConversationSummary {
  id: string
  tender_id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
}

export interface QAConversationMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  response_json: QAResponse | null
  created_at: string
}

export interface QAConversationDetail extends QAConversationSummary {
  messages: QAConversationMessage[]
}

export interface QARetrievalStatus {
  database_dialect: string
  pgvector_available: boolean
  pgvector_version: string | null
  semantic_retrieval_enabled: boolean
  embedding_provider_configured: boolean
  embedding_model: string
  embedding_dimensions: number
  dense_retrieval_ready: boolean
  reranker_enabled: boolean
  reranker_configured: boolean
  reranker_model: string
  hnsw_ef_search?: number
  candidate_k?: number
  rrf_k?: number
  chunker_version?: string
  chunk_target_tokens?: number
}
export interface AnalysisResponse {
  tender_id: string
  report: string
  sources: Source[]
  evidence_found: boolean
}
export type EligibilityStatus = 'met' | 'not_met' | 'evidence_missing' | 'needs_verification'
export interface EligibilityRequirement {
  requirement: string
  category: string
  status: EligibilityStatus
  explanation: string
  required_evidence: string[]
  tender_source_ids: string[]
  bidder_source_ids: string[]
}
export interface EligibilitySource extends Source {
  document_role: 'tender' | 'bidder'
}
export interface EligibilityResponse {
  tender_id: string
  overall_status: 'ready' | 'not_ready' | 'incomplete' | 'needs_review' | 'no_requirements_found'
  summary: string
  requirements: EligibilityRequirement[]
  sources: EligibilitySource[]
  evidence_found: boolean
  bidder_evidence_found: boolean
  extracted_unit_count: number
  reviewed_source_count: number
}
export type ComplianceStatus = 'compliant' | 'non_compliant' | 'evidence_missing' | 'needs_verification'
export interface ComplianceRequirement {
  requirement: string
  category: string
  status: ComplianceStatus
  explanation: string
  required_action: string
  tender_source_ids: string[]
  bidder_source_ids: string[]
}
export interface TenderInformationItem {
  item: string
  category: 'submission_deadline' | 'clarification_deadline' | 'bid_opening' | 'other'
  details: string
  tender_source_ids: string[]
}
export interface PostAwardObligation {
  obligation: string
  category: string
  details: string
  tender_source_ids: string[]
}
export interface ComplianceSource extends Source {
  document_role: 'tender' | 'bidder'
}
export interface ComplianceResponse {
  tender_id: string
  overall_status: 'compliant' | 'non_compliant' | 'incomplete' | 'needs_review' | 'no_requirements_found'
  summary: string
  requirements: ComplianceRequirement[]
  tender_information: TenderInformationItem[]
  post_award_obligations: PostAwardObligation[]
  sources: ComplianceSource[]
  evidence_found: boolean
  bidder_evidence_found: boolean
}
export interface ReportAction {
  action: string
  category: 'eligibility' | 'compliance'
  status: string
  source_ids: string[]
}
export interface ReportSource {
  source_id: string
  agent: 'tender_analysis' | 'eligibility' | 'compliance'
  original_source_id: string
  document_name: string
  document_role: string
  unit_type: string
  location: Record<string, unknown>
  citation: string
}
export type VerificationStatus = 'pending' | 'in_progress' | 'verified' | 'blocked' | 'not_applicable'

export interface VerificationItem {
  id: string
  requirement_key: string
  category: string
  requirement: string
  required_action: string
  evidence_status: string
  verification_status: VerificationStatus
  source_ids: string[]
  candidate_documents: Array<Record<string, unknown>>
  verification_notes: string | null
  reviewer_name: string | null
  verified_at: string | null
  created_at: string
  updated_at: string
}

export interface VerificationChecklist {
  id: string
  tender_id: string
  company_id: string | null
  evidence_matching_run_id: string | null
  readiness_status: string
  item_count: number
  verified_count: number
  pending_count: number
  blocked_count: number
  created_at: string
  updated_at: string
  items: VerificationItem[]
}

export interface BidReadinessReport {
  tender_id: string
  overall_status: 'ready' | 'not_ready' | 'incomplete' | 'needs_review' | 'insufficient_evidence'
  summary: string
  report_markdown: string
  tender_analysis: AnalysisResponse
  eligibility_requirements: EligibilityRequirement[]
  compliance_requirements: ComplianceRequirement[]
  tender_information: TenderInformationItem[]
  post_award_obligations: PostAwardObligation[]
  action_items: ReportAction[]
  sources: ReportSource[]
  company_name: string | null
  evidence_matching: EvidenceMatchingRun | null
  verification_checklist: VerificationChecklist | null
  evidence_found: boolean
  bidder_evidence_found: boolean
}

export function startProcessing(tenderId: string, documentIds: string[]) {
  return request<ProcessingRunCreated>(`${base(tenderId)}/processing-runs`, {
    method: 'POST',
    body: JSON.stringify({ document_ids: documentIds }),
  })
}
export function getProcessingRun(tenderId: string, runId: string) {
  return request<ProcessingRunDetail>(`${base(tenderId)}/processing-runs/${encodeURIComponent(runId)}`)
}
export function listProcessingRuns(tenderId: string) {
  return request<{ items: ProcessingRun[]; total: number; page: number; limit: number; offset: number }>(
    `${base(tenderId)}/processing-runs?page=1&limit=10`,
  )
}
export function getProcessingItemExtraction(tenderId: string, runId: string, itemId: string) {
  return request<ProcessingItemExtraction>(
    `${base(tenderId)}/processing-runs/${encodeURIComponent(runId)}/items/${encodeURIComponent(itemId)}/extraction`,
  )
}
export function askQuestion(tenderId: string, question: string) {
  return request<QAResponse>(`${base(tenderId)}/qa`, {
    method: 'POST',
    body: JSON.stringify({ question }),
  })
}
export async function streamTenderQuestion(
  tenderId: string,
  question: string,
  onDelta: (text: string) => void,
  conversationId?: string | null,
): Promise<QAResponse> {
  let response: Response
  try {
    response = await fetch(`${base(tenderId)}/qa/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ question, conversation_id: conversationId || null }),
    })
  } catch {
    throw new Error('Could not reach the TenderWise API. Check that the backend is running.')
  }

  if (!response.ok) throw new Error(await readError(response))
  if (!response.body) throw new Error('The browser could not open the streaming response.')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const completion: { response: QAResponse | null } = { response: null }

  const processFrame = (frame: string) => {
    let eventName = 'message'
    const dataLines: string[] = []
    for (const line of frame.split(/\r?\n/)) {
      if (line.startsWith('event:')) eventName = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }
    if (!dataLines.length) return
    const payload = JSON.parse(dataLines.join('\n')) as Record<string, unknown>
    if (eventName === 'delta' && typeof payload.text === 'string') onDelta(payload.text)
    if (eventName === 'done') completion.response = payload as unknown as QAResponse
    if (eventName === 'error') {
      throw new Error(typeof payload.message === 'string' ? payload.message : 'The answer could not be completed.')
    }
  }

  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
      const frames = buffer.split(/\r?\n\r?\n/)
      buffer = frames.pop() ?? ''
      for (const frame of frames) processFrame(frame)
      if (done) break
    }
    if (buffer.trim()) processFrame(buffer)
  } catch (reason) {
    await reader.cancel().catch(() => undefined)
    if (reason instanceof SyntaxError) throw new Error('The streaming response was malformed.')
    throw reason
  }

  if (!completion.response) throw new Error('The answer stream ended before a final validated response was received.')
  return completion.response
}


export function listQAConversations(tenderId: string) {
  return request<QAConversationSummary[]>(`${base(tenderId)}/qa/conversations`)
}

export function readQAConversation(tenderId: string, conversationId: string) {
  return request<QAConversationDetail>(`${base(tenderId)}/qa/conversations/${encodeURIComponent(conversationId)}`)
}

export async function deleteQAConversation(tenderId: string, conversationId: string): Promise<void> {
  const response = await fetch(`${base(tenderId)}/qa/conversations/${encodeURIComponent(conversationId)}`, { method: 'DELETE' })
  if (!response.ok) throw new Error(await readError(response))
}

export function getQARetrievalStatus() {
  return request<QARetrievalStatus>('/api/qa/retrieval-status')
}

export function runTenderAnalysis(tenderId: string) {
  return request<AnalysisResponse>(`${base(tenderId)}/analysis`, { method: 'POST' })
}
export function runEligibility(tenderId: string) {
  return request<EligibilityResponse>(`${base(tenderId)}/eligibility`, { method: 'POST' })
}
export function runCompliance(tenderId: string) {
  return request<ComplianceResponse>(`${base(tenderId)}/compliance`, { method: 'POST' })
}
export function generateReadinessReport(
  tenderId: string,
  evidenceContext?: { companyId: string; matchingRunId: string } | null,
) {
  const query = new URLSearchParams()
  if (evidenceContext) {
    query.set('company_id', evidenceContext.companyId)
    query.set('matching_run_id', evidenceContext.matchingRunId)
  }
  const queryString = query.toString()
  const suffix = queryString ? `?${queryString}` : ''
  return request<BidReadinessReport>(`${base(tenderId)}/report${suffix}`, { method: 'POST' })
}

export function getLatestVerificationChecklist(tenderId: string) {
  return request<VerificationChecklist | null>(`${base(tenderId)}/verification-checklists/latest`)
}

export function updateVerificationItem(
  tenderId: string,
  checklistId: string,
  itemId: string,
  payload: { verification_status: VerificationStatus; verification_notes: string; reviewer_name: string },
) {
  return request<VerificationChecklist>(
    `${base(tenderId)}/verification-checklists/${encodeURIComponent(checklistId)}/items/${encodeURIComponent(itemId)}`,
    { method: 'PATCH', body: JSON.stringify(payload) },
  )
}

export function selectableDocuments(documents: TenderDocument[]) {
  return documents.filter((document) => Boolean(document.id))
}
