import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { Sparkles, TrendingUp, Loader2, User } from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import '../auth.css';
import '../whatif.css';

const money = (n) => `₹${Number(n || 0).toLocaleString('en-IN')}`;
const crore = (n) => {
  const v = Number(n || 0);
  if (v >= 10000000) return `₹${(v / 10000000).toFixed(2)} Cr`;
  if (v >= 100000) return `₹${(v / 100000).toFixed(2)} L`;
  return `₹${v.toLocaleString('en-IN')}`;
};

const SCEN_COLORS = ['#087f56', '#3b82f6', '#f59e0b', '#a855f7'];

export default function DigitalTwin({ isDemo }) {
  const [monthlySavings, setMonthlySavings] = useState('');
  const [annualReturn, setAnnualReturn] = useState(8);
  const [lumpSum, setLumpSum] = useState('');
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const run = async () => {
    if (isDemo) { setError('Sign up to project your own net worth.'); return; }
    setBusy(true); setError('');
    try {
      const payload = { annual_return: Number(annualReturn) || 8.0 };
      if (monthlySavings !== '') payload.monthly_savings = Number(monthlySavings);
      if (lumpSum !== '') payload.lump_sum = Number(lumpSum);
      const { data } = await api.post('/whatif/twin', payload);
      setResult(data);
    } catch (e) {
      setError(e?.response?.status === 401 ? 'Please sign in to run your Digital Twin.' : 'Could not project your net worth. Please try again.');
    } finally { setBusy(false); }
  };

  useEffect(() => { if (!isDemo) run(); /* run once with defaults */ }, []); // eslint-disable-line

  const chartData = result ? mergeSeries(result.baseline, result.scenarios) : [];

  return (
    <div data-testid="twin-page">
      <div className="page-intro">
        <div>
          <div className="eyebrow">Your future you</div>
          <h2>Digital Twin</h2>
          <p>See where your net worth is headed in 5 and 10 years — and how small changes today rewrite the future.</p>
        </div>
        <span className="ai-badge"><Sparkles size={14} /> Projection engine</span>
      </div>

      <div className="wi-grid">
        <div className="wi-card" data-testid="twin-input-card">
          <h3><User size={18} className="wi-icon-inline" /> Fine-tune your twin</h3>
          <div className="wi-two-col">
            <div className="auth-field">
              <label>Monthly savings (₹) <small style={{ color: '#8b9995' }}>optional</small></label>
              <input data-testid="twin-monthly-input" type="number" min="0" value={monthlySavings} onChange={(e) => setMonthlySavings(e.target.value)} placeholder="Uses your profile default" />
            </div>
            <div className="auth-field">
              <label>Assumed annual return (%)</label>
              <input data-testid="twin-return-input" type="number" min="0" max="30" step="0.5" value={annualReturn} onChange={(e) => setAnnualReturn(e.target.value)} />
            </div>
          </div>
          <div className="auth-field">
            <label>Optional lump sum in year 1 (₹)</label>
            <input data-testid="twin-lumpsum-input" type="number" min="0" value={lumpSum} onChange={(e) => setLumpSum(e.target.value)} placeholder="e.g. bonus, inheritance" />
          </div>
          {error && <div className="auth-error" data-testid="twin-error">{error}</div>}
          <button data-testid="twin-run-button" className="primary-btn full" onClick={run} disabled={busy}>
            {busy ? (<><Loader2 size={16} className="wi-spin" /> Projecting…</>) : (<>Update projection <TrendingUp size={16} /></>)}
          </button>
        </div>

        {result && (
          <div className="wi-card wi-summary-card" data-testid="twin-snapshot">
            <h3>Your snapshot</h3>
            <div className="wi-metric"><span>Current net worth</span><b>{money(result.user_snapshot.base_networth)}</b></div>
            <div className="wi-metric"><span>Monthly savings</span><b>{money(result.user_snapshot.monthly_savings)}</b></div>
            <div className="wi-metric"><span>Assumed return</span><b>{result.user_snapshot.assumed_return_pct.toFixed(1)}%</b></div>
            <div className="wi-metric"><span>Projected in 5 years</span><b data-testid="twin-5y-value">{crore(result.baseline.final_5y)}</b></div>
            <div className="wi-metric"><span>Projected in 10 years</span><b data-testid="twin-10y-value">{crore(result.baseline.final_10y)}</b></div>
          </div>
        )}
      </div>

      {result && chartData.length > 0 && (
        <div className="wi-card" style={{ marginBottom: 20 }} data-testid="twin-chart-card">
          <h3><TrendingUp size={18} className="wi-icon-inline" /> Net worth over time</h3>
          <ResponsiveContainer width="100%" height={320}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2ecdf" />
              <XAxis dataKey="year" tickFormatter={(y) => `Yr ${y}`} tick={{ fontSize: 11 }} />
              <YAxis tickFormatter={crore} tick={{ fontSize: 11 }} width={80} />
              <Tooltip formatter={(v) => crore(v)} labelFormatter={(y) => `Year ${y}`} contentStyle={{ borderRadius: 8, fontSize: 12 }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line type="monotone" dataKey="baseline" stroke={SCEN_COLORS[0]} strokeWidth={2.5} name="Baseline (as-is)" dot={false} />
              {result.scenarios.map((s, i) => (
                <Line key={s.id} type="monotone" dataKey={s.id} stroke={SCEN_COLORS[(i + 1) % SCEN_COLORS.length]} strokeWidth={2} strokeDasharray="4 4" name={s.label} dot={false} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {result && (
        <div className="wi-options-grid" data-testid="twin-scenarios">
          {result.scenarios.map((s, i) => {
            const boostPct = result.baseline.final_10y > 0
              ? Math.round(((s.final_10y - result.baseline.final_10y) / result.baseline.final_10y) * 100)
              : 0;
            return (
              <div className={`wi-option ${boostPct > 15 ? 'wi-best' : ''}`} key={s.id} data-testid={`twin-scenario-${s.id}`}>
                <div className="wi-option-head">
                  <h3>{s.label}</h3>
                </div>
                <div className="wi-stats">
                  <Stat label="Net worth in 5 yrs" value={crore(s.final_5y)} good />
                  <Stat label="Net worth in 10 yrs" value={crore(s.final_10y)} good />
                  <Stat label="Monthly saved" value={money(s.monthly_savings)} />
                  <Stat label="vs baseline" value={`${boostPct >= 0 ? '+' : ''}${boostPct}%`} good={boostPct > 0} bad={boostPct < 0} />
                </div>
              </div>
            );
          })}
        </div>
      )}

      {result && <p className="wi-disclaimer">{result.disclaimer}</p>}
    </div>
  );
}

function mergeSeries(baseline, scenarios) {
  const rows = {};
  baseline.series.forEach((p) => { rows[p.year] = { year: p.year, baseline: p.balance }; });
  scenarios.forEach((s) => {
    s.series.forEach((p) => { rows[p.year] = { ...(rows[p.year] || { year: p.year }), [s.id]: p.balance }; });
  });
  return Object.values(rows).sort((a, b) => a.year - b.year);
}

function Stat({ label, value, good, bad }) {
  return (
    <div className={`wi-stat ${good ? 'good' : ''} ${bad ? 'bad' : ''}`}>
      <span className="wi-stat-l">{label}</span>
      <span className="wi-stat-v">{value}</span>
    </div>
  );
}
