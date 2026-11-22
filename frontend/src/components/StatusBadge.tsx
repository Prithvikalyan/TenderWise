import type { TenderStatus } from '../services/tenders'

const labels: Record<TenderStatus, string> = {
  draft: 'Draft',
  active: 'Active',
  archived: 'Archived',
}

export default function StatusBadge({ status }: { status: TenderStatus }) {
  return <span className={`status-badge status-${status}`}>{labels[status]}</span>
}
