import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import EvidenceMatchingPanel from './EvidenceMatchingPanel'
import { listCompanies } from '../services/companies'
import { createEvidenceMatchingRun, listEvidenceMatchingRuns, getEvidenceMatchingRun } from '../services/evidenceMatching'

vi.mock('../services/companies', () => ({ listCompanies: vi.fn() }))
vi.mock('../services/evidenceMatching', () => ({ createEvidenceMatchingRun: vi.fn(), listEvidenceMatchingRuns: vi.fn(), getEvidenceMatchingRun: vi.fn() }))

// Vitest is configured without global afterEach, so explicitly clean up each render.
afterEach(() => cleanup())

const company = {
  id: 'company-1', legal_name: 'Example Infrastructure Ltd', display_name: null,
  registration_number: null, notes: null, is_archived: false,
  created_at: '2026-11-21T00:00:00Z', updated_at: '2026-11-21T00:00:00Z',
}

const run = {
  id: 'run-1', tender_id: 'tender-1', company_id: 'company-1', status: 'completed' as const,
  eligibility_requirement_count: 1, compliance_requirement_count: 1,
  candidate_found_count: 1, potential_match_count: 0,
  requirements_with_candidates: 1, candidate_occurrence_count: 1, unique_candidate_document_count: 1,
  non_synthetic_candidate_document_count: 1, synthetic_candidate_document_count: 0,
  company_document_count: 1, latest_version_count: 1, documents_without_versions_count: 0,
  processed_company_document_count: 1, unprocessed_company_document_count: 0,
  missing_evidence_count: 0, expired_evidence_count: 0, unique_expired_document_count: 0, manual_review_count: 0,
  processing_required_count: 0, non_company_evidence_count: 0,
  summary: 'Compared 1 eligibility and 1 compliance requirement. Found one candidate.',
  created_at: '2026-11-21T12:00:00+05:30',
  results: [{
    requirement_id: 'EL-1', requirement_type: 'eligibility' as const,
    requirement: 'Bidder must demonstrate annual turnover.', category: 'financial', evidence_kind: 'financial', status: 'candidate_found' as const,
    explanation: 'A relevant company document candidate was found. Manual review is required.', required_evidence: ['Turnover certificate'],
    tender_sources: [{ agent: 'eligibility' as const, source_id: 'T1', document_name: 'Tender.pdf', citation: 'Page 2, block 4', location: { page: 2 } }],
    pending_versions: [], experience_evidence: null,
    candidates: [{
      company_document_id: 'doc-1', document_title: 'Turnover Certificate', category: 'turnover_certificate',
      version_id: 'version-1', version_number: 1, original_filename: 'turnover.pdf', issue_date: null, expiry_date: null,
      financial_year: '2025-26', issuer: null, reference_number: 'SAMPLE-REF', relevance_score: 0.76,
      matched_terms: ['turnover', 'financial'], evidence_kind: 'financial', category_match: true, synthetic_test_data: false, evidence_state: 'validity_not_recorded' as const,
      review_note: 'Check the original document.', units: [
        { extraction_unit_id: 'unit-1', unit_index: 0, unit_type: 'text', source_ref: { page: 1 }, excerpt: 'Annual turnover was reported for the financial year 2025-26.' },
        { extraction_unit_id: 'unit-2', unit_index: 1, unit_type: 'text', source_ref: { paragraph_index: 6, body_order_index: 7 }, excerpt: 'Turnover certificate details.' },
        { extraction_unit_id: 'unit-3', unit_index: 2, unit_type: 'table', source_ref: { table_index: 1, body_order_index: 8 }, excerpt: 'Financial year | Turnover' },
      ],
    }],
  }],
}

