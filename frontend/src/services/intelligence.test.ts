import { afterEach, describe, expect, it, vi } from 'vitest'
import { streamTenderQuestion } from './intelligence'

afterEach(() => vi.unstubAllGlobals())

describe('streaming tender Q&A client', () => {
  it('collects draft deltas and returns the final validated answer', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('event: meta\ndata: {"sources":[]}\n\n'))
        controller.enqueue(encoder.encode('event: delta\ndata: {"text":"The EMD is "}\n\n'))
        controller.enqueue(encoder.encode('event: delta\ndata: {"text":"INR 80,000 [S1]."}\n\n'))
        controller.enqueue(encoder.encode('event: done\ndata: {"question":"What is the EMD?","answer":"The EMD is INR 80,000 [S1].","sources":[],"evidence_found":true}\n\n'))
        controller.close()
      },
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, body: stream }))
    const deltas: string[] = []

    const result = await streamTenderQuestion('tender-123', 'What is the EMD?', (text) => deltas.push(text))

    expect(deltas.join('')).toBe('The EMD is INR 80,000 [S1].')
    expect(result.answer).toBe('The EMD is INR 80,000 [S1].')
    expect(result.evidence_found).toBe(true)
  })

  it('surfaces a server-sent error event as a useful error', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('event: error\ndata: {"message":"AI provider request timed out."}\n\n'))
        controller.close()
      },
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, body: stream }))

    await expect(streamTenderQuestion('tender-123', 'What is the EMD?', () => undefined))
      .rejects.toThrow('AI provider request timed out.')
  })
})
