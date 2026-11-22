import TenderForm from './TenderForm'
import type { Tender, TenderCreate } from '../services/tenders'

export default function NewTenderDialog({
  onClose,
  onCreate,
}: {
  onClose: () => void
  onCreate: (payload: TenderCreate) => Promise<Tender>
}) {
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <section className="dialog-card" role="dialog" aria-modal="true" aria-labelledby="new-tender-title">
        <div className="dialog-heading">
          <div>
            <p className="eyebrow">New workspace</p>
            <h2 id="new-tender-title">Create a tender</h2>
          </div>
          <button type="button" className="icon-button" aria-label="Close dialog" onClick={onClose}>×</button>
        </div>
        <p className="dialog-copy">Start with the tender details. You can add documents to the workspace later.</p>
        <TenderForm
          submitLabel="Create workspace"
          onCancel={onClose}
          onSubmit={async (payload) => {
            await onCreate(payload as TenderCreate)
          }}
        />
      </section>
    </div>
  )
}
