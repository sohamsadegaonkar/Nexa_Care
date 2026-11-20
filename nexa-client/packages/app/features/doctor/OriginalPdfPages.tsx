'use client'

import { Button, Paragraph, XStack, YStack } from '@my/ui'
import { useEffect, useRef, useState } from 'react'
import type { PDFDocumentLoadingTask, PDFDocumentProxy, RenderTask } from 'pdfjs-dist'

// Render only pixels. The full viewer's scripting, forms, attachments, navigation
// and external link layers are never mounted. The worker ships with this app.
export function OriginalPdfPages({ file }: { file: Blob }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null)
  const [pageNumber, setPageNumber] = useState(1)
  const [error, setError] = useState(false)
  useEffect(() => {
    let dead = false
    let task: PDFDocumentLoadingTask | undefined
    async function load() {
      try {
        const lib = await import('pdfjs-dist')
        const data = new Uint8Array(await file.arrayBuffer())
        if (dead) return
        lib.GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString()
        task = lib.getDocument({ data, enableXfa: false, useWorkerFetch: false, useWasm: false,
          disableFontFace: true, useSystemFonts: false, maxImageSize: 25_000_000, stopAtErrors: true })
        const doc = await task.promise
        if (!dead) setPdf(doc)
      } catch { if (!dead) setError(true) }
    }
    void load()
    return () => { dead = true; if (task) void task.destroy().catch(() => {}) }
  }, [file])
  useEffect(() => {
    if (!pdf || !canvas.current) return
    const surface = canvas.current
    let dead = false
    let render: RenderTask | undefined
    async function paint(doc: PDFDocumentProxy) {
      try {
        const page = await doc.getPage(pageNumber)
        if (dead) return
        const original = page.getViewport({ scale: 1 })
        const viewport = page.getViewport({ scale: Math.min(2, 1600 / Math.max(original.width, original.height)) })
        surface.width = Math.ceil(viewport.width)
        surface.height = Math.ceil(viewport.height)
        render = page.render({ canvas: surface, viewport, annotationMode: 0 })
        await render.promise
      } catch { if (!dead) setError(true) }
    }
    void paint(pdf)
    return () => { dead = true; render?.cancel(); surface.width = 0; surface.height = 0 }
  }, [pdf, pageNumber])
  if (error) return <Paragraph>PDF preview unavailable.</Paragraph>
  return <YStack gap="$2">
    {!pdf && <Paragraph>Opening PDF…</Paragraph>}
    <canvas ref={canvas} aria-label={'Original report, page ' + pageNumber} style={{ maxWidth: '100%', height: 'auto' }} />
    {pdf && <XStack gap="$2" alignItems="center">
      <Button disabled={pageNumber <= 1} onPress={() => setPageNumber(pageNumber - 1)}>Previous page</Button>
      <Paragraph>Page {pageNumber} of {pdf.numPages}</Paragraph>
      <Button disabled={pageNumber >= pdf.numPages} onPress={() => setPageNumber(pageNumber + 1)}>Next page</Button>
    </XStack>}
  </YStack>
}
