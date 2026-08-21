import { useEffect, useState, useCallback } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Fingerprint } from 'lucide-react';
import { useAuth } from '../lib/auth';
import { unlockWithPasskey, passkeysSupported } from '../lib/passkey';
import '../auth.css';

/**
 * PinLock handles three modes:
 *  - "set"    : first time — set a 4-digit PIN (with confirm)
 *  - "verify" : unlock the app with the stored PIN
 *  - "skip"   : allow skipping PIN setup during onboarding
 */
export default function PinLock({ mode = 'verify' }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, setPin, verifyPin, logout, formatApiError } = useAuth();
  const nextPath = location.state?.next || '/';
  const [pin, setPinValue] = useState('');
  const [confirmPin, setConfirmPin] = useState('');
  const [phase, setPhase] = useState(mode === 'set' ? 'enter' : 'verify'); // enter -> confirm -> done
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const canUsePasskey = mode === 'verify' && user?.has_passkey && passkeysSupported();

  const tryPasskey = async () => {
    setError('');
    try {
      await unlockWithPasskey();
      sessionStorage.setItem('finaura_unlocked', '1');
      window.location.assign(nextPath);
    } catch (err) {
      setError(err?.message || 'Passkey unlock cancelled.');
    }
  };

  const handleKey = useCallback((digit) => {
    setError('');
    if (phase === 'confirm') {
      setConfirmPin((p) => (p.length < 4 ? p + digit : p));
    } else {
      setPinValue((p) => (p.length < 4 ? p + digit : p));
    }
  }, [phase]);

  const backspace = () => {
    setError('');
    if (phase === 'confirm') setConfirmPin((p) => p.slice(0, -1));
    else setPinValue((p) => p.slice(0, -1));
  };

  const clearAll = () => { setPinValue(''); setConfirmPin(''); setError(''); setPhase(mode === 'set' ? 'enter' : 'verify'); };

  // Keyboard support
  useEffect(() => {
    const onKey = (e) => {
      if (/^\d$/.test(e.key)) handleKey(e.key);
      else if (e.key === 'Backspace') backspace();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

  // Auto-submit when full
  useEffect(() => {
    const submit = async () => {
      if (mode === 'set') {
        if (phase === 'enter' && pin.length === 4) {
          setPhase('confirm');
        } else if (phase === 'confirm' && confirmPin.length === 4) {
          if (confirmPin !== pin) {
            setError('PINs don\'t match. Try again.');
            setTimeout(clearAll, 800);
            return;
          }
          setBusy(true);
          try {
            await setPin(pin);
            navigate('/', { replace: true });
          } catch (err) {
            setError(formatApiError(err));
            setTimeout(clearAll, 800);
          } finally { setBusy(false); }
        }
      } else {
        if (pin.length === 4 && !busy) {
          setBusy(true);
          try {
            await verifyPin(pin);
            navigate(nextPath, { replace: true });
          } catch (err) {
            setError(formatApiError(err));
            setTimeout(() => { setPinValue(''); setError((e) => e); }, 500);
          } finally { setBusy(false); }
        }
      }
    };
    submit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pin, confirmPin, phase]);

  const shownPin = phase === 'confirm' ? confirmPin : pin;
  const heading = mode === 'set'
    ? (phase === 'enter' ? 'Create a 4-digit PIN' : 'Confirm your PIN')
    : 'Enter your PIN';
  const hint = mode === 'set'
    ? (phase === 'enter' ? 'Add an extra lock on top of your password.' : 'Type the same PIN once more.')
    : `Welcome back${user?.name ? `, ${user.name.split(' ')[0]}` : ''}. Unlock FINAURA AI to continue.`;

  return (
    <div className="pin-page" data-testid="pin-lock-screen">
      <div className="pin-card">
        <div className="brand-mark">f</div>
        {mode === 'set' && <span className="pin-badge">Extra security</span>}
        <h1>{heading}</h1>
        <p className="hint">{hint}</p>

        <div className="pin-dots" data-testid="pin-dots">
          {[0,1,2,3].map((i) => (
            <span key={i} className={`pin-dot ${shownPin.length > i ? 'filled' : ''} ${error ? 'error' : ''}`} data-testid={`pin-dot-${i}`}/>
          ))}
        </div>

        <div className="pin-error-msg" data-testid="pin-error-msg">{error}</div>

        <div className="pin-pad">
          {['1','2','3','4','5','6','7','8','9'].map((d) => (
            <button key={d} data-testid={`pin-key-${d}`} className="pin-key" disabled={busy} onClick={() => handleKey(d)}>{d}</button>
          ))}
          <button className="pin-key wide" onClick={clearAll} data-testid="pin-key-clear">Clear</button>
          <button data-testid="pin-key-0" className="pin-key" disabled={busy} onClick={() => handleKey('0')}>0</button>
          <button className="pin-key wide" onClick={backspace} data-testid="pin-key-backspace">⌫</button>
        </div>

        <div className="pin-actions">
          {canUsePasskey && (
            <button data-testid="unlock-with-passkey-button" onClick={tryPasskey}>
              <Fingerprint size={14} style={{ verticalAlign: 'middle', marginRight: 4 }} /> Use passkey
            </button>
          )}
          {mode === 'set' && (
            <button data-testid="skip-pin-button" onClick={() => navigate('/', { replace: true })}>Skip for now</button>
          )}
          {mode === 'verify' && (
            <button data-testid="sign-out-from-lock" onClick={() => { logout(); navigate('/login', { replace: true }); }}>Sign out</button>
          )}
        </div>
      </div>
    </div>
  );
}
