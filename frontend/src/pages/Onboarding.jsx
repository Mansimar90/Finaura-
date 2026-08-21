import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import '../auth.css';

export default function Onboarding() {
  const navigate = useNavigate();
  const { user, completeOnboarding, formatApiError } = useAuth();
  const [choice, setChoice] = useState('empty');
  const [name, setName] = useState(user?.name || '');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setError(''); setBusy(true);
    try {
      await completeOnboarding(choice, name);
      navigate('/set-pin');
    } catch (err) {
      setError(formatApiError(err));
    } finally { setBusy(false); }
  };

  return (
    <div className="onboard-page">
      <div className="onboard-card" data-testid="onboarding-card">
        <div className="auth-brand"><span className="brand-mark">f</span> FINAURA AI</div>
        <h1>Welcome to FINAURA AI</h1>
        <p>Would you like to start fresh with your own data, or explore first with our sample profile? You can change this later.</p>

        {error && <div className="auth-error">{error}</div>}

        <div className="auth-field" style={{ maxWidth: 340 }}>
          <label>Preferred name</label>
          <input data-testid="onboarding-name-input" type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="How should we greet you?"/>
        </div>

        <div className="onboard-choices">
          <button data-testid="onboarding-empty-choice" type="button" className={`onboard-choice ${choice === 'empty' ? 'selected' : ''}`} onClick={() => setChoice('empty')}>
            <span className="badge">Recommended</span>
            <h3>Start with a blank slate</h3>
            <p>Enter your own income, expenses and goals. Your financial picture builds as you go.</p>
          </button>
          <button data-testid="onboarding-demo-choice" type="button" className={`onboard-choice ${choice === 'demo' ? 'selected' : ''}`} onClick={() => setChoice('demo')}>
            <span className="badge">Explore</span>
            <h3>Load sample data</h3>
            <p>Try FINAURA AI with six months of realistic demo transactions. Clear it any time from Settings.</p>
          </button>
        </div>

        <button data-testid="onboarding-continue-button" className="auth-btn" disabled={busy} onClick={submit} style={{ maxWidth: 240 }}>
          {busy ? 'Setting up…' : 'Continue'}
        </button>
      </div>
    </div>
  );
}
