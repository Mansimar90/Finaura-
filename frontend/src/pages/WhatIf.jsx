import { useState } from 'react';
import { api } from '../lib/api';
import { Sparkles, ArrowRight, Zap, Check, X, AlertTriangle, Star, TrendingDown, Wallet, Loader2 } from 'lucide-react';
import '../auth.css';
import '../whatif.css';

const money = (n) => `₹${Number(n || 0).toLocaleString('en-IN')}`;

const CATEGORY_OPTIONS = [
  'Electronics', 'Vehicle', 'Home', 'Travel', 'Health', 'Education', 'Investment', 'Other'
];

export default function WhatIf({ isDemo }) {
  const [mode, setMode] = useState('purchase'); // 'purchase' | 'subscription'
  const [form, setForm] = useState({
    item_name: '',
    amount: 100000,
    category: 'Electronics',
    recurring_monthly_cost: 0,
    purchase_date: '',
    notes: '',
  });
  const [subForm, setSubForm] = useState({ item_name: '', monthly_cost: 1500, onetime_cost: 40000 });
  const [subResult, setSubResult] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pinnedId, setPinnedId] = useState(null);
  const upd = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const updSub = (k, v) => setSubForm((f) => ({ ...f, [k]: v }));

  const run = async () => {
    if (isDemo) {
      setError('Sign up for a free account to run AI-powered scenarios on your own finances.');
      return;
    }
    if (!form.item_name || !form.amount || Number(form.amount) <= 0) {
      setError('Please enter an item and amount.'); return;
    }
    setBusy(true); setError(''); setResult(null); setPinnedId(null);
    try {
      const payload = {
        item_name: form.item_name.trim(),
        amount: Number(form.amount),
        category: form.category || null,
        recurring_monthly_cost: Number(form.recurring_monthly_cost) || null,
        purchase_date: form.purchase_date || null,
        notes: form.notes ? form.notes.trim() : null,
      };
      const { data } = await api.post('/whatif/scenario', payload);
      setResult(data);
    } catch (err) {
      const status = err?.response?.status;
      if (status === 401) setError('Please sign in to run a personalised simulation.');
      else setError(err?.response?.data?.detail || 'Could not run simulation. Please try again.');
    } finally { setBusy(false); }
  };

  const applyPlan = async (opt) => {
    if (isDemo) return;
    try {
      await api.post('/whatif/scenario/apply', {
        scenario_name: form.item_name,
        amount: Number(form.amount),
        option_label: opt.label,
        summary: `${opt.label} — ${opt.pros?.[0] || ''} ${opt.cons?.[0] || ''} Goal delay ~${opt.total_goal_delay_months} months.`,
      });
      setPinnedId(opt.id);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not pin this plan to memory. Please try again.');
    }
  };

  const runSubscription = async () => {
    if (isDemo) { setError('Sign up to compare recurring costs against a one-time buy.'); return; }
    if (!subForm.item_name || !subForm.monthly_cost || !subForm.onetime_cost) {
      setError('Fill in item, monthly cost and one-time cost.'); return;
    }
    setBusy(true); setError(''); setSubResult(null);
    try {
      const { data } = await api.post('/whatif/subscription', {
        item_name: subForm.item_name.trim(),
        monthly_cost: Number(subForm.monthly_cost),
        onetime_cost: Number(subForm.onetime_cost),
      });
      setSubResult(data);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not run comparison. Please try again.');
    } finally { setBusy(false); }
  };

  return (
    <div data-testid="whatif-page">
      <div className="page-intro">
        <div>
          <div className="eyebrow">Test your future money moves</div>
          <h2>What-If Simulator</h2>
          <p>Ask AI how a hypothetical purchase would affect your finances and goals — nothing is changed for real.</p>
        </div>
        <span className="ai-badge"><Sparkles size={14} /> Claude Sonnet 5</span>
      </div>

      <div className="settings-tabs" data-testid="whatif-mode-tabs">
        <button data-testid="whatif-tab-purchase" className={`settings-tab ${mode === 'purchase' ? 'active' : ''}`} onClick={() => { setMode('purchase'); setError(''); }}>Purchase scenario</button>
        <button data-testid="whatif-tab-subscription" className={`settings-tab ${mode === 'subscription' ? 'active' : ''}`} onClick={() => { setMode('subscription'); setError(''); }}>Subscription vs one-time</button>
      </div>

      {mode === 'purchase' && (<>
      <div className="wi-grid">
        <div className="wi-card wi-input-card" data-testid="whatif-input-card">
          <h3><Zap size={18} className="wi-icon-inline" /> Describe the scenario</h3>
          <div className="auth-field">
            <label>What do you want to buy?</label>
            <input data-testid="whatif-item-input" value={form.item_name} onChange={(e) => upd('item_name', e.target.value)} placeholder="e.g. Laptop, iPhone, bike, home renovation" />
          </div>
          <div className="wi-two-col">
            <div className="auth-field">
              <label>Amount (₹)</label>
              <input data-testid="whatif-amount-input" type="number" min="1" value={form.amount} onChange={(e) => upd('amount', e.target.value)} />
            </div>
            <div className="auth-field">
              <label>Category</label>
              <select data-testid="whatif-category-input" value={form.category} onChange={(e) => upd('category', e.target.value)} className="wi-select">
                {CATEGORY_OPTIONS.map((c) => <option key={c}>{c}</option>)}
              </select>
            </div>
          </div>
          <div className="wi-two-col">
            <div className="auth-field">
              <label>Recurring monthly cost (optional, ₹)</label>
              <input data-testid="whatif-recurring-input" type="number" min="0" value={form.recurring_monthly_cost} onChange={(e) => upd('recurring_monthly_cost', e.target.value)} placeholder="e.g. subscription, EMI" />
            </div>
            <div className="auth-field">
              <label>Purchase date (optional)</label>
              <input data-testid="whatif-date-input" type="date" value={form.purchase_date} onChange={(e) => upd('purchase_date', e.target.value)} />
            </div>
          </div>
          <div className="auth-field">
            <label>Notes / assumptions (optional)</label>
            <input data-testid="whatif-notes-input" value={form.notes} onChange={(e) => upd('notes', e.target.value)} placeholder="Any context for the AI" />
          </div>
          {error && <div className="auth-error" data-testid="whatif-error">{error}</div>}
          <button data-testid="whatif-run-button" className="primary-btn full" onClick={run} disabled={busy}>
            {busy ? (<><Loader2 size={16} className="wi-spin" /> Analyzing with AI…</>) : (<>Run simulation <ArrowRight size={16} /></>)}
          </button>
          <p className="wi-hint">Nothing here modifies your real goals, transactions, or balances.</p>
        </div>

        {result && (
          <div className="wi-card wi-summary-card" data-testid="whatif-summary-card">
            <h3>Your current snapshot</h3>
            <div className="wi-metric"><span>Monthly free cash</span><b>{money(result.user_snapshot.monthly_free_cash)}</b></div>
            <div className="wi-metric"><span>Current savings</span><b>{money(result.user_snapshot.current_savings)}</b></div>
            <div className="wi-metric"><span>Active goals</span><b>{result.user_snapshot.goal_count}</b></div>
            <div className="wi-metric"><span>Monthly expenses</span><b>{money(result.user_snapshot.monthly_expenses)}</b></div>
            {result.ai_available === false && (
              <div className="wi-warn"><AlertTriangle size={14} /> AI is temporarily unavailable — a rule-based recommendation is shown.</div>
            )}
          </div>
        )}
      </div>

      {result && (
        <div className="wi-options-grid" data-testid="whatif-options-grid">
          {result.options.map((opt) => (
            <ScenarioCard
              key={opt.id}
              opt={opt}
              isBest={opt.id === 'best'}
              pinned={pinnedId === opt.id}
              onApply={() => applyPlan(opt)}
              isDemo={isDemo}
            />
          ))}
        </div>
      )}

      {result && (<p className="wi-disclaimer">{result.disclaimer}</p>)}
      </>)}

      {mode === 'subscription' && (
        <>
          <div className="wi-grid">
            <div className="wi-card wi-input-card" data-testid="whatif-sub-input-card">
              <h3><Zap size={18} className="wi-icon-inline" /> Compare subscription vs one-time buy</h3>
              <div className="auth-field">
                <label>What are you deciding on?</label>
                <input data-testid="whatif-sub-item" value={subForm.item_name} onChange={(e) => updSub('item_name', e.target.value)} placeholder="e.g. Music streaming, gym, software" />
              </div>
              <div className="wi-two-col">
                <div className="auth-field">
                  <label>Monthly recurring cost (₹)</label>
                  <input data-testid="whatif-sub-monthly" type="number" min="1" value={subForm.monthly_cost} onChange={(e) => updSub('monthly_cost', e.target.value)} />
                </div>
                <div className="auth-field">
                  <label>One-time buy cost (₹)</label>
                  <input data-testid="whatif-sub-onetime" type="number" min="1" value={subForm.onetime_cost} onChange={(e) => updSub('onetime_cost', e.target.value)} />
                </div>
              </div>
              {error && <div className="auth-error" data-testid="whatif-sub-error">{error}</div>}
              <button data-testid="whatif-sub-run" className="primary-btn full" onClick={runSubscription} disabled={busy}>
                {busy ? (<><Loader2 size={16} className="wi-spin" /> Comparing…</>) : (<>Compare <ArrowRight size={16} /></>)}
              </button>
              <p className="wi-hint">Assumes 8% p.a. return on money not spent. Nothing here modifies your real data.</p>
            </div>

            {subResult && (
              <div className="wi-card" data-testid="whatif-sub-verdict-card">
                <h3><Sparkles size={18} className="wi-icon-inline" /> Verdict</h3>
                <p className="wi-ai-reco" data-testid="whatif-sub-recommendation">{subResult.recommendation}</p>
                {subResult.breakeven_months !== null && (
                  <div className="wi-metric"><span>Break-even</span><b>{subResult.breakeven_months} months</b></div>
                )}
              </div>
            )}
          </div>

          {subResult && (
            <div className="wi-options-grid" data-testid="whatif-sub-results">
              <div className="wi-option" data-testid="whatif-sub-option-subscription">
                <div className="wi-option-head"><h3>If you subscribe</h3></div>
                <div className="wi-stats">
                  <Stat label="Total paid in 5 yrs" value={money(subResult.subscription.total_paid_5y)} bad />
                  <Stat label="Total paid in 10 yrs" value={money(subResult.subscription.total_paid_10y)} bad />
                  <Stat label="If invested (5 yrs)" value={money(subResult.subscription.opportunity_cost_5y)} />
                  <Stat label="If invested (10 yrs)" value={money(subResult.subscription.opportunity_cost_10y)} />
                </div>
                <p className="wi-pc" style={{ fontSize: 12 }}>The invested column shows how much wealth you'd build if you kept the money instead.</p>
              </div>
              <div className="wi-option wi-best" data-testid="whatif-sub-option-onetime">
                <div className="wi-option-head">
                  <span className="wi-best-badge"><Star size={12} /> If you buy once</span>
                  <h3>Own it outright</h3>
                </div>
                <div className="wi-stats">
                  <Stat label="Upfront cost" value={money(subResult.onetime.cost)} />
                  <Stat label="Opportunity cost 5 yrs" value={money(subResult.onetime.future_value_5y)} />
                  <Stat label="Opportunity cost 10 yrs" value={money(subResult.onetime.future_value_10y)} />
                  <Stat label="Return assumed" value={`${subResult.annual_return_assumed}%`} />
                </div>
                <div className="wi-ai-note"><Sparkles size={12} /> {subResult.recommendation}</div>
              </div>
            </div>
          )}
          {subResult && <p className="wi-disclaimer">{subResult.disclaimer}</p>}
        </>
      )}
    </div>
  );
}

