import { useState } from 'react';
import { apiRequest } from '../lib/api';

function formatCountdown(deadlineIso) {
  if (!deadlineIso) return null;
  const ts = Date.parse(deadlineIso);
  if (Number.isNaN(ts)) return deadlineIso;
  const diffHours = Math.round((ts - Date.now()) / 3600000);
  if (diffHours < 0) return 'Deadline passed';
  if (diffHours < 24) return `${diffHours}h left`;
  const days = Math.round(diffHours / 24);
  return `${days}d left`;
}

export default function OpportunityInspector({
  opportunity,
  qaHistory,
  preferredLanguage,
  onOpportunityUpdated,
  onQaUpdated,
  onNotify,
}) {
  const [viewLang, setViewLang] = useState(preferredLanguage || 'both');
  const [question, setQuestion] = useState('');
  const [qaLang, setQaLang] = useState('auto');
  const [sendQaToWa, setSendQaToWa] = useState(false);
  const [busyAction, setBusyAction] = useState('');
  const [researchReport, setResearchReport] = useState('');

  if (!opportunity) {
    return (
      <div className="ws-panel ws-empty-inspector">
        <p style={{ color: 'rgba(255,255,255,0.6)', fontSize: '14px' }}>
          Select an opportunity from the list to view full English &amp; हिंदी details, track deadlines, run LiveScout Deep Research, or ask AI questions.
        </p>
      </div>
    );
  }

  const oid = opportunity.id || opportunity._id;
  const countdown = formatCountdown(opportunity.deadline_iso);

  const handleTrack = async (status = 'interested') => {
    setBusyAction('track');
    try {
      await apiRequest(`/api/opportunities/${oid}/track`, {
        method: 'POST',
        body: JSON.stringify({ status, notify_whatsapp: true }),
      });
      onNotify(`Saved "${opportunity.title}" (${status}) with deadline reminders.`);
      await onOpportunityUpdated();
    } catch (err) {
      onNotify(err.message, true);
    } finally {
      setBusyAction('');
    }
  };

  const handleSkip = async () => {
    setBusyAction('skip');
    try {
      await apiRequest(`/api/opportunities/${oid}/feedback`, {
        method: 'POST',
        body: JSON.stringify({ feedback: 'skip' }),
      });
      onNotify('Recorded feedback to tune future personalized matches.');
    } catch (err) {
      onNotify(err.message, true);
    } finally {
      setBusyAction('');
    }
  };

  const handlePushWhatsApp = async () => {
    setBusyAction('whatsapp');
    try {
      await apiRequest(`/api/opportunities/${oid}/send-whatsapp`, { method: 'POST' });
      onNotify('Sent full bilingual brief & action buttons to WhatsApp!');
    } catch (err) {
      onNotify(err.message, true);
    } finally {
      setBusyAction('');
    }
  };

  const handleDeepResearch = async () => {
    setBusyAction('research');
    setResearchReport('');
    try {
      const data = await apiRequest(`/api/opportunities/${oid}/research`, {
        method: 'POST',
        body: JSON.stringify({ send_to_whatsapp: true }),
      });
      setResearchReport(data.report || '');
      onNotify('Deep Research completed & synced!');
      await onOpportunityUpdated();
    } catch (err) {
      onNotify(err.message, true);
    } finally {
      setBusyAction('');
    }
  };

  const handleAsk = async (customPrompt = null, isDraft = false) => {
    const q = (customPrompt !== null ? customPrompt : question).trim();
    if (!q && !isDraft) return;
    setBusyAction('ask');
    try {
      const payload = {
        question: q || 'Generate application cover letter',
        draft: isDraft,
        send_to_whatsapp: sendQaToWa,
      };
      if (qaLang !== 'auto') {
        payload.language = qaLang;
      }
      const data = await apiRequest(`/api/opportunities/${oid}/ask`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      if (customPrompt === null) {
        setQuestion('');
      }
      onQaUpdated(oid, data.qa_history || []);
      onNotify(`Answered in ${data.language === 'hi' ? 'Hindi' : data.language === 'en' ? 'English' : 'EN + Hindi'}.`);
    } catch (err) {
      onNotify(err.message, true);
    } finally {
      setBusyAction('');
    }
  };

  const showEn = viewLang === 'en' || viewLang === 'both';
  const showHi = viewLang === 'hi' || viewLang === 'both';

  return (
    <div className="ws-panel ws-inspector">
      <div className="ws-inspector-top">
        <div>
          <div className="ws-badges">
            <span className="ws-tag">{opportunity.category || 'OPPORTUNITY'}</span>
            {opportunity.priority_score != null && (
              <span className="ws-tag ws-tag-green">
                Match {Math.round(opportunity.priority_score * 100)}%
              </span>
            )}
            {countdown && <span className="ws-tag ws-tag-amber">{countdown}</span>}
            {opportunity.is_tracked && (
              <span className="ws-tag ws-tag-blue">
                Tracked: {opportunity.tracking?.status || 'interested'}
              </span>
            )}
          </div>
          <h3 className="ws-opp-heading">{opportunity.title}</h3>
          {opportunity.title_hi && opportunity.title_hi !== opportunity.title && (
            <p className="ws-opp-subheading">{opportunity.title_hi}</p>
          )}
          <p className="ws-opp-meta">
            {opportunity.organization} &middot; {opportunity.location} &middot; {opportunity.stipend_or_reward}
          </p>
        </div>

        <div className="ws-lang-switch">
          {['both', 'en', 'hi'].map((code) => (
            <button
              key={code}
              type="button"
              className={`ws-mini-btn ${viewLang === code ? 'active' : ''}`}
              onClick={() => setViewLang(code)}
            >
              {code === 'both' ? 'EN + हिंदी' : code === 'en' ? 'English' : 'हिंदी'}
            </button>
          ))}
        </div>
      </div>

      <div className="ws-action-row">
        <button
          type="button"
          className="ws-btn ws-btn-green"
          disabled={Boolean(busyAction)}
          onClick={() => handleTrack('interested')}
        >
          {busyAction === 'track' ? 'Saving...' : opportunity.is_tracked ? 'Tracked (Renew Reminders)' : 'Save & Track Deadline'}
        </button>
        <button
          type="button"
          className="ws-btn"
          disabled={Boolean(busyAction)}
          onClick={() => handleTrack('applied')}
        >
          Mark Applied
        </button>
        <button
          type="button"
          className="ws-btn"
          disabled={Boolean(busyAction)}
          onClick={handleDeepResearch}
        >
          {busyAction === 'research' ? 'Researching...' : 'Deep Research'}
        </button>
        <button
          type="button"
          className="ws-btn"
          disabled={Boolean(busyAction)}
          onClick={handlePushWhatsApp}
        >
          {busyAction === 'whatsapp' ? 'Sending...' : 'Send to WhatsApp'}
        </button>
        <button
          type="button"
          className="ws-btn ws-btn-muted"
          disabled={Boolean(busyAction)}
          onClick={handleSkip}
        >
          Skip
        </button>
        {opportunity.url && opportunity.url !== 'N/A' && (
          <a href={opportunity.url} target="_blank" rel="noreferrer" className="ws-btn ws-btn-link">
            Official Link &rarr;
          </a>
        )}
      </div>

      <div className="ws-facts-grid">
        <div>
          <span className="ws-fact-label">Deadline</span>
          <span className="ws-fact-val">{opportunity.deadline_iso || 'Not specified'}</span>
        </div>
        <div>
          <span className="ws-fact-label">Eligibility</span>
          <span className="ws-fact-val">{opportunity.eligibility || 'See official link'}</span>
        </div>
        <div>
          <span className="ws-fact-label">How to Apply</span>
          <span className="ws-fact-val">{opportunity.application_steps || 'Apply via official link'}</span>
        </div>
        <div>
          <span className="ws-fact-label">ID</span>
          <span className="ws-fact-val"><code>{oid}</code></span>
        </div>
      </div>

      {opportunity.reasoning && (
        <div className="ws-reasoning-box">
          <strong>AI Match Rationale:</strong> {opportunity.reasoning}
        </div>
      )}

      {showEn && (
        <div className="ws-detail-block">
          <h4>Complete Details (English)</h4>
          <pre className="ws-pre">{opportunity.full_details_en || opportunity.summary_en || opportunity.summary}</pre>
        </div>
      )}

      {showHi && (
        <div className="ws-detail-block ws-detail-hi">
          <h4>पूरी जानकारी (हिंदी में विवरण)</h4>
          <pre className="ws-pre">{opportunity.full_details_hi || opportunity.summary_hi || opportunity.summary_en}</pre>
        </div>
      )}

      {Array.isArray(opportunity.citations) && opportunity.citations.length > 0 && (
        <div className="ws-detail-block">
          <h4>Verified Citations</h4>
          <ul className="ws-citations">
            {opportunity.citations.map((cit, idx) => (
              <li key={idx}>
                <a href={cit.url} target="_blank" rel="noreferrer">
                  {cit.title || cit.url}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      {researchReport && (
        <div className="ws-detail-block ws-detail-research">
          <h4>LiveScout Deep Research Report</h4>
          <pre className="ws-pre">{researchReport}</pre>
        </div>
      )}

      <div className="ws-qa-box">
        <div className="ws-qa-header">
          <h4>Ask AI About This Opportunity (English / हिंदी)</h4>
          <select value={qaLang} onChange={(e) => setQaLang(e.target.value)} className="ws-select">
            <option value="auto">Auto-detect Query Language</option>
            <option value="en">Answer in English</option>
            <option value="hi">हिंदी में जवाब (Hindi)</option>
            <option value="both">Bilingual (EN + हिंदी)</option>
          </select>
        </div>

        <div className="ws-qa-starters">
          <button type="button" className="ws-chip" onClick={() => handleAsk('What is the exact eligibility, stipend, and selection process?')}>
            Eligibility &amp; Selection?
          </button>
          <button type="button" className="ws-chip" onClick={() => handleAsk('इस अवसर की पात्रता, स्टाइपेंड और आवेदन प्रक्रिया हिंदी में विस्तार से समझाएं।')}>
            हिंदी में पूरी जानकारी
          </button>
          <button type="button" className="ws-chip" onClick={() => handleAsk('Draft a tailored application note / SOP for my profile', true)}>
            Draft Cover Letter / SOP
          </button>
        </div>

        <div className="ws-qa-thread">
          {(qaHistory || []).length === 0 ? (
            <p className="ws-muted-note">No questions asked yet. Ask anything in English, Hindi, or Hinglish!</p>
          ) : (
            qaHistory.map((turn, idx) => (
              <div key={idx} className={`ws-qa-msg ${turn.role === 'user' ? 'user' : 'assistant'}`}>
                <span className="ws-qa-role">{turn.role === 'user' ? 'You' : 'OppTrax AI'}</span>
                <pre className="ws-pre">{turn.text}</pre>
              </div>
            ))
          )}
        </div>

        <form
          className="ws-qa-form"
          onSubmit={(e) => {
            e.preventDefault();
            handleAsk();
          }}
        >
          <input
            type="text"
            className="ws-input"
            placeholder="Ask in English or हिंदी (e.g. 'Is it remote?', 'आवेदन कैसे करें?')..."
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            disabled={busyAction === 'ask'}
          />
          <button type="submit" className="ws-btn ws-btn-green" disabled={busyAction === 'ask' || !question.trim()}>
            {busyAction === 'ask' ? 'Thinking...' : 'Ask AI'}
          </button>
        </form>
        <label className="ws-checkbox-row">
          <input type="checkbox" checked={sendQaToWa} onChange={(e) => setSendQaToWa(e.target.checked)} />
          <span>Also forward AI answer to my WhatsApp</span>
        </label>
      </div>
    </div>
  );
}
