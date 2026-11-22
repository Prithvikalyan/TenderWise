import { useState, type FormEvent } from 'react'
import type { Tender, TenderCreate, TenderStatus, TenderUpdate } from '../services/tenders'

type FormValues = {
  name: string
  reference_number: string
  issuing_organization: string
  department: string
  description: string
  status: TenderStatus
}

const emptyValues: FormValues = {
  name: '',
  reference_number: '',
  issuing_organization: '',
  department: '',
  description: '',
  status: 'draft',
}

function valuesFor(tender?: Tender): FormValues {
  return tender
    ? {
        name: tender.name,
        reference_number: tender.reference_number ?? '',
        issuing_organization: tender.issuing_organization ?? '',
        department: tender.department ?? '',
        description: tender.description ?? '',
        status: tender.status,
      }
    : emptyValues
}

function optionalValue(value: string): string | null {
  return value.trim() || null
}

export default function TenderForm({
  tender,
  onSubmit,
  onCancel,
  submitLabel,
}: {
  tender?: Tender
  onSubmit: (payload: TenderCreate | TenderUpdate) => Promise<void>
  onCancel?: () => void
  submitLabel: string
}) {
  const [values, setValues] = useState<FormValues>(() => valuesFor(tender))
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  function update<K extends keyof FormValues>(key: K, value: FormValues[K]) {
    setValues((current) => ({ ...current, [key]: value }))
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const name = values.name.trim()
    if (!name) {
      setError('Enter a tender name to continue.')
      return
    }
    if (name.length > 255) {
      setError('Tender name must be 255 characters or fewer.')
      return
    }

    setError('')
    setSaving(true)
    const common = {
      name,
      reference_number: optionalValue(values.reference_number),
      issuing_organization: optionalValue(values.issuing_organization),
      department: optionalValue(values.department),
      description: optionalValue(values.description),
    }
    try {
      await onSubmit(tender ? { ...common, status: values.status } : common)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The tender could not be saved.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <form className="tender-form" onSubmit={handleSubmit} noValidate>
      <label className="field field-full">
        <span>Tender name <b aria-hidden="true">*</b></span>
        <input
          autoFocus
          required
          maxLength={255}
          value={values.name}
          onChange={(event) => update('name', event.target.value)}
          placeholder="For example, Road maintenance works"
          aria-label="Tender name"
        />
      </label>
      <label className="field">
        <span>Reference number</span>
        <input
          maxLength={255}
          value={values.reference_number}
          onChange={(event) => update('reference_number', event.target.value)}
          placeholder="Optional"
          aria-label="Reference number"
        />
      </label>
      <label className="field">
        <span>Issuing organization</span>
        <input
          maxLength={255}
          value={values.issuing_organization}
          onChange={(event) => update('issuing_organization', event.target.value)}
          placeholder="Optional"
          aria-label="Issuing organization"
        />
      </label>
      <label className="field">
        <span>Department</span>
        <input
          maxLength={255}
          value={values.department}
          onChange={(event) => update('department', event.target.value)}
          placeholder="Optional"
          aria-label="Department"
        />
      </label>
      {tender && (
        <label className="field">
          <span>Status</span>
          <select value={values.status} onChange={(event) => update('status', event.target.value as TenderStatus)}>
            <option value="draft">Draft</option>
            <option value="active">Active</option>
            <option value="archived">Archived</option>
          </select>
        </label>
      )}
      <label className="field field-full">
        <span>Description</span>
        <textarea
          rows={4}
          value={values.description}
          onChange={(event) => update('description', event.target.value)}
          placeholder="Add a short note about this tender (optional)"
          aria-label="Description"
        />
      </label>
      {error && <p className="form-error field-full" role="alert">{error}</p>}
      <div className="form-actions field-full">
        {onCancel && <button type="button" className="button button-secondary" onClick={onCancel}>Cancel</button>}
        <button type="submit" className="button button-primary" disabled={saving}>
          {saving ? 'Saving…' : submitLabel}
        </button>
      </div>
    </form>
  )
}
