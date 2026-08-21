import { useEffect, useMemo, useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { GoogleOAuthProvider } from '@react-oauth/google';
import { AreaChart, Area, BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, Legend } from 'recharts';
import { LayoutDashboard, Wallet, FileText, TrendingUp, Target, Activity, BookOpen, Sparkles, ShieldCheck, Bell, ChevronRight, Upload, Plus, ArrowUpRight, ArrowDownRight, Send, Check, LockKeyhole, Trash2, CircleHelp, LogOut, Lock, KeyRound, Fingerprint, User, Zap, Edit3, Mic, MicOff, Volume2, Moon, Sun, X, Brain } from 'lucide-react';
import '@/App.css';
import './auth.css';
import { AuthProvider, useAuth } from './lib/auth';
import { api, API, formatApiError } from './lib/api';
import { registerPasskey, listPasskeys, removePasskey, passkeysSupported } from './lib/passkey';
import { useVoice } from './lib/useVoice';
import Login from './pages/Login';
import Signup from './pages/Signup';
import ForgotPassword from './pages/ForgotPassword';
import ResetPassword from './pages/ResetPassword';
import VerifyEmail from './pages/VerifyEmail';
import Onboarding from './pages/Onboarding';
import PinLock from './pages/PinLock';
import StatementUpload from './pages/StatementUpload';
import Profile from './pages/Profile';
import WhatIf from './pages/WhatIf';
import ArticleDetail from './pages/ArticleDetail';

const nav = [
  {label:'Dashboard',path:'/',icon:LayoutDashboard},
  {label:'My Finances',path:'/finances',icon:Wallet},
  {label:'Statements',path:'/statements',icon:FileText},
  {label:'Six-Month Analysis',path:'/analysis',icon:TrendingUp},
  {label:'Goals & Priorities',path:'/goals',icon:Target},
  {label:'What-If Simulator',path:'/whatif',icon:Zap},
  {label:'Financial Changes',path:'/changes',icon:Activity},
  {label:'FINAURA Learn',path:'/learn',icon:BookOpen},
  {label:'Ask FINAURA AI',path:'/ask',icon:Sparkles},
  {label:'Profile',path:'/profile',icon:User},
  {label:'Settings & Privacy',path:'/settings',icon:ShieldCheck},
];
const money = (n) => `₹${Number(n || 0).toLocaleString('en-IN')}`;
const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);
const initials = (n) => (n || 'FN').split(/\s+/).map((p) => p[0]).slice(0,2).join('').toUpperCase();

