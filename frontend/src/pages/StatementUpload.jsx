import { useEffect, useMemo, useState } from 'react';
import { Upload, Check, ChevronRight, ArrowRight, FileText, X } from 'lucide-react';
import { api } from '../lib/api';
import '../auth.css';

const MAPPING_FIELDS = [
  { key: 'date', label: 'Transaction date' },
  { key: 'description', label: 'Description / narration' },
  { key: 'amount', label: 'Signed amount (single column)' },
  { key: 'debit', label: 'Debit / withdrawal column' },
  { key: 'credit', label: 'Credit / deposit column' },
  { key: 'type', label: 'Type (CR / DR)' },
];

const money = (n) => `₹${Number(n || 0).toLocaleString('en-IN')}`;

export default function StatementUpload({ onImported }) {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [mapping, setMapping] = useState({});
  const [parsed, setParsed] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [imported, setImported] = useState(0);
  const [step, setStep] = useState('choose'); // choose -> map (csv) -> review -> done

  const reset = () => {
    setFile(null); setPreview(null); setMapping({}); setParsed([]); setError('');
    setImported(0); setStep('choose');
  };

  const handleFile = async (f) => {
    if (!f) return;
    setError(''); setBusy(true); setFile(f);
    try {
      const form = new FormData();
      form.append('file', f);
      const { data } = await api.post('/statements/preview', form);
      setPreview(data);
      if (data.kind === 'csv' || data.kind === 'excel') {
        setMapping(data.guess || {});
        setStep('map');
      } else {
        // PDF: server already extracted transactions
        setParsed(data.transactions || []);
        setStep('review');
      }
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not read that file.');
    } finally { setBusy(false); }
  };

  const runParse = async () => {
    setBusy(true); setError('');
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('mapping', JSON.stringify(mapping));
      const { data } = await api.post('/statements/parse', form);
      setParsed(data.transactions || []);
      setStep('review');
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not extract transactions.');
    } finally { setBusy(false); }
  };

  const commit = async () => {
    if (!parsed.length) return;
    setBusy(true); setError('');
    try {
      const { data } = await api.post('/statements/confirm-import', { transactions: parsed });
      setImported(data.imported);
      setStep('done');
      onImported?.(data.imported);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not save.');
    } finally { setBusy(false); }
  };

  const updateTxn = (idx, patch) => setParsed((prev) => prev.map((t, i) => (i === idx ? { ...t, ...patch } : t)));
  const removeTxn = (idx) => setParsed((prev) => prev.filter((_, i) => i !== idx));

  return (
    <div className="statement-upload" data-testid="statement-upload">
      {error && <div className="auth-error" data-testid="upload-error">{error}</div>}
      {step === 'choose' && (
        <div className="upload-dropzone">
          <div className="upload-icon"><Upload size={22} /></div>
          <h3>Upload your bank statement</h3>
          <p>CSV, Excel (.xlsx) or PDF · up to 10 MB. Nothing is stored until you confirm.</p>
          <input data-testid="statement-file-input" type="file" accept=".csv,.xlsx,.xls,.pdf,application/pdf,text/csv" onChange={(e) => handleFile(e.target.files?.[0])} />
          {busy && <p style={{ fontSize: 12, color: '#8b9995', marginTop: 12 }}>Reading your file…</p>}
        </div>
      )}
      {step === 'map' && preview && (
        <div>
          <div className="upload-stepbar" data-testid="upload-mapping-step">
            <span className="active">1. Map columns</span> <ChevronRight size={14} /> <span>2. Review</span> <ChevronRight size={14} /> <span>3. Import</span>
          </div>
          <p style={{ fontSize: 13, color: '#556b60' }}>
            Detected {preview.total_rows} rows in <b>{file?.name}</b>. Match the columns from your file to Finaura's fields.
            Use <i>Signed amount</i> if your statement has one amount column, or <i>Debit / Credit</i> if it splits them.
          </p>
          <div className="mapping-grid">
            {MAPPING_FIELDS.map((f) => (
              <div key={f.key} className="auth-field">
                <label>{f.label}</label>
                <select data-testid={`map-${f.key}`} value={mapping[f.key] || ''} onChange={(e) => setMapping((m) => ({ ...m, [f.key]: e.target.value || undefined }))}>
                  <option value="">— Not in file —</option>
                  {(preview.columns || []).map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
            ))}
          </div>
          <div className="preview-table">
            <div className="eyebrow">Sample rows</div>
            <div className="table-wrap">
              <table>
                <thead><tr>{preview.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                <tbody>
                  {(preview.rows || []).map((r, i) => <tr key={i}>{preview.columns.map((c) => <td key={c}>{String(r[c] ?? '')}</td>)}</tr>)}
                </tbody>
              </table>
            </div>
          </div>
          <div className="upload-actions">
            <button data-testid="upload-cancel-button" className="outline-btn" onClick={reset}>Cancel</button>
            <button data-testid="upload-parse-button" className="primary-btn" disabled={busy || !mapping.date || !mapping.description} onClick={runParse}>
              {busy ? 'Extracting…' : <>Extract transactions <ArrowRight size={15} /></>}
            </button>
          </div>
        </div>
      )}
      {step === 'review' && (
        <div>
          <div className="upload-stepbar">
            <span>1. Map columns</span> <ChevronRight size={14} /> <span className="active">2. Review</span> <ChevronRight size={14} /> <span>3. Import</span>
          </div>
          <p style={{ fontSize: 13, color: '#556b60' }}>
            {parsed.length} transaction{parsed.length === 1 ? '' : 's'} found. Correct anything Finaura got wrong before importing.
            {preview?.kind === 'pdf' && <> PDF extraction is best-effort — always double-check.</>}
          </p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Date</th><th>Description</th><th>Type</th><th>Category</th><th>Amount</th><th></th></tr></thead>
              <tbody>
                {parsed.length === 0 && <tr><td colSpan={6} style={{ textAlign: 'center', padding: 30, color: '#8b9995' }}>No transactions extracted. Try adjusting your column mapping.</td></tr>}
                {parsed.map((t, i) => (
                  <tr key={i} data-testid={`review-txn-${i}`}>
                    <td>{t.date}</td>
                    <td><input value={t.description} onChange={(e) => updateTxn(i, { description: e.target.value })} style={{ border: '1px solid #e2e8f0', padding: '4px 6px', borderRadius: 4, fontSize: 12, width: '100%' }} /></td>
                    <td>
                      <select value={t.type} onChange={(e) => updateTxn(i, { type: e.target.value })}>
                        <option>Expense</option><option>Income</option>
                      </select>
                    </td>
                    <td>
                      <select value={t.category} onChange={(e) => updateTxn(i, { category: e.target.value })}>
                        {['Income','Food','Shopping','Transport','Rent','Bills','Education','Entertainment','Healthcare','Other'].map((c) => <option key={c}>{c}</option>)}
                      </select>
                    </td>
                    <td className={t.type === 'Income' ? 'amount-income' : ''}>{t.type === 'Income' ? '+' : '−'}{money(t.amount)}</td>
                    <td><button className="icon-btn" onClick={() => removeTxn(i)} data-testid={`remove-review-${i}`}><X size={14} /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="upload-actions">
            <button className="outline-btn" onClick={reset} data-testid="upload-restart-button">Start over</button>
            <button data-testid="upload-import-button" className="primary-btn" disabled={busy || parsed.length === 0} onClick={commit}>
              {busy ? 'Importing…' : `Import ${parsed.length} transactions`}
            </button>
          </div>
        </div>
      )}
      {step === 'done' && (
        <div className="upload-done" data-testid="upload-done">
          <div className="upload-icon" style={{ background: '#e5f8ef', color: '#087f56' }}><Check size={22} /></div>
          <h3>Imported {imported} transactions</h3>
          <p>They're now available in your Statements table.</p>
          <button className="primary-btn" onClick={reset} data-testid="upload-another-button">Upload another statement</button>
        </div>
      )}
    </div>
  );
}
