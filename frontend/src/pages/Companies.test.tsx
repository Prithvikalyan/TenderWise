import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Companies from './Companies'
import {
  archiveCompanyDocument,
  getCompanyExtractionUnits,
  getCompanyProcessingRun,
  listCompanies,
  listCompanyDocuments,
  listCompanyProcessingRuns,
  type Company,
  type CompanyDocument,
  type CompanyProcessingRunDetail,
} from '../services/companies'

vi.mock('../services/companies', () => ({
  COMPANY_DOCUMENT_CATEGORIES: ['business_registration', 'gst_certificate', 'pan_card', 'audited_financial_statements', 'turnover_certificate', 'purchase_order', 'work_order', 'completion_certificate', 'non_blacklisting_declaration', 'technical_certification', 'license', 'other'],
  archiveCompanyDocument: vi.fn(),
  createCompany: vi.fn(),
  getCompanyExtractionUnits: vi.fn(),
  getCompanyProcessingRun: vi.fn(),
  listCompanies: vi.fn(),
  listCompanyDocuments: vi.fn(),
  listCompanyProcessingRuns: vi.fn(),
  queueCompanyProcessingRun: vi.fn(),
  updateCompanyDocumentVersionMetadata: vi.fn(),
  uploadCompanyDocument: vi.fn(),
}))

const company: Company = {
  id: 'company-1',
  legal_name: 'Asterion Infra Labs Private Limited [SAMPLE ENTITY]',
  display_name: null,
  registration_number: null,
  notes: null,
  is_archived: false,
  created_at: '2026-11-21T10:00:00Z',
  updated_at: '2026-11-21T10:00:00Z',
}

const document: CompanyDocument = {
  id: 'document-1',
  company_id: company.id,
  title: 'GST Registration Profile',
  category: 'gst_certificate',
  is_archived: false,
  created_at: '2026-11-21T10:00:00Z',
  updated_at: '2026-11-21T10:00:00Z',
  version_count: 1,
  current_version: {
    id: 'version-1',
    document_id: 'document-1',
    version_number: 1,
    original_filename: '01_SAMPLE_GST_PROFILE.pdf',
    extension: '.pdf',
    content_type: 'application/pdf',
    size_bytes: 3163,
    sha256: null,
    issue_date: '2024-04-01',
    expiry_date: null,
    financial_year: null,
    issuer: null,
    reference_number: 'SAMPLE-GST-0001',
    notes: null,
    created_at: '2026-11-21T10:00:00Z',
  },
}

const completedRun: CompanyProcessingRunDetail = {
  id: 'run-1',
  company_id: company.id,
  status: 'completed',
  requested_document_count: 1,
  created_at: '2026-11-21T10:01:00Z',
  started_at: '2026-11-21T10:01:01Z',
  completed_at: '2026-11-21T10:01:02Z',
  updated_at: '2026-11-21T10:01:02Z',
  item_counts: { pending: 0, processing: 0, completed: 1, failed: 0 },
  items: [{
    id: 'item-1',
    company_document_version_id: 'version-1',
    status: 'completed',
    outcome_code: 'processed',
    failure_class: null,
    attempt_count: 1,
    max_attempts: 3,
    safe_message: null,
    extraction_unit_count: 18,
    created_at: '2026-11-21T10:01:00Z',
    started_at: '2026-11-21T10:01:01Z',
    completed_at: '2026-11-21T10:01:02Z',
  }],
  total_items: 1,
  page: 1,
  limit: 20,
  offset: 0,
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listCompanies).mockResolvedValue({ items: [company], total: 1, limit: 100, offset: 0 })
  vi.mocked(listCompanyDocuments).mockResolvedValue({ items: [document], total: 1, limit: 100, offset: 0 })
  vi.mocked(listCompanyProcessingRuns).mockResolvedValue({ items: [completedRun], total: 1, page: 1, limit: 20, offset: 0 })
  vi.mocked(getCompanyProcessingRun).mockResolvedValue(completedRun)
  vi.mocked(getCompanyExtractionUnits).mockResolvedValue({
    run_id: 'run-1',
    company_document_id: document.id,
    company_document_version_id: 'version-1',
    items: [{
      id: 'unit-1',
      unit_index: 0,
      unit_type: 'text',
      source_ref: { page: 1, filename: '01_SAMPLE_GST_PROFILE.pdf', source_scope: 'company' },
      content_text: 'Legal name: Asterion Infra Labs Private Limited [SAMPLE ENTITY]',
      table_data: null,
      metadata: { format: 'pdf', extractor: 'pymupdf' },
      created_at: '2026-11-21T10:02:00Z',
    }],
    total: 1,
    page: 1,
    limit: 500,
    offset: 0,
  })
})

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('Company document repository', () => {
  it('removes a company document from the active library after confirmation while retaining its history', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(archiveCompanyDocument).mockResolvedValue({ ...document, is_archived: true })
    vi.mocked(listCompanyDocuments)
      .mockResolvedValueOnce({ items: [document], total: 1, limit: 100, offset: 0 })
      .mockResolvedValueOnce({ items: [], total: 0, limit: 100, offset: 0 })

    render(<Companies onBack={vi.fn()} />)
    expect(await screen.findByText('GST Registration Profile')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Remove GST Registration Profile' }))

    expect(confirmSpy).toHaveBeenCalled()
    expect(archiveCompanyDocument).toHaveBeenCalledWith(company.id, document.id)
    expect(await screen.findByText(/was removed from the active company document library/)).toBeTruthy()
    expect(screen.queryByText('GST Registration Profile')).toBeNull()

    confirmSpy.mockRestore()
  })

  it('does not archive a company document when removal is cancelled', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<Companies onBack={vi.fn()} />)

    expect(await screen.findByText('GST Registration Profile')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Remove GST Registration Profile' }))

    expect(confirmSpy).toHaveBeenCalled()
    expect(archiveCompanyDocument).not.toHaveBeenCalled()
    expect(screen.getByText('GST Registration Profile')).toBeTruthy()

    confirmSpy.mockRestore()
  })

  it('shows completed processing and previews extracted text with its source page', async () => {
    const user = userEvent.setup()
    render(<Companies onBack={vi.fn()} />)

    expect(await screen.findByText('GST Registration Profile')).toBeTruthy()
    expect(await screen.findByText('18 extraction units')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'View extraction' }))

    expect(await screen.findByText(/Legal name: Asterion Infra Labs/)).toBeTruthy()
    expect(screen.getByText('Page 1')).toBeTruthy()
    expect(getCompanyExtractionUnits).toHaveBeenCalledWith(company.id, document.id, 'version-1')
  })
})
