import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Browser Web Speech API (STT + TTS). No API keys required.
 * Falls back gracefully when unsupported (older Firefox, private tabs).
 */
export function useVoice() {
  const recognitionRef = useRef(null);
  const [listening, setListening] = useState(false);
  const [supported, setSupported] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [ttsSupported, setTtsSupported] = useState(false);
  const [speaking, setSpeaking] = useState(false);

  useEffect(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SR) {
      const rec = new SR();
      rec.lang = 'en-IN';
      rec.interimResults = true;
      rec.continuous = false;
      rec.onresult = (e) => {
        let t = '';
        for (let i = 0; i < e.results.length; i++) t += e.results[i][0].transcript;
        setTranscript(t.trim());
      };
      rec.onend = () => setListening(false);
      rec.onerror = () => setListening(false);
      recognitionRef.current = rec;
      setSupported(true);
    }
    setTtsSupported('speechSynthesis' in window);
  }, []);

  const start = useCallback(() => {
    if (!recognitionRef.current) return;
    setTranscript('');
    try { recognitionRef.current.start(); setListening(true); } catch {}
  }, []);
  const stop = useCallback(() => {
    if (!recognitionRef.current) return;
    try { recognitionRef.current.stop(); } catch {}
    setListening(false);
  }, []);
  const speak = useCallback((text) => {
    if (!('speechSynthesis' in window) || !text) return;
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = 'en-IN';
    utter.rate = 1.02;
    utter.pitch = 1;
    utter.onstart = () => setSpeaking(true);
    utter.onend = () => setSpeaking(false);
    utter.onerror = () => setSpeaking(false);
    window.speechSynthesis.speak(utter);
  }, []);
  const stopSpeaking = useCallback(() => { if ('speechSynthesis' in window) { window.speechSynthesis.cancel(); setSpeaking(false); } }, []);

  return { supported, listening, transcript, setTranscript, start, stop, speak, ttsSupported, speaking, stopSpeaking };
}
