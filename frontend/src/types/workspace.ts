export type TenderWorkspaceView =
  | 'overview'
  | 'documents'
  | 'tender-analysis'
  | 'eligibility-analysis'
  | 'compliance-analysis'
  | 'qa'
  | 'evidence'
  | 'readiness'

export const tenderWorkspaceViews: Array<{
  id: TenderWorkspaceView
  label: string
  shortLabel: string
  description: string
}> = [
  { id: 'overview', label: 'Overview', shortLabel: 'Home', description: 'Workspace summary and navigation' },
  { id: 'documents', label: 'Documents', shortLabel: 'Files', description: 'Upload, organize and process tender documents' },
  { id: 'tender-analysis', label: 'Tender analysis', shortLabel: 'Tender', description: 'Scope, dates, BOQ, commercial terms and risks' },
  { id: 'eligibility-analysis', label: 'Eligibility analysis', shortLabel: 'Eligibility', description: 'Bidder qualification criteria and evidence' },
  { id: 'compliance-analysis', label: 'Compliance analysis', shortLabel: 'Compliance', description: 'Submission, technical and post-award requirements' },
  { id: 'qa', label: 'Tender Q&A', shortLabel: 'Q&A', description: 'Ask questions and inspect cited answers' },
  { id: 'evidence', label: 'Company evidence', shortLabel: 'Evidence', description: 'Match company documents to tender requirements' },
  { id: 'readiness', label: 'Bid readiness', shortLabel: 'Readiness', description: 'Review readiness, open actions and manual verification' },
]

export function parseTenderWorkspaceView(value: string | null): TenderWorkspaceView {
  if (!value) return 'overview'
  return tenderWorkspaceViews.some((item) => item.id === value)
    ? value as TenderWorkspaceView
    : 'overview'
}
