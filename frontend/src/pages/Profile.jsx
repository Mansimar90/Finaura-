import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { useAuth } from '../lib/auth';
import { Save, X, Check } from 'lucide-react';
import '../auth.css';

const INTERESTS = ['Mutual funds', 'Stocks', 'SIP', 'Tax planning', 'Retirement', 'Real estate', 'Crypto', 'Insurance', 'Emergency fund'];
const EXPERIENCE = ['beginner', 'intermediate', 'advanced'];
const RISK = ['conservative', 'balanced', 'aggressive'];

export default function Profile({ isDemo }) {
  const navigate = useNavigate();
  const { user, refreshMe, formatApiError } = useAuth();
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (isDemo) {
      setForm({
        name: 'Aarav Sharma', email: 'aarav@finauraai.dev', occupation: 'Product Designer', age: 29,
        location: 'Bengaluru', phone: '+91 98xxxx1234', financial_experience: 'intermediate',
        risk_tolerance: 'balanced', interests: ['Mutual funds', 'SIP', 'Tax planning'],
        monthly_income: 185000, monthly_expenses: 123000, current_savings: 500000,
        investments: 250000, debt: 120000, emi: 18000, avatar_url: '',
      });
      return;
    }
    api.get('/user/profile').then((r) => setForm({ ...r.data, interests: r.data.interests || [] }))
       .catch((err) => setError(formatApiError(err)));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDemo]);

  const upd = (k, v) => { setForm((f) => ({ ...f, [k]: v })); setSaved(false); };
  const toggle = (v) => upd('interests', form.interests?.includes(v) ? form.interests.filter((x) => x !== v) : [...(form.interests||[]), v]);

  const save = async () => {
    if (isDemo) { navigate('/signup'); return; }
    setBusy(true); setError('');
    try {
      const num = (v) => v === '' || v === null ? null : Number(v);
      await api.patch('/user/profile', {
        name: form.name, occupation: form.occupation, age: num(form.age),
        phone: form.phone, dob: form.dob, location: form.location,
        financial_experience: form.financial_experience, risk_tolerance: form.risk_tolerance,
        interests: form.interests, avatar_url: form.avatar_url,
        monthly_income: num(form.monthly_income), monthly_expenses: num(form.monthly_expenses),
        current_savings: num(form.current_savings), investments: num(form.investments),
        debt: num(form.debt), emi: num(form.emi),
      });
      await refreshMe();
      setSaved(true);
    } catch (err) { setError(formatApiError(err)); }
    finally { setBusy(false); }
  };

  if (!form) return <p style={{ padding: 40, color: '#8b9995' }}>Loading your profile…</p>;

  return (
    <div className="profile-page" data-testid="profile-page">
      <div className="page-intro">
        <div><div className="eyebrow">Your identity in FINAURA AI</div><h2>Profile</h2><p>Keep this up to date so your insights match your reality.</p></div>
        {saved && <span className="success-banner" data-testid="profile-saved-banner" style={{padding:'8px 12px',fontSize:12}}><Check size={14}/> Saved</span>}
      </div>
      {error && <div className="auth-error" data-testid="profile-error">{error}</div>}
      {isDemo && <div className="auth-note">You're editing the demo profile. Sign up to save your own changes.</div>}
      <div className="profile-grid">
        <section className="profile-card">
          <h3>About you</h3>
          <Row label="Full name" tid="profile-name" value={form.name} onChange={(v) => upd('name', v)} />
          <Row label="Email" tid="profile-email" value={form.email} disabled />
          <Row label="Phone" tid="profile-phone" value={form.phone || ''} onChange={(v) => upd('phone', v)} placeholder="+91 …" />
          <Row label="Occupation" tid="profile-occupation" value={form.occupation || ''} onChange={(v) => upd('occupation', v)} />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <Row label="Date of birth" tid="profile-dob" type="date" value={form.dob || ''} onChange={(v) => upd('dob', v)} />
            <Row label="Age" tid="profile-age" type="number" value={form.age || ''} onChange={(v) => upd('age', v)} />
          </div>
          <Row label="Location" tid="profile-location" value={form.location || ''} onChange={(v) => upd('location', v)} placeholder="City" />
        </section>
        <section className="profile-card">
          <h3>Financial persona</h3>
          <Select label="Financial experience" tid="profile-experience" value={form.financial_experience || ''} options={['', ...EXPERIENCE]} onChange={(v) => upd('financial_experience', v)} />
          <Select label="Risk tolerance" tid="profile-risk" value={form.risk_tolerance || ''} options={['', ...RISK]} onChange={(v) => upd('risk_tolerance', v)} />
          <div className="auth-field">
            <label>Financial interests</label>
            <div className="chip-row" data-testid="profile-interests">
              {INTERESTS.map((i) => (
                <button key={i} type="button" data-testid={`interest-${i.replaceAll(' ','-').toLowerCase()}`} className={`chip ${form.interests?.includes(i) ? 'active' : ''}`} onClick={() => toggle(i)}>{i}</button>
              ))}
            </div>
          </div>
        </section>
        <section className="profile-card">
          <h3>Money snapshot</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <Row label="Monthly income (₹)" tid="profile-income" type="number" value={form.monthly_income ?? ''} onChange={(v) => upd('monthly_income', v)} />
            <Row label="Monthly expenses (₹)" tid="profile-expenses" type="number" value={form.monthly_expenses ?? ''} onChange={(v) => upd('monthly_expenses', v)} />
            <Row label="Current savings (₹)" tid="profile-savings" type="number" value={form.current_savings ?? ''} onChange={(v) => upd('current_savings', v)} />
            <Row label="Investments (₹)" tid="profile-investments" type="number" value={form.investments ?? ''} onChange={(v) => upd('investments', v)} />
            <Row label="Debt (₹)" tid="profile-debt" type="number" value={form.debt ?? ''} onChange={(v) => upd('debt', v)} />
            <Row label="Monthly EMI (₹)" tid="profile-emi" type="number" value={form.emi ?? ''} onChange={(v) => upd('emi', v)} />
          </div>
        </section>
      </div>
      <div className="profile-actions">
        <button data-testid="profile-cancel-button" className="outline-btn" onClick={() => window.location.reload()} disabled={busy}><X size={15}/> Reset</button>
        <button data-testid="profile-save-button" className="primary-btn" onClick={save} disabled={busy}><Save size={15}/> {busy ? 'Saving…' : 'Save profile'}</button>
      </div>
    </div>
  );
}

function Row({ label, value, onChange, tid, disabled, placeholder, type = 'text' }) {
  return (
    <div className="auth-field">
      <label>{label}</label>
      <input data-testid={tid} type={type} value={value ?? ''} onChange={(e) => onChange?.(e.target.value)} disabled={disabled} placeholder={placeholder}/>
    </div>
  );
}
function Select({ label, value, options, onChange, tid }) {
  return (
    <div className="auth-field">
      <label>{label}</label>
      <select data-testid={tid} value={value} onChange={(e) => onChange(e.target.value)} style={{ width: '100%', padding: 11, borderRadius: 8, border: '1px solid #d9e4df', background: '#fbfdfc', fontFamily: 'inherit', fontSize: 13 }}>
        {options.map((o) => <option key={o} value={o}>{o || 'Not specified'}</option>)}
      </select>
    </div>
  );
}