describe('EvidenceMatchingPanel', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.mocked(listCompanies).mockResolvedValue({ items: [company], total: 1, limit: 100, offset: 0 })
    vi.mocked(createEvidenceMatchingRun).mockResolvedValue(run)
    vi.mocked(listEvidenceMatchingRuns).mockResolvedValue({ items: [], total: 0, page: 1, limit: 20, offset: 0 })
    vi.mocked(getEvidenceMatchingRun).mockResolvedValue(run)
  })

  it('runs matching for the selected company and displays source-linked candidates', async () => {
    render(<EvidenceMatchingPanel tenderId="tender-1" />)
    expect(await screen.findByRole('option', { name: 'Example Infrastructure Ltd' })).toBeTruthy()

    fireEvent.change(screen.getByRole('combobox', { name: 'Company profile' }), { target: { value: 'company-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Match company evidence' }))

    expect(await screen.findByText('Bidder must demonstrate annual turnover.')).toBeTruthy()
    expect(screen.getByText('Turnover Certificate')).toBeTruthy()
    expect(screen.getByText(/Page 2, block 4/)).toBeTruthy()
    expect(screen.getByText(/Matching is scoped to/)).toBeTruthy()
    expect(screen.getByText('High text relevance')).toBeTruthy()
    expect(screen.getByText(/Paragraph 7/)).toBeTruthy()
    expect(screen.getByText(/Table · Table 2/)).toBeTruthy()
    expect(screen.getByText(/Evidence category compatible/)).toBeTruthy()
    expect(screen.getByText(/requirements with candidates/)).toBeTruthy()
    expect(createEvidenceMatchingRun).toHaveBeenCalledWith('tender-1', 'company-1')
  })

  it('shows exact pending versions and distinguishes contract references from qualifying contracts', async () => {
    vi.mocked(createEvidenceMatchingRun).mockResolvedValue({
      ...run,
      requirements_with_candidates: 1,
      candidate_occurrence_count: 2,
      unique_candidate_document_count: 2,
      non_synthetic_candidate_document_count: 1,
      synthetic_candidate_document_count: 1,
      results: [
        {
          ...run.results[0],
          requirement_id: 'EL-2',
          evidence_kind: 'experience',
          experience_evidence: {
            distinct_referenced_contract_count: 1,
            unreferenced_document_count: 0,
            contract_groups: [{
              reference_number: 'WO-2025-001',
              documents: [{
                version_id: 'version-1', document_title: 'Work order', original_filename: 'work-order.pdf',
                category: 'work_order', version_number: 1, evidence_role: 'order_document', synthetic_test_data: false,
              }],
              has_order_document: true, has_completion_certificate: false,
            }],
            unreferenced_documents: [],
            note: 'Reference grouping does not establish qualification.',
          },
        },
        {
          ...run.results[0],
          requirement_id: 'CO-2',
          status: 'processing_required',
          candidates: [],
          pending_versions: [{
            company_document_id: 'doc-2', document_title: 'Audited financial statements', category: 'audited_financial_statements',
            version_id: 'version-2', version_number: 1, original_filename: 'audited-statements.pdf',
            inferred_evidence_kind: 'financial', processing_state: 'not_queued',
          }],
          experience_evidence: null,
        },
      ],
    })
    render(<EvidenceMatchingPanel tenderId="tender-1" />)
    await screen.findByRole('option', { name: 'Example Infrastructure Ltd' })
    fireEvent.change(screen.getByRole('combobox', { name: 'Company profile' }), { target: { value: 'company-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Match company evidence' }))

    expect(await screen.findByText(/Contract reference grouping/)).toBeTruthy()
    expect(screen.getByText(/WO-2025-001/)).toBeTruthy()
    expect(screen.getByText(/Reference grouping does not establish qualification/)).toBeTruthy()
    expect(screen.getByText(/Relevant latest versions awaiting usable extraction/)).toBeTruthy()
    expect(screen.getByText(/audited-statements.pdf/)).toBeTruthy()
    expect(screen.getByText(/Not queued for processing/)).toBeTruthy()
  })
})