function ScenarioCard({ opt, isBest, pinned, onApply, isDemo }) {
  return (
    <div className={`wi-option ${isBest ? 'wi-best' : ''}`} data-testid={`whatif-option-${opt.id}`}>
      <div className="wi-option-head">
        {isBest && <span className="wi-best-badge"><Star size={12} /> AI recommendation</span>}
        <h3>{opt.label}</h3>
        {opt.months_delay > 0 && !isBest && <span className="wi-delay">Wait {opt.months_delay} months</span>}
      </div>

      {isBest && opt.ai_recommendation && (
        <p className="wi-ai-reco" data-testid="whatif-ai-reco">{opt.ai_recommendation}</p>
      )}

      <div className="wi-stats">
        <Stat label="Cash after purchase" value={money(opt.remaining_cash_after_purchase)} bad={opt.remaining_cash_after_purchase < 0} />
        <Stat label="Goals delay" value={`~${opt.total_goal_delay_months} mo`} icon={<TrendingDown size={13} />} bad={opt.total_goal_delay_months > 6} />
        <Stat label="Health impact" value={`${opt.health_score_delta >= 0 ? '+' : ''}${opt.health_score_delta}`} good={opt.health_score_delta > 0} bad={opt.health_score_delta < 0} />
        {opt.recurring_monthly_cost > 0 && (
          <Stat label="Recurring (yr)" value={money(opt.total_recurring_first_year)} bad />
        )}
      </div>

      {opt.dips_into_savings && (
        <div className="wi-warn small"><AlertTriangle size={12} /> Uses savings reserve</div>
      )}

      {opt.goal_impacts?.length > 0 && (
        <div className="wi-goals">
          <div className="wi-goals-title"><Wallet size={13} /> Affected goals</div>
          <ul>
            {opt.goal_impacts.slice(0, 3).map((g) => (
              <li key={g.goal_id}>
                <span>{g.goal_name}</span>
                <span className={`priority ${(g.priority || 'medium').toLowerCase()}`}>+{g.months_delay}mo</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="wi-proscons">
        <div className="wi-pros">
          <div className="wi-pc-title"><Check size={12} /> Pros</div>
          {(opt.pros || []).map((p, i) => <div key={i} className="wi-pc">{p}</div>)}
        </div>
        <div className="wi-cons">
          <div className="wi-pc-title"><X size={12} /> Cons</div>
          {(opt.cons || []).map((c, i) => <div key={i} className="wi-pc">{c}</div>)}
        </div>
      </div>

      {opt.ai_note && !isBest && (
        <div className="wi-ai-note"><Sparkles size={12} /> {opt.ai_note}</div>
      )}

      {isBest && opt.ai_reasoning && (
        <div className="wi-ai-reasoning"><Sparkles size={12} /> {opt.ai_reasoning}</div>
      )}

      {!isDemo && !pinned && (
        <button
          className={isBest ? 'primary-btn full' : 'pill-btn full mint'}
          data-testid={`whatif-apply-${opt.id}`}
          onClick={onApply}
        >
          Apply this plan
        </button>
      )}
      {pinned && (
        <div className="wi-pinned" data-testid={`whatif-pinned-${opt.id}`}>
          <Check size={14} /> Pinned to AI memory
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, good, bad, icon }) {
  return (
    <div className={`wi-stat ${good ? 'good' : ''} ${bad ? 'bad' : ''}`}>
      <span className="wi-stat-l">{label}</span>
      <span className="wi-stat-v">{icon}{value}</span>
    </div>
  );
}
