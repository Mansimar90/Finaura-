import { useEffect, useState } from 'react';
import { LineChart, Line, ResponsiveContainer, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts';
import { api } from '../lib/api';
import { Sparkles, ArrowRight } from 'lucide-react';
import '../auth.css';

const money = (n) => `₹${Number(n || 0).toLocaleString('en-IN')}`;

export default function WhatIf({ data, isDemo }) {
  const summary = data.summary;
  const firstGoal = (data.goals || [])[0];
  const [form, setForm] = useState({
    current_monthly_savings: summary.savings || 10000,
    monthly_savings_delta: 5000,
    goal_target: firstGoal?.target_amount || 500000,
    goal_current: firstGoal?.current_amount || 0,
    expected_annual_return: 10,
    years_horizon: 5,
  });
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [goalId, setGoalId] = useState(firstGoal?.id || '');

  useEffect(() => { if (firstGoal) { setGoalId(firstGoal.id); setForm((f) => ({ ...f, goal_target: firstGoal.target_amount, goal_current: firstGoal.current_amount })); } }, [firstGoal?.id]); // eslint-disable-line

  const pickGoal = (id) => {
    setGoalId(id);
    const g = (data.goals || []).find((x) => x.id === id);
    if (g) setForm((f) => ({ ...f, goal_target: g.target_amount, goal_current: g.current_amount }));
  };

  const run = async () => {
    setBusy(true); setError(''); setResult(null);
    try {
      const { data: r } = await api.post('/whatif', { ...form,
        current_monthly_savings: Number(form.current_monthly_savings),
        monthly_savings_delta: Number(form.monthly_savings_delta),
        goal_target: Number(form.goal_target),
        goal_current: Number(form.goal_current),
        expected_annual_return: Number(form.expected_annual_return),
        years_horizon: Number(form.years_horizon),
      });
      setResult(r);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not run simulation.');
    } finally { setBusy(false); }
  };

  const upd = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const monthsToYears = (m) => m == null ? '—' : `${Math.floor(m/12)}y ${m%12}m`;
  const delta = result && result.months_to_goal_current && result.months_to_goal_proposed
    ? result.months_to_goal_current - result.months_to_goal_proposed : null;

  return (
    <div data-testid="whatif-page">
      <div className="page-intro">
        <div><div className="eyebrow">Test your future money moves</div><h2>What-If Simulator</h2><p>Change one number, see how the whole plan shifts. Projections are educational estimates.</p></div>
        <span className="ai-badge"><Sparkles size={14} /> Simulator</span>
      </div>
      {error && <div className="auth-error">{error}</div>}
      <div className="whatif-grid">
        <section className="card">
          <h3 style={{margin:'0 0 12px',font:'600 17px Outfit'}}>Your scenario</h3>
          {(data.goals || []).length > 0 && (
            <div className="auth-field">
              <label>Which goal are we simulating?</label>
              <select data-testid="whatif-goal-select" value={goalId} onChange={(e) => pickGoal(e.target.value)} style={{ width: '100%', padding: 11, borderRadius: 8, border: '1px solid #d9e4df', background: '#fbfdfc', fontSize: 13 }}>
                {(data.goals || []).map((g) => <option key={g.id} value={g.id}>{g.name} — {money(g.target_amount)}</option>)}
              </select>
            </div>
          )}
          <NumRow label="Current monthly savings (₹)" tid="whatif-current-savings" value={form.current_monthly_savings} onChange={(v) => upd('current_monthly_savings', v)} />
          <NumRow label="Change monthly savings by (₹)" tid="whatif-delta" value={form.monthly_savings_delta} onChange={(v) => upd('monthly_savings_delta', v)} hint="Positive = save more, negative = save less." />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <NumRow label="Goal target (₹)" tid="whatif-target" value={form.goal_target} onChange={(v) => upd('goal_target', v)} />
            <NumRow label="Already saved (₹)" tid="whatif-current" value={form.goal_current} onChange={(v) => upd('goal_current', v)} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <NumRow label="Expected annual return (%)" tid="whatif-return" value={form.expected_annual_return} onChange={(v) => upd('expected_annual_return', v)} hint="Equity long-term ≈ 10-12%, debt ≈ 6-7%." />
            <NumRow label="Horizon (years)" tid="whatif-horizon" value={form.years_horizon} onChange={(v) => upd('years_horizon', v)} />
          </div>
          <button data-testid="whatif-run-button" className="primary-btn full" onClick={run} disabled={busy || isDemo}>{busy ? 'Running…' : <>Run simulation <ArrowRight size={15}/></>}</button>
          {isDemo && <p style={{ fontSize: 11, color: '#8b9995', textAlign:'center', marginTop: 8 }}>Sign up to run the simulator with your own numbers.</p>}
        </section>
        <section className="card">
          <h3 style={{margin:'0 0 12px',font:'600 17px Outfit'}}>Projected outcome</h3>
          {!result && <p style={{ color: '#8b9995', fontSize: 13, textAlign: 'center', padding: 30 }}>Run a simulation to see the impact.</p>}
          {result && (
            <>
              <div className="whatif-stats">
                <div><small>Current plan</small><strong data-testid="whatif-current-months">{monthsToYears(result.months_to_goal_current)}</strong><span>to goal</span></div>
                <div className="highlight"><small>New plan</small><strong data-testid="whatif-proposed-months">{monthsToYears(result.months_to_goal_proposed)}</strong><span>to goal</span></div>
                <div><small>Difference</small><strong data-testid="whatif-delta-months" style={{ color: delta > 0 ? '#087f56' : delta < 0 ? '#a83932' : '#556b60' }}>
                  {delta == null ? '—' : (delta > 0 ? `${delta} mo faster` : delta < 0 ? `${Math.abs(delta)} mo slower` : 'no change')}
                </strong><span>impact</span></div>
              </div>
              <ResponsiveContainer width="100%" height={230}>
                <LineChart data={result.series}>
                  <CartesianGrid stroke="#edf1ef" vertical={false} />
                  <XAxis dataKey="month" tickFormatter={(v) => `${Math.round(v/12)}y`} tick={{ fill: '#94a3b8', fontSize: 11 }} axisLine={false} tickLine={false}/>
                  <YAxis tickFormatter={(v) => `₹${Math.round(v/1000)}k`} tick={{ fill: '#94a3b8', fontSize: 11 }} axisLine={false} tickLine={false}/>
                  <Tooltip formatter={(v) => money(v)} labelFormatter={(m) => `Month ${m}`} contentStyle={{ border: '1px solid #e2e8f0', borderRadius: 8 }} />
                  <Legend />
                  <Line type="monotone" dataKey="current" stroke="#94a3b8" strokeWidth={2} dot={false} name="Current" />
                  <Line type="monotone" dataKey="proposed" stroke="#10b981" strokeWidth={2.5} dot={false} name="Proposed" />
                </LineChart>
              </ResponsiveContainer>
              <p style={{ fontSize: 11, color: '#8b9995', marginTop: 10, lineHeight: 1.5 }}>{result.disclaimer}</p>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
function NumRow({ label, tid, value, onChange, hint }) {
  return (
    <div className="auth-field">
      <label>{label}</label>
      <input data-testid={tid} type="number" value={value} onChange={(e) => onChange(e.target.value)}/>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}
