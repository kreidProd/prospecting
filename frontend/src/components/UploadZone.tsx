import { useRef, useState } from 'react'
import { uploadFile, type UploadResult } from '../api'

type Props = {
  label: string
  hint?: string
  kind: 'scraped' | 'existing'
  value: UploadResult | null
  onChange: (v: UploadResult | null) => void
}

export function UploadZone({ label, hint, kind, value, onChange }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)

  async function onFile(f: File) {
    setUploading(true)
    setErr(null)
    try {
      const res = await uploadFile(kind, f)
      onChange(res)
    } catch (e: any) {
      setErr(e.message || 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragging(false)
        const f = e.dataTransfer.files?.[0]
        if (f) onFile(f)
      }}
      onClick={() => inputRef.current?.click()}
      className={`group cursor-pointer rounded-xl border bg-white p-5 shadow-card transition ${
        dragging
          ? 'border-brand-500 bg-brand-50/40 ring-2 ring-brand-500/20'
          : 'border-slate-200 hover:border-slate-300 hover:shadow-pop'
      }`}
    >
      <div className="flex items-start gap-3">
        <div
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg transition ${
            value ? 'bg-emerald-50 text-emerald-600' : 'bg-slate-100 text-slate-500 group-hover:bg-brand-50 group-hover:text-brand-600'
          }`}
        >
          {value ? <CheckIcon /> : <UploadIcon />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-slate-900">{label}</div>
          {hint && <div className="mt-0.5 text-xs text-slate-500">{hint}</div>}

          <div className="mt-2 text-xs">
            {!value && !uploading && (
              <span className="text-slate-400">Click or drop a .csv file</span>
            )}
            {uploading && (
              <span className="inline-flex items-center gap-1.5 text-brand-600">
                <Spinner /> Uploading…
              </span>
            )}
            {value && !uploading && (
              <div className="flex items-center gap-2">
                <span className="truncate font-medium text-slate-700">{value.filename}</span>
                <span className="text-slate-400">·</span>
                <span className="text-slate-500">{(value.size / 1024).toFixed(1)} KB</span>
                <button
                  className="ml-auto text-slate-400 hover:text-slate-700"
                  onClick={(e) => {
                    e.stopPropagation()
                    onChange(null)
                  }}
                >
                  Remove
                </button>
              </div>
            )}
            {err && <div className="mt-1 text-red-600">{err}</div>}
          </div>
        </div>
      </div>

      <input
        ref={inputRef}
        type="file"
        accept=".csv,text/csv"
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) onFile(f)
        }}
      />
    </div>
  )
}

function UploadIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </svg>
  )
}

function CheckIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  )
}

function Spinner() {
  return (
    <svg className="animate-spin" width="12" height="12" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="4" />
      <path d="M4 12a8 8 0 018-8" stroke="currentColor" strokeWidth="4" strokeLinecap="round" />
    </svg>
  )
}
