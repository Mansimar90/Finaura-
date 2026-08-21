import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { ArrowLeft, Sparkles } from 'lucide-react';
import '../auth.css';

export default function ArticleDetail({ isDemo, articleId }) {
  const params = useParams();
  const id = articleId || params.id;
  const navigate = useNavigate();
  const [article, setArticle] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    api.get(`/learn/articles/${id}`).then((r) => setArticle(r.data)).catch((e) => setError(e?.response?.data?.detail || 'Could not load article.'));
  }, [id]);
  const askAi = () => {
    if (!article) return;
    const target = isDemo ? '/demo/ask' : '/ask';
    sessionStorage.setItem('finaura_learn_prompt', `I just read the FINAURA Learn article "${article.title}". Can you explain the key ideas simply, with an Indian perspective?`);
    navigate(target);
  };
  return (
    <div className="article-page" data-testid="article-page">
      <button className="auth-back" onClick={() => navigate(-1)} data-testid="article-back-button"><ArrowLeft size={14}/> Back to FINAURA Learn</button>
      {error && <div className="auth-error" data-testid="article-error">{error}</div>}
      {!article && !error && <p style={{color:'#8b9995'}}>Loading article…</p>}
      {article && (
        <>
          <div className={`article-hero ${article.art_variant || 'mint'}`}>
            <span className="eyebrow">{article.category} · {article.read_minutes} min read</span>
            <h1 data-testid="article-title">{article.title}</h1>
            <p style={{color:'#556b60',fontSize:14,margin:'8px 0 0'}}>{article.why_relevant}</p>
          </div>
          <div className="article-body" data-testid="article-body">
            {article.body.map((section, i) => (
              <section key={i}>
                <h2>{section.heading}</h2>
                <p>{section.text}</p>
              </section>
            ))}
            <div className="article-ai-cta">
              <div>
                <div className="eyebrow">Keep learning</div>
                <h3>Ask FINAURA AI about this</h3>
                <p>Get personalised answers grounded in your own numbers.</p>
              </div>
              <button data-testid="article-ask-ai-button" className="primary-btn" onClick={askAi}><Sparkles size={15}/> Ask FINAURA AI</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