function Shell({ children, mode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout, lock } = useAuth();
  const isDemo = mode === 'demo';
  const active = nav.find((n) => (isDemo ? `/demo${n.path === '/' ? '' : n.path}` : n.path) === location.pathname) || nav[0];
  const displayName = isDemo ? 'Aarav Sharma' : (user?.name || 'FINAURA AI user');
  const displayInitials = isDemo ? 'AS' : initials(displayName);
  const goToNav = (path) => navigate(isDemo ? `/demo${path === '/' ? '' : path}` : path);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand" data-testid="sidebar-brand"><span className="brand-mark">f</span><span>FINAURA <b>AI</b></span></div>
        <div className="demo-pill" data-testid="workspace-mode-pill">
          <span></span> {isDemo ? 'Demo mode · Read only' : (user?.has_demo_data ? 'Demo data loaded' : 'Your data')}
        </div>
        <nav>
          {nav.map(({ label, path, icon: Icon }) => {
            const target = isDemo ? `/demo${path === '/' ? '' : path}` : path;
            return (
              <button
                key={path}
                data-testid={`nav-${label.toLowerCase().replaceAll(' ', '-').replaceAll('&','and')}`}
                className={active.path === path ? 'nav-item active' : 'nav-item'}
                onClick={() => navigate(target)}
              >
                <Icon size={18} /><span>{label}</span>
                {active.path === path && <ChevronRight size={14} className="nav-arrow" />}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          <div className="privacy-mini">
            <LockKeyhole size={16} />
            <div><strong>Your data, yours.</strong><small>Prototype privacy controls active</small></div>
          </div>
          <div className="profile">
            <div className="avatar" data-testid="sidebar-avatar">{displayInitials}</div>
            <div><strong>{displayName}</strong><small>{isDemo ? 'Demo profile' : user?.email}</small></div>
            {!isDemo && user?.has_pin && (
              <button className="icon-btn" title="Lock now" onClick={() => { lock(); navigate('/lock'); }} data-testid="lock-now-button" style={{ marginLeft: 'auto' }}>
                <Lock size={15} />
              </button>
            )}
          </div>
        </div>
      </aside>
      <main className="main">
        {isDemo && (
          <div className="demo-topbanner" data-testid="demo-topbanner">
            <div><strong>You're exploring the FINAURA AI demo profile.</strong> Sign up to save your own goals and data.</div>
            <div className="actions">
              <button className="ghost" data-testid="demo-signin-button" onClick={() => navigate('/login')}>Sign in</button>
              <button data-testid="demo-signup-button" onClick={() => navigate('/signup')}>Create account</button>
            </div>
          </div>
        )}
        <header className="topbar">
          <div><span className="eyebrow">{active.label}</span><h1 data-testid="page-title">{active.label}</h1></div>
          <div className="top-actions">
            <div className="status"><span></span> Updated just now</div>
            <button data-testid="notifications-button" className="icon-btn"><Bell size={19} /></button>
            {!isDemo && (
              <button className="icon-btn" title="Sign out" onClick={() => { logout(); navigate('/login'); }} data-testid="sign-out-button">
                <LogOut size={17} />
              </button>
            )}
            <div className="avatar small">{displayInitials}</div>
          </div>
        </header>
        {children}
      </main>
    </div>
  );
}

function Card({ children, className = '', ...rest }) { return <section className={`card ${className}`} {...rest}>{children}</section>; }
function SectionTitle({ eyebrow, title, action }) {
  return <div className="section-title"><div><div className="eyebrow">{eyebrow}</div><h2>{title}</h2></div>{action}</div>;
}

function DemoGate({ children, onSignup }) {
  return <>{children}</>;
}

function Dashboard({ data, isDemo }) {
  const s = data.summary;
  const navigate = useNavigate();
  const today = useMemo(() => new Date().toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }), []);
  return (
    <>
      <div className="welcome">
        <div>
          <div className="eyebrow">{today}</div>
          <h2>Good morning, {(isDemo ? 'Aarav' : data.user.name.split(' ')[0])}<span className="mint">.</span></h2>
          <p>Here's what's happening with your financial life.</p>
        </div>
        <button data-testid="dashboard-add-data-button" className="primary-btn" onClick={() => navigate(isDemo ? '/demo/statements' : '/statements')}>
          <Plus size={17} /> {isDemo ? 'Explore statements' : 'Add financial data'}
        </button>
      </div>
      <div className="metric-grid">
        {[['Monthly income', s.income, 'positive', 'vs. last month'],
          ['Monthly expenses', s.expenses, 'negative', 'vs. last month'],
          ['Monthly savings', s.savings, 'positive', `${pct(s.savings, s.income || 1)}% savings rate`],
          ['Estimated net worth', s.net_worth, 'neutral', 'across all accounts']].map(([label, value, tone, sub], i) => (
          <Card key={label} className="metric-card">
            <div className="metric-top"><span>{label}</span><span className={`metric-icon ${tone}`}>{i === 1 ? <ArrowDownRight size={17} /> : <ArrowUpRight size={17} />}</span></div>
            <strong data-testid={`metric-${i}`}>{money(value)}</strong><small className={tone === 'negative' ? 'down' : ''}>{sub}</small>
          </Card>
        ))}
      </div>
      <div className="dashboard-grid">
        <Card className="trend-card">
          <SectionTitle eyebrow="Cash flow" title="Your financial rhythm" action={<span className="legend"><i></i> Income <b></b> Expenses</span>} />
          <ResponsiveContainer width="100%" height={235}>
            <AreaChart data={data.history}>
              <defs><linearGradient id="income" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#10b981" stopOpacity=".25" /><stop offset="100%" stopColor="#10b981" stopOpacity="0" /></linearGradient></defs>
              <CartesianGrid stroke="#edf1ef" vertical={false} />
              <XAxis dataKey="month" axisLine={false} tickLine={false} tick={{ fill: '#94a3b8', fontSize: 12 }} />
              <YAxis hide />
              <Tooltip formatter={(v) => money(v)} contentStyle={{ border: '1px solid #e2e8f0', borderRadius: 8 }} />
              <Area type="monotone" dataKey="income" stroke="#10b981" fill="url(#income)" strokeWidth={2.5} />
              <Area type="monotone" dataKey="expenses" stroke="#f59e0b" fill="none" strokeWidth={2.5} strokeDasharray="5 5" />
            </AreaChart>
          </ResponsiveContainer>
        </Card>
        <Card className="health-card">
          <SectionTitle eyebrow="Financial health" title="A clear picture" />
          <div className="health-score">
            <div className="score-ring"><strong data-testid="health-score">{s.health_score}</strong><span>/ 100</span></div>
            <div><strong>{s.health_score >= 70 ? 'Looking good' : s.health_score >= 40 ? 'Room to grow' : 'Let\'s build it up'}</strong>
              <p>{s.health_score >= 70 ? 'Your savings habits are doing the heavy lifting.' : 'Add more data to sharpen this picture.'}</p></div>
          </div>
          <div className="health-bars">
            {[['Savings', Math.min(100, pct(s.savings, s.income || 1) * 2)],
              ['Spending', Math.max(0, 100 - pct(s.expenses, s.income || 1))],
              ['Debt', Math.max(0, 100 - pct(s.debt, s.current_savings + s.investments || 1))],
              ['Goal progress', s.health_score]].map(([x, v]) => (
              <div key={x}><div><span>{x}</span><b>{v}</b></div><div className="bar"><i style={{ width: `${v}%` }}></i></div></div>
            ))}
          </div>
        </Card>
      </div>
      <div className="dashboard-grid lower">
        <Card>
          <SectionTitle eyebrow="Goals" title="Your north stars" action={<button data-testid="view-goals-button" className="text-btn" onClick={() => navigate(isDemo ? '/demo/goals' : '/goals')}>View all <ChevronRight size={15} /></button>} />
          {data.goals.length === 0 && <p style={{ fontSize: 13, color: '#8b9995' }}>No goals yet — head to Goals to add your first.</p>}
          {data.goals.slice(0, 3).map((g) => <GoalRow goal={g} key={g.id} />)}
        </Card>
        <Card>
          <SectionTitle eyebrow="Finaura insight" title="Worth noticing" />
          <div className="insight">
            <div className="insight-mark">✦</div>
            <div>
              <strong>{data.transactions.length ? 'Your savings are back on track' : 'Add your first transaction'}</strong>
              <p>{data.transactions.length ? <>After dipping in June, you saved <b>{money(s.savings)}</b> this month.</> : 'FINAURA AI will surface trends here as soon as you add income or expenses.'}</p>
            </div>
          </div>
        </Card>
      </div>
    </>
  );
}
function GoalRow({ goal }) {
  return (
    <div className="goal-row">
      <span className="goal-emoji">{goal.emoji || '✦'}</span>
      <div className="goal-info">
        <div><strong>{goal.name}</strong><span className={`priority ${goal.priority.toLowerCase()}`}>{goal.priority}</span></div>
        <div className="goal-progress"><span style={{ width: `${pct(goal.current_amount, goal.target_amount)}%` }}></span></div>
        <small>{money(goal.current_amount)} of {money(goal.target_amount)} <b>{pct(goal.current_amount, goal.target_amount)}%</b></small>
      </div>
    </div>
  );
}

function Finances({ data }) {
  return (
    <>
      <div className="page-intro">
        <div><div className="eyebrow">The full picture</div><h2>My finances</h2><p>Everything you've told FINAURA AI, organized in one place.</p></div>
        <span className="data-note"><LockKeyhole size={15} /> Private to your profile</span>
      </div>
      <div className="finance-grid">
        {[['Cash savings', data.summary.current_savings, 'Liquid reserves'],
          ['Investments', data.summary.investments, 'Long-term growth'],
          ['Loans & debt', data.summary.debt, 'Outstanding principal'],
          ['Monthly EMI', data.summary.emi, 'Fixed obligation']].map(([a, b, c]) => (
          <Card className="finance-stat" key={a}><small>{a}</small><strong>{money(b)}</strong><span>{c}</span></Card>
        ))}
      </div>
      <Card>
        <SectionTitle eyebrow="Monthly allocation" title="Where your money goes" />
        {data.spending.length ? (
          <div className="allocation">
            <ResponsiveContainer width="46%" height={230}>
              <PieChart><Pie data={data.spending} dataKey="value" innerRadius={70} outerRadius={95} paddingAngle={3}>
                {data.spending.map((x) => <Cell key={x.name} fill={x.color} />)}
              </Pie><Tooltip formatter={(v) => money(v)} /></PieChart>
            </ResponsiveContainer>
            <div className="allocation-list">
              {data.spending.map((x) => {
                const total = data.spending.reduce((a, b) => a + b.value, 0) || 1;
                return (
                  <div className="allocation-line" key={x.name}>
                    <i style={{ background: x.color }}></i><span>{x.name}</span><b>{money(x.value)}</b><small>{Math.round((x.value / total) * 100)}%</small>
                  </div>
                );
              })}
            </div>
          </div>
        ) : (
          <p style={{ padding: '30px 0', color: '#8b9995', fontSize: 13 }}>No spending data yet. Add transactions from the Statements page.</p>
        )}
      </Card>
    </>
  );
}

function Statements({ data, isDemo, reload }) {
  const [uploaded, setUploaded] = useState(false);
  const [txns, setTxns] = useState(data.transactions);
  const navigate = useNavigate();
  useEffect(() => setTxns(data.transactions), [data.transactions]);
  const update = async (id, category) => {
    if (isDemo) { navigate('/signup'); return; }
    setTxns((prev) => prev.map((t) => (t.id === id ? { ...t, category } : t)));
    try { await api.patch(`/transactions/${id}`, { category }); } catch {}
  };
  const importDemo = async () => {
    if (isDemo) { setUploaded(true); return; }
    try { await api.post('/statements/import-demo'); setUploaded(true); await reload?.(); } catch {}
  };
  return (
    <>
      <div className="page-intro">
        <div><div className="eyebrow">Enter · Understand · Organize</div><h2>Statements</h2><p>No bank account connection required. Bring your history, your way.</p></div>
        <button data-testid="demo-shortcut-button" className="outline-btn" onClick={importDemo}><Plus size={17} /> {isDemo ? 'View demo' : 'Load six-month demo'}</button>
      </div>
      {uploaded && <div className="success-banner" data-testid="statement-upload-success"><Check size={18} /> Demo statement imported — review the categorization before confirming.</div>}
      {!isDemo && (
        <Card className="statement-upload-card">
          <SectionTitle eyebrow="Import your own data" title="Upload a bank statement" />
          <StatementUpload onImported={() => reload?.()} />
        </Card>
      )}
      <Card>
        <SectionTitle eyebrow={`${txns.length} transactions`} title="Review your imported data" action={<span className="review-pill"><span></span> Needs your review</span>} />
        <div className="table-wrap">
          <table>
            <thead><tr><th>Date</th><th>Description</th><th>Type</th><th>Category</th><th>Amount</th></tr></thead>
            <tbody>
              {txns.length === 0 && <tr><td colSpan={5} style={{ textAlign: 'center', color: '#8b9995', padding: '30px 0' }}>No transactions yet. Upload a statement or load the demo above to get started.</td></tr>}
              {txns.map((t) => (
                <tr key={t.id}>
                  <td>{t.date}</td>
                  <td><strong>{t.description}</strong></td>
                  <td><span className={t.type === 'Income' ? 'income-tag' : 'expense-tag'}>{t.type}</span></td>
                  <td>
                    <select data-testid={`transaction-category-${t.id}`} value={t.category} onChange={(e) => update(t.id, e.target.value)} disabled={isDemo}>
                      {['Income', 'Food', 'Shopping', 'Transport', 'Rent', 'Bills', 'Education', 'Entertainment', 'Healthcare', 'Other'].map((c) => <option key={c}>{c}</option>)}
                    </select>
                  </td>
                  <td className={t.type === 'Income' ? 'amount-income' : ''}>{t.type === 'Income' ? '+' : '−'}{money(t.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

function Analysis({ data }) {
  return (
    <>
      <div className="page-intro">
        <div><div className="eyebrow">Patterns over time</div><h2>Six-month analysis</h2><p>Small shifts become clear when you step back.</p></div>
        <span className="period-selector">Mar — Aug 2026 ▾</span>
      </div>
      <Card className="analysis-chart">
        <SectionTitle eyebrow="Income vs expenses" title="The gap is your resilience" />
        <ResponsiveContainer width="100%" height={270}>
          <LineChart data={data.history} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
            <CartesianGrid stroke="#edf1ef" vertical={false} />
            <XAxis dataKey="month" axisLine={false} tickLine={false} tick={{ fill: '#94a3b8', fontSize: 12 }} />
            <YAxis tickFormatter={(v) => `₹${Math.round(v/1000)}k`} axisLine={false} tickLine={false} tick={{ fill: '#94a3b8', fontSize: 11 }} />
            <Tooltip formatter={(v) => money(v)} contentStyle={{ border: '1px solid #e2e8f0', borderRadius: 8 }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Line type="monotone" dataKey="income" stroke="#10b981" strokeWidth={2.5} dot={{ r: 3 }} name="Income" />
            <Line type="monotone" dataKey="expenses" stroke="#f59e0b" strokeWidth={2.5} dot={{ r: 3 }} name="Expenses" />
            <Line type="monotone" dataKey="savings" stroke="#0f172a" strokeWidth={2.5} dot={{ r: 3 }} strokeDasharray="4 4" name="Savings" />
          </LineChart>
        </ResponsiveContainer>
      </Card>
      <Card>
        <SectionTitle eyebrow="Monthly breakdown" title="Your six-month record" />
        <div className="table-wrap">
          <table>
            <thead><tr><th>Month</th><th>Income</th><th>Expenses</th><th>Savings</th><th>Savings rate</th></tr></thead>
            <tbody>
              {data.history.map((m) => (
                <tr key={m.month}>
                  <td><strong>{m.month} 2026</strong></td>
                  <td>{money(m.income)}</td>
                  <td>{money(m.expenses)}</td>
                  <td className="amount-income">{money(m.savings)}</td>
                  <td><span className="rate-chip">{m.savings_rate}%</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

function Goals({ data, isDemo, reload }) {
  const navigate = useNavigate();
  const [goals, setGoals] = useState(data.goals);
  const [show, setShow] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState({ name: '', target_amount: 500000, current_amount: 0, deadline: '2030', priority: 'Medium', monthly_contribution: 10000, emoji: '✦' });
  const [error, setError] = useState('');
  useEffect(() => setGoals(data.goals), [data.goals]);
  const upd = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const openNew = () => { setEditingId(null); setForm({ name: '', target_amount: 500000, current_amount: 0, deadline: '2030', priority: 'Medium', monthly_contribution: 10000, emoji: '✦' }); setShow(true); };
  const openEdit = (g) => { setEditingId(g.id); setForm({ name: g.name, target_amount: g.target_amount, current_amount: g.current_amount, deadline: g.deadline, priority: g.priority, monthly_contribution: g.monthly_contribution || 0, emoji: g.emoji || '✦' }); setShow(true); };
  const save = async () => {
    if (!form.name) return;
    if (isDemo) { navigate('/signup'); return; }
    const g = { ...form, target_amount: Number(form.target_amount) || 0, current_amount: Number(form.current_amount) || 0, monthly_contribution: Number(form.monthly_contribution) || 0 };
    try {
      if (editingId) await api.patch(`/goals/${editingId}`, g); else await api.post('/goals', g);
      setForm({ name: '', target_amount: 500000, current_amount: 0, deadline: '2030', priority: 'Medium', monthly_contribution: 10000, emoji: '✦' });
      setError(''); setShow(false); setEditingId(null);
      await reload?.();
    } catch (e) { setError("We couldn't save that goal. Please try again."); }
  };
  const remove = async (g) => {
    if (isDemo) { navigate('/signup'); return; }
    if (!window.confirm(`Delete goal "${g.name}"? This cannot be undone.`)) return;
    try { await api.delete(`/goals/${g.id}`); await reload?.(); } catch {}
  };
  return (
    <>
      <div className="page-intro">
        <div><div className="eyebrow">Prioritize what matters</div><h2>Goals & priorities</h2><p>Give every rupee a purpose, then choose what comes first.</p></div>
        <button data-testid="create-goal-button" className="primary-btn" onClick={openNew}><Plus size={17} /> New goal</button>
      </div>
      {goals.length === 0 && <p style={{ padding: '30px 0', color: '#8b9995' }}>No goals yet. Create your first goal to see it here.</p>}
      <div className="goals-grid">
        {goals.map((g) => (
          <Card className={`goal-card ${g.priority.toLowerCase()}`} key={g.id} data-testid={`goal-card-${g.id}`}>
            <div className="goal-card-top">
              <span className="goal-emoji large">{g.emoji || '✦'}</span>
              <span className={`priority ${g.priority.toLowerCase()}`}>{g.priority} priority</span>
              {!isDemo && (
                <div className="goal-actions">
                  <button className="icon-btn" data-testid={`edit-goal-${g.id}`} onClick={() => openEdit(g)} title="Edit"><Edit3 size={14}/></button>
                  <button className="icon-btn" data-testid={`delete-goal-${g.id}`} onClick={() => remove(g)} title="Delete"><Trash2 size={14}/></button>
                </div>
              )}
            </div>
            <h3 data-testid={`goal-card-name-${g.id}`}>{g.name}</h3>
            <div className="goal-big-amount" data-testid={`goal-card-amount-${g.id}`}>{money(g.current_amount)} <small>/ {money(g.target_amount)}</small></div>
            <div className="goal-progress thick"><span style={{ width: `${pct(g.current_amount, g.target_amount)}%` }}></span></div>
            <div className="goal-meta"><span>{pct(g.current_amount, g.target_amount)}% complete</span><span>Due {g.deadline}</span></div>
            <div className="goal-requirement"><span>Monthly contribution</span><b>{money(g.monthly_contribution)} / mo</b></div>
          </Card>
        ))}
      </div>
      {show && (
        <div className="modal-backdrop">
          <div className="modal">
            <button data-testid="close-goal-modal-button" className="modal-close" onClick={() => setShow(false)}>×</button>
            <div className="eyebrow">{editingId ? 'Edit goal' : 'New goal'}</div><h3>{editingId ? 'Update the details' : 'What are you working toward?'}</h3>
            <div className="auth-field"><label>Goal name</label>
              <input data-testid="goal-name-input" value={form.name} onChange={(e) => upd('name', e.target.value)} placeholder="e.g. New home, sabbatical" /></div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <div className="auth-field"><label>Target amount (₹)</label>
                <input data-testid="goal-target-input" type="number" min="0" value={form.target_amount} onChange={(e) => upd('target_amount', e.target.value)} /></div>
              <div className="auth-field"><label>Saved so far (₹)</label>
                <input data-testid="goal-current-input" type="number" min="0" value={form.current_amount} onChange={(e) => upd('current_amount', e.target.value)} /></div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <div className="auth-field"><label>Target year</label>
                <input data-testid="goal-deadline-input" value={form.deadline} onChange={(e) => upd('deadline', e.target.value)} placeholder="2030" /></div>
              <div className="auth-field"><label>Priority</label>
                <select data-testid="goal-priority-input" value={form.priority} onChange={(e) => upd('priority', e.target.value)} style={{ width: '100%', padding: 11, borderRadius: 8, border: '1px solid #d9e4df', background: '#fbfdfc', fontFamily: 'inherit', fontSize: 13 }}>
                  <option>High</option><option>Medium</option><option>Low</option>
                </select></div>
            </div>
            <div className="auth-field"><label>Monthly contribution (₹)</label>
              <input data-testid="goal-monthly-input" type="number" min="0" value={form.monthly_contribution} onChange={(e) => upd('monthly_contribution', e.target.value)} /></div>
            <button data-testid="save-goal-button" className="primary-btn full" onClick={save}>{isDemo ? 'Sign up to save' : (editingId ? 'Update goal' : 'Save goal')}</button>
            {error && <div className="error-message" data-testid="goal-save-error">{error}</div>}
          </div>
        </div>
      )}
    </>
  );
}

function Changes({ data }) {
  return (
    <>
      <div className="page-intro">
        <div><div className="eyebrow">What changed?</div><h2>Financial changes</h2><p>Meaningful signals from your financial life, not just numbers.</p></div>
        <span className="signal-summary"><i></i> 2 positive · 1 to watch</span>
      </div>
      <div className="alert-card">
        <div className="alert-symbol">↘</div>
        <div>
          <span className="eyebrow">Savings alert · Significant</span>
          <h3>Your savings dipped, then recovered</h3>
          <p>Monthly savings decreased by approximately ₹17,000 between April and June, before recovering to ₹62,000 in August.</p>
        </div>
        <button data-testid="change-insight-button" className="text-btn">Explore signal <ChevronRight size={15} /></button>
      </div>
      <Card className="timeline-card">
        <SectionTitle eyebrow="Your story · March to August" title="The moments that shaped your money" />
        <div className="timeline">
          {[['MAR', 'Stable financial condition', 'Savings rate held at 41%', 'positive'],
            ['APR', 'Food spending increased', 'Discretionary spending +9%', 'moderate'],
            ['MAY', 'Income increased', 'Salary moved up by ₹5,000', 'positive'],
            ['JUN', 'Savings rate decreased', 'Expenses reached ₹1,26,000', 'negative'],
            ['JUL', 'Shopping became a pattern', 'Shopping up 18% from average', 'negative'],
            ['AUG', 'Back on track', 'Savings recovered to ₹62,000', 'positive']].map((m, i) => (
            <div className={`timeline-item ${i === 5 ? 'current' : ''}`} key={m[0]}>
              <div className={`timeline-dot ${m[3]}`}></div>
              <div className="timeline-date">{m[0]}</div>
              <div><strong>{m[1]}</strong><p>{m[2]}</p></div>
              {i < 5 && <div className="timeline-line" />}
            </div>
          ))}
        </div>
      </Card>
    </>
  );
}

function Learn({ isDemo }) {
  const navigate = useNavigate();
  const [articles, setArticles] = useState([]);
  const [daily, setDaily] = useState(null);
  useEffect(() => {
    api.get('/learn/articles').then((r) => setArticles(r.data.articles || [])).catch(() => {});
    api.get('/learn/daily').then((r) => setDaily(r.data)).catch(() => {});
  }, []);
  const openArticle = (id) => navigate(isDemo ? `/demo/learn/${id}` : `/learn/${id}`);
  const featured = articles[0];
  return (
    <>
      <div className="page-intro">
        <div><div className="eyebrow">Knowledge, made relevant</div><h2>FINAURA Learn</h2><p>Financial education shaped around your goals — with an Indian lens.</p></div>
        <span className="learn-count">{articles.length} recommendations for you</span>
      </div>
      {daily && (
        <Card className="daily-learn-card" data-testid="daily-learn-card">
          <div className="daily-badge">{daily.kind}</div>
          <div>
            <h3 data-testid="daily-learn-text">{daily.text}</h3>
            <small>{daily.date} · Tip {daily.index + 1} of {daily.of_total}</small>
          </div>
        </Card>
      )}
      {featured && (
        <div className="learn-feature" onClick={() => openArticle(featured.id)} data-testid="learn-feature" style={{cursor:'pointer'}}>
          <div>
            <div className="eyebrow">Recommended for you · 01</div>
            <h2>{featured.title}</h2>
            <p>{featured.why_relevant}</p>
            <button data-testid="learn-feature-button" className="light-btn">Read the guide <ArrowUpRight size={16} /></button>
          </div>
          <div className="feature-number">01</div>
        </div>
      )}
      <div className="learn-grid">
        {articles.slice(1).map((c) => (
          <Card className="learn-card" key={c.id} onClick={() => openArticle(c.id)} data-testid={`learn-card-${c.id}`} style={{cursor:'pointer'}}>
            <div className={`learn-art ${c.art_variant || 'mint'}`}>
              <span>{c.art_variant === 'mint' ? '◒' : c.art_variant === 'dark' ? '◌' : c.art_variant === 'yellow' ? '↗' : '≈'}</span>
            </div>
            <div className="eyebrow">{c.category}</div><h3>{c.title}</h3><p>{c.why_relevant}</p>
            <div className="learn-footer"><span>{c.read_minutes} min read</span><ArrowUpRight size={16} /></div>
          </Card>
        ))}
      </div>
    </>
  );
}

function Ask({ isDemo, userName }) {
  const [message, setMessage] = useState('');
  const [messages, setMessages] = useState([]); // [{role:'user'|'assistant', text, model?}]
  const [loading, setLoading] = useState(false);
  const [model, setModel] = useState(() => localStorage.getItem('finaura_chat_model') || 'openai');
  const [availableModels, setAvailableModels] = useState([
    { id: 'openai', label: 'OpenAI GPT-5.4', provider: 'openai' },
    { id: 'claude', label: 'Claude Sonnet 5', provider: 'anthropic' },
  ]);
  const voice = useVoice();
  const [voiceMode, setVoiceMode] = useState(() => localStorage.getItem('finaura_voice_reply') === '1');

  useEffect(() => {
    api.get('/chat/models').then((r) => setAvailableModels(r.data.models)).catch(() => {});
    // Pull any prompt seeded by a Learn article
    const seed = sessionStorage.getItem('finaura_learn_prompt');
    if (seed) { sessionStorage.removeItem('finaura_learn_prompt'); setMessage(seed); }
  }, []);
  useEffect(() => { if (voice.transcript) setMessage(voice.transcript); }, [voice.transcript]);
  const activeModelLabel = availableModels.find((m) => m.id === model)?.label || model;
  const chooseModel = (id) => { setModel(id); localStorage.setItem('finaura_chat_model', id); };
  const toggleVoiceReply = () => { const next = !voiceMode; setVoiceMode(next); localStorage.setItem('finaura_voice_reply', next ? '1' : '0'); if (!next) voice.stopSpeaking(); };

  const ask = async (q = message) => {
    if (!q || loading) return;
    voice.stop();
    setMessage('');
    setMessages((m) => [...m, { role: 'user', text: q }, { role: 'assistant', text: '', model }]);
    setLoading(true);
    let fullReply = '';
    try {
      const token = localStorage.getItem('finaura_token');
      const r = await fetch(`${API}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ message: q, model }),
      });
      if (!r.ok) throw new Error('Chat unavailable');
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = dec.decode(value);
        fullReply += chunk;
        setMessages((m) => {
          const copy = [...m];
          copy[copy.length - 1] = { ...copy[copy.length - 1], text: (copy[copy.length - 1].text || '') + chunk };
          return copy;
        });
      }
    } catch {
      fullReply = "I'm unable to reach the assistant right now. Your data is still safely stored.";
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = { ...copy[copy.length - 1], text: fullReply };
        return copy;
      });
    }
    setLoading(false);
    if (voiceMode && fullReply) voice.speak(fullReply);
  };
  return (
    <>
      <div className="page-intro">
        <div><div className="eyebrow">A second opinion, grounded in your data</div><h2>Ask FINAURA <span className="mint">AI</span></h2><p>Ask questions in plain language. Get clear, educational answers.</p></div>
        <span className="ai-badge"><Sparkles size={14} /> Context-aware AI</span>
      </div>
      <div className="ask-layout">
        <Card className="chat-card">
          <div className="chat-head">
            <div className="ai-avatar"><Sparkles size={19} /></div>
            <div>
              <strong>FINAURA intelligence</strong>
              <small>Knows your {isDemo ? 'demo' : ''} profile · Long-term memory · Always educational</small>
            </div>
            <div className="model-picker" data-testid="model-picker">
              {availableModels.map((m) => (
                <button
                  key={m.id}
                  data-testid={`model-choice-${m.id}`}
                  className={`model-pill ${model === m.id ? 'active' : ''}`}
                  onClick={() => chooseModel(m.id)}
                  title={m.label}
                  disabled={loading}
                >
                  {m.id === 'claude' ? 'Claude' : 'GPT'}
                </button>
              ))}
            </div>
          </div>
          <div className="chat-body" data-testid="chat-body">
            {messages.length === 0 && (
              <div className="message assistant" data-testid="chat-welcome">
                <strong>Ask me about your money{userName ? `, ${userName.split(' ')[0]}` : ''}.</strong>
                <p>I can help you understand your trends, plan for goals with Indian tax rules (FY 2025-26), or explain financial concepts. Currently answering with <b>{activeModelLabel}</b>.</p>
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`message ${m.role}`} data-testid={`chat-message-${m.role}-${i}`}>
                {m.role === 'assistant' && m.model && (
                  <small className="msg-model-tag" data-testid={`msg-model-${i}`}>{availableModels.find((x) => x.id === m.model)?.label || m.model}</small>
                )}
                <p>{m.text || (loading && i === messages.length - 1 ? '…' : '')}</p>
                {m.role === 'assistant' && m.text && voice.ttsSupported && (
                  <button className="link-mic" data-testid={`speak-message-${i}`} onClick={() => voice.speaking ? voice.stopSpeaking() : voice.speak(m.text)}><Volume2 size={12}/> {voice.speaking ? 'Stop' : 'Read aloud'}</button>
                )}
              </div>
            ))}
            {loading && <div className="typing"><i></i><i></i><i></i></div>}
          </div>
          <div className="suggestions">
            {['Why did my savings decrease?', 'Which goal should I focus on?', 'What should I learn next?'].map((q) => (
              <button data-testid={`suggested-question-${q.slice(0, 8).replaceAll(' ', '-').toLowerCase()}`} key={q} onClick={() => ask(q)}>{q}</button>
            ))}
          </div>
          <div className="chat-input">
            {voice.supported && (
              <button data-testid="voice-mic-button" className={`icon-btn mic-btn ${voice.listening ? 'listening' : ''}`} onClick={() => voice.listening ? voice.stop() : voice.start()} title={voice.listening ? 'Stop listening' : 'Speak your question'}>
                {voice.listening ? <MicOff size={17}/> : <Mic size={17}/>}
              </button>
            )}
            <input data-testid="ask-finaura-input" value={message} onChange={(e) => setMessage(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && ask()} placeholder={voice.listening ? 'Listening…' : `Ask ${activeModelLabel} anything…`} />
            <button data-testid="ask-finaura-send-button" onClick={() => ask()}><Send size={17} /></button>
          </div>
          {voice.ttsSupported && (
            <label className="voice-toggle" data-testid="voice-reply-toggle">
              <input type="checkbox" checked={voiceMode} onChange={toggleVoiceReply}/> Read replies aloud
            </label>
          )}
          <small className="disclaimer">FINAURA AI provides education, not investment orders. {isDemo && 'This conversation uses demo data.'}</small>
        </Card>
        <div className="ask-side">
          <div className="eyebrow">Try asking</div>
          <h3>Make your money clearer.</h3>
          {['Which month did my expenses increase the most?', 'What are my highest spending categories?', 'Explain the FY 2025-26 new tax regime.'].map((q) => (
            <button data-testid={`ask-prompt-${q.slice(0, 10).replaceAll(' ', '-').toLowerCase()}`} className="prompt" onClick={() => ask(q)} key={q}>{q}<ChevronRight size={15} /></button>
          ))}
        </div>
      </div>
    </>
  );
}

function PasskeySection() {
  const { refreshMe } = useAuth();
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const supported = passkeysSupported();
  const load = async () => { try { setItems(await listPasskeys()); } catch {} };
  useEffect(() => { load(); }, []);
  const add = async () => {
    setBusy(true); setError('');
    try {
      const label = window.prompt('Name this passkey (e.g. "iPhone", "MacBook")', 'This device') || 'Passkey';
      await registerPasskey(label);
      await load();
      await refreshMe();
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || 'Could not register passkey.');
    } finally { setBusy(false); }
  };
  const remove = async (prefix) => {
    if (!window.confirm('Remove this passkey?')) return;
    try { await removePasskey(prefix); await load(); await refreshMe(); } catch (err) { setError(err?.message || 'Remove failed.'); }
  };
  return (
    <div className="account-card" data-testid="passkey-section">
      <h3>Passkeys (Face ID · Touch ID · Windows Hello)</h3>
      <p style={{ fontSize: 12, color: '#556b60', margin: '0 0 12px', lineHeight: 1.55 }}>
        Passkeys let you unlock FINAURA AI with your face, fingerprint, or a hardware security key — alongside your PIN.
      </p>
      {!supported && <div className="auth-provider-badge" data-testid="passkey-not-supported">Passkeys aren't available in this browser. Try Safari (iOS/macOS), Chrome, or Edge on a device with biometrics.</div>}
      {error && <div className="auth-error" data-testid="passkey-error">{error}</div>}
      <div className="rows">
        {items.length === 0 && <div><span>No passkeys yet</span><strong style={{ color: '#8b9995' }}>Add one below</strong></div>}
        {items.map((p) => (
          <div key={p.id}>
            <span>{p.label} <small style={{ color: '#94a3b8', marginLeft: 6 }}>· {p.id}</small></span>
            <strong><button data-testid={`remove-passkey-${p.id}`} className="pill-btn" onClick={() => remove(p.id)}><Trash2 size={13}/> Remove</button></strong>
          </div>
        ))}
      </div>
      {supported && (
        <button className="pill-btn mint" data-testid="add-passkey-button" onClick={add} disabled={busy} style={{ marginTop: 12 }}>
          <Fingerprint size={13}/> {busy ? 'Registering…' : 'Add a passkey'}
        </button>
      )}
    </div>
  );
}

function AiMemorySection() {
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [form, setForm] = useState({ category: 'preference', key: '', value: '' });
  const load = async () => { try { const { data } = await api.get('/memories'); setItems(data.memories || []); } catch {} };
  useEffect(() => { load(); }, []);
  const add = async () => {
    if (!form.key || !form.value) return;
    setBusy(true); setError('');
    try { await api.post('/memories', form); setForm({ category: 'preference', key: '', value: '' }); await load(); }
    catch (e) { setError(e?.response?.data?.detail || 'Could not save.'); }
    finally { setBusy(false); }
  };
  const del = async (id) => { if (!window.confirm('Forget this memory?')) return; try { await api.delete(`/memories/${id}`); await load(); } catch {} };
  const clearAll = async () => { if (!window.confirm('Clear ALL long-term memories for FINAURA AI?')) return; try { await api.delete('/memories'); await load(); } catch {} };
  return (
    <div className="account-card" data-testid="ai-memory-section">
      <h3><Brain size={16} style={{verticalAlign:'-3px',marginRight:6}}/> AI long-term memory</h3>
      <p style={{ fontSize: 12, color: '#556b60', margin: '0 0 12px', lineHeight: 1.55 }}>
        FINAURA AI stores structured facts you share — goals, income, preferences — for at least a year so it doesn't ask twice. You control everything here.
      </p>
      {error && <div className="auth-error">{error}</div>}
      <div className="memory-list" data-testid="memory-list">
        {items.length === 0 && <p style={{fontSize:12,color:'#8b9995'}}>No stored memories yet. Add one below or let FINAURA AI capture them from chat.</p>}
        {items.map((m) => (
          <div className="memory-row" key={m.id} data-testid={`memory-row-${m.id}`}>
            <div>
              <span className="memory-cat">{m.category}</span>
              <strong>{m.key}</strong>
              <p>{m.value}{m.numeric_value != null ? ` — ${m.numeric_value} ${m.unit || ''}` : ''}</p>
              <small>Updated {(m.updated_at || '').slice(0,10)}</small>
            </div>
            <button className="icon-btn" data-testid={`memory-delete-${m.id}`} onClick={() => del(m.id)}><Trash2 size={13}/></button>
          </div>
        ))}
      </div>
      <div className="memory-add">
        <select value={form.category} onChange={(e) => setForm((f) => ({...f, category: e.target.value}))} data-testid="memory-category">
          {['preference','income','expense','goal','risk','investment','tax','debt','insurance','profile','other'].map((c) => <option key={c}>{c}</option>)}
        </select>
        <input placeholder="Fact key (e.g. monthly_income)" value={form.key} onChange={(e) => setForm((f) => ({...f, key: e.target.value}))} data-testid="memory-key"/>
        <input placeholder="Value" value={form.value} onChange={(e) => setForm((f) => ({...f, value: e.target.value}))} data-testid="memory-value"/>
        <button className="pill-btn mint" data-testid="memory-add-button" onClick={add} disabled={busy}><Plus size={13}/> Add</button>
      </div>
      {items.length > 0 && (
        <button className="pill-btn" data-testid="memory-clear-all" onClick={clearAll} style={{marginTop:12}}><Trash2 size={13}/> Clear all AI memory</button>
      )}
    </div>
  );
}

function useTheme() {
  const [theme, setTheme] = useState(() => localStorage.getItem('finaura_theme') || 'light');
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('finaura_theme', theme);
  }, [theme]);
  return [theme, setTheme];
}

function AppearanceSection() {
  const [theme, setTheme] = useTheme();
  return (
    <div className="account-card" data-testid="appearance-section">
      <h3>Appearance</h3>
      <p style={{ fontSize: 12, color: '#556b60', margin: '0 0 12px' }}>Choose how FINAURA AI looks. Applies immediately.</p>
      <div className="theme-choices">
        {[
          { id: 'light', label: 'Light', icon: <Sun size={15}/> },
          { id: 'dark', label: 'Dark', icon: <Moon size={15}/> },
          { id: 'system', label: 'System', icon: <ShieldCheck size={15}/> },
        ].map((t) => (
          <button key={t.id} data-testid={`theme-${t.id}`} className={`theme-choice ${theme === t.id ? 'active' : ''}`} onClick={() => setTheme(t.id)}>
            {t.icon} <span>{t.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function AccountEditSection() {
  const { user, refreshMe, formatApiError } = useAuth();
  const [name, setName] = useState(user?.name || '');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const save = async () => {
    setBusy(true); setMsg(''); setErr('');
    try { await api.patch('/user/profile', { name }); await refreshMe(); setMsg('Saved.'); }
    catch (e) { setErr(formatApiError(e)); }
    finally { setBusy(false); }
  };
  return (
    <div className="auth-field" style={{ display:'flex', gap:8, alignItems:'flex-end' }}>
      <div style={{flex:1}}>
        <label>Display name</label>
        <input data-testid="account-name-input" value={name} onChange={(e) => setName(e.target.value)}/>
      </div>
      <button className="pill-btn mint" data-testid="account-name-save" onClick={save} disabled={busy}>{busy?'…':'Save'}</button>
      {msg && <span style={{ color: '#087f56', fontSize: 11 }}>{msg}</span>}
      {err && <span style={{ color: '#a83932', fontSize: 11 }}>{err}</span>}
    </div>
  );
}

function Settings({ isDemo, reload }) {
  const { user, removePin, setPin, logout, refreshMe, formatApiError } = useAuth();
  const navigate = useNavigate();
  const [deleted, setDeleted] = useState(false);
  const [pinModal, setPinModal] = useState(null); // 'set' | 'remove' | null
  const [pinValue, setPinValue] = useState('');
  const [currentPin, setCurrentPin] = useState('');
  const [pinError, setPinError] = useState('');
  const [pinBusy, setPinBusy] = useState(false);

  const handleDelete = async () => {
    if (isDemo) { navigate('/signup'); return; }
    try { await api.delete('/financial/data'); setDeleted(true); await refreshMe(); reload?.(); } catch {}
  };
  const closePinModal = () => { setPinModal(null); setPinValue(''); setCurrentPin(''); setPinError(''); };
  const submitPin = async () => {
    setPinError(''); setPinBusy(true);
    try {
      if (pinModal === 'set') {
        if (!/^\d{4}$/.test(pinValue)) throw new Error('PIN must be 4 digits.');
        await setPin(pinValue);
      } else if (pinModal === 'remove') {
        if (!/^\d{4}$/.test(currentPin)) throw new Error('PIN must be 4 digits.');
        await removePin(currentPin);
      }
      closePinModal();
    } catch (err) { setPinError(err?.message || formatApiError(err)); }
    finally { setPinBusy(false); }
  };

  return (
    <>
      <div className="page-intro">
        <div><div className="eyebrow">Trust is a feature</div><h2>Settings & privacy</h2><p>Your financial data belongs to you.</p></div>
      </div>
      {!isDemo && (
        <div className="account-card" data-testid="account-card">
          <h3>Account</h3>
          <AccountEditSection />
          <div className="rows">
            <div><span>Email</span><strong>{user?.email} {user?.email_verified ? <span style={{color:'#087f56',fontSize:11,marginLeft:6}}>· verified</span> : <span style={{color:'#a56800',fontSize:11,marginLeft:6}}>· pending verification</span>}</strong></div>
            <div><span>Sign-in methods</span><strong>{(user?.providers || []).join(', ') || 'email'}</strong></div>
            <div>
              <span>App lock (4-digit PIN)</span>
              <strong>
                {user?.has_pin ? (
                  <button className="pill-btn" data-testid="remove-pin-button" onClick={() => setPinModal('remove')}><KeyRound size={13}/> Change / remove</button>
                ) : (
                  <button className="pill-btn mint" data-testid="set-pin-button" onClick={() => setPinModal('set')}><Lock size={13}/> Set PIN</button>
                )}
              </strong>
            </div>
            <div>
              <span>Session</span>
              <strong><button data-testid="settings-sign-out" className="pill-btn" onClick={() => { logout(); navigate('/login'); }}><LogOut size={13}/> Sign out</button></strong>
            </div>
          </div>
        </div>
      )}
      {!isDemo && <PasskeySection />}
      {!isDemo && <AiMemorySection />}
      <AppearanceSection />
      <div className="settings-grid">
        <Card>
          <SectionTitle eyebrow="Privacy principles" title="Built with care" />
          {[['Encrypted in transit', 'Your data travels over HTTPS/TLS.'],
            ['Encrypted at rest', 'Prototype storage is isolated to your profile.'],
            ['Password hashing (bcrypt)', 'Your password is one-way hashed; even we can\'t read it.'],
            ['No bank credentials', 'FINAURA AI never asks for or stores bank passwords.'],
            ['Delete anytime', 'You can remove your financial information below.']].map(([a, b]) => (
            <div className="privacy-row" key={a}>
              <div className="privacy-icon"><LockKeyhole size={16} /></div>
              <div><strong>{a}</strong><p>{b}</p></div>
              <Check size={16} className="check" />
            </div>
          ))}
        </Card>
        <Card className="danger-card">
          <div className="eyebrow">Data controls</div>
          <h3>Prototype status</h3>
          <p>{isDemo ? 'You\'re viewing the fictional Aarav Sharma demo. Sign up to save your own data.' : 'Security features shown here are prototype implementations, not a production audit or certification.'}</p>
          {!isDemo && (
            <>
              <button data-testid="delete-data-button" className="danger-btn" onClick={handleDelete}><Trash2 size={16} /> Delete my financial data</button>
              {deleted && <div className="deleted-message" data-testid="data-deleted-message">Your saved goals and transactions have been deleted.</div>}
            </>
          )}
        </Card>
      </div>

      {pinModal && (
        <div className="modal-backdrop">
          <div className="modal">
            <button className="modal-close" onClick={closePinModal}>×</button>
            <div className="eyebrow">{pinModal === 'set' ? 'Set PIN' : 'Remove PIN'}</div>
            <h3>{pinModal === 'set' ? 'Add an app lock' : 'Confirm your current PIN'}</h3>
            {pinError && <div className="auth-error">{pinError}</div>}
            <div className="auth-field">
              <label>{pinModal === 'set' ? 'New 4-digit PIN' : 'Current PIN'}</label>
              <input data-testid="pin-modal-input" type="password" inputMode="numeric" maxLength={4} pattern="\d{4}" value={pinModal === 'set' ? pinValue : currentPin} onChange={(e) => pinModal === 'set' ? setPinValue(e.target.value.replace(/\D/g, '')) : setCurrentPin(e.target.value.replace(/\D/g, ''))} placeholder="0000"/>
            </div>
            <button className="auth-btn" data-testid="pin-modal-submit" onClick={submitPin} disabled={pinBusy}>
              {pinBusy ? 'Working…' : (pinModal === 'set' ? 'Save PIN' : 'Remove PIN')}
            </button>
          </div>
        </div>
      )}
    </>
  );
}

// ================== Data loaders ==================

function useOverview(mode) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const load = async () => {
    try {
      const url = mode === 'demo' ? '/demo/overview' : '/financial/overview';
      const { data } = await api.get(url);
      setData(data);
    } catch (e) { setError(formatApiError(e)); }
  };
  useEffect(() => { load(); }, [mode]);
  return { data, error, reload: load };
}

function LoadingScreen() {
  return (
    <div className="loading-screen">
      <div className="brand"><span className="brand-mark">f</span><span>finaura</span></div>
      <p>Building your financial picture…</p>
    </div>
  );
}

function ProtectedApp() {
  const { user, unlocked } = useAuth();
  const location = useLocation();
  if (user === undefined) return <LoadingScreen />;
  if (user === null) return <Navigate to="/login" state={{ from: location }} replace />;
  if (!user.onboarding_done) return <Navigate to="/onboarding" replace />;
  if (user.has_pin && !unlocked) return <Navigate to="/lock" state={{ next: location.pathname }} replace />;
  return <AppWorkspace mode="auth" />;
}

function AppWorkspace({ mode }) {
  const location = useLocation();
  const { data, reload } = useOverview(mode);
  const isDemo = mode === 'demo';
  if (!data) return <LoadingScreen />;
  const raw = location.pathname.replace(/^\/demo/, '') || '/';
  // Article detail routes like /learn/:id
  if (raw.startsWith('/learn/') && raw.length > 7) {
    return <Shell mode={mode}><ArticleDetail isDemo={isDemo}/></Shell>;
  }
  const pages = {
    '/': <Dashboard data={data} isDemo={isDemo} />,
    '/finances': <Finances data={data} />,
    '/statements': <Statements data={data} isDemo={isDemo} reload={reload} />,
    '/analysis': <Analysis data={data} />,
    '/goals': <Goals data={data} isDemo={isDemo} reload={reload} />,
    '/whatif': <WhatIf data={data} isDemo={isDemo} />,
    '/changes': <Changes data={data} />,
    '/learn': <Learn isDemo={isDemo} />,
    '/ask': <Ask isDemo={isDemo} userName={data.user?.name} />,
    '/profile': <Profile isDemo={isDemo} />,
    '/settings': <Settings isDemo={isDemo} reload={reload} />,
  };
  return <Shell mode={mode}>{pages[raw] || pages['/']}</Shell>;
}

function RootRedirect() {
  const { user, unlocked } = useAuth();
  if (user === undefined) return <LoadingScreen />;
  if (user === null) return <Navigate to="/login" replace />;
  if (!user.onboarding_done) return <Navigate to="/onboarding" replace />;
  if (user.has_pin && !unlocked) return <Navigate to="/lock" replace />;
  return <ProtectedApp />;
}

function AppRoutes() {
  const { user, config } = useAuth();

  // Load Apple JS script if enabled
  useEffect(() => {
    if (config.apple_enabled && !document.getElementById('apple-id-script')) {
      const s = document.createElement('script');
      s.id = 'apple-id-script';
      s.src = 'https://appleid.cdn-apple.com/appleauth/static/jsapi/appleid/1/en_US/appleid.auth.js';
      s.async = true;
      document.head.appendChild(s);
    }
  }, [config.apple_enabled]);

  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
      <Route path="/signup" element={user ? <Navigate to="/" replace /> : <Signup />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route path="/verify-email" element={<VerifyEmail />} />
      <Route path="/onboarding" element={user ? <Onboarding /> : <Navigate to="/login" replace />} />
      <Route path="/lock" element={user ? <PinLock mode="verify" /> : <Navigate to="/login" replace />} />
      <Route path="/set-pin" element={user ? <PinLock mode="set" /> : <Navigate to="/login" replace />} />
      <Route path="/demo/*" element={<AppWorkspace mode="demo" />} />
      <Route path="/demo" element={<AppWorkspace mode="demo" />} />
      <Route path="/*" element={<ProtectedApp />} />
    </Routes>
  );
}

function AppInner() {
  const clientId = process.env.REACT_APP_GOOGLE_CLIENT_ID || '';
  // Apply saved theme on first mount so all pages see it.
  useEffect(() => {
    const saved = localStorage.getItem('finaura_theme') || 'light';
    document.documentElement.dataset.theme = saved;
    document.title = 'FINAURA AI — Your Money. Your Goals. Your Future.';
  }, []);
  const content = <AppRoutes />;
  return clientId ? (
    <GoogleOAuthProvider clientId={clientId}>{content}</GoogleOAuthProvider>
  ) : content;
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppInner />
      </AuthProvider>
    </BrowserRouter>
  );
}
