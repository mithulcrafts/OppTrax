import { useCallback, useEffect, useState } from 'react';
import { apiRequest } from '../lib/api';
import OpportunityInspector from './OpportunityInspector';
import ProfileTab from './ProfileTab';
import WhatsAppBridgeTab from './WhatsAppBridgeTab';

export default function Dashboard({ sessionUser, deepLink, onUserUpdated, onLogout }) {
  const [activeTab, setActiveTab] = useState('board'); // 'board' | 'whatsapp' | 'profile'
  const [user, setUser] = useState(sessionUser);
  const [integrations, setIntegrations] = useState({});
  const [waDeepLink, setWaDeepLink] = useState(deepLink || '');
  const [opportunities, setOpportunities] = useState([]);
  const [scouts, setScouts] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [selectedDetail, setSelectedDetail] = useState(null);
  const [qaHistoryMap, setQaHistoryMap] = useState({});

  const [searchPrompt, setSearchPrompt] = useState('');
  const [launchScout, setLaunchScout] = useState(true);
  const [sendSearchToWa, setSendSearchToWa] = useState(true);
  const [boardFilter, setBoardFilter] = useState('');
  const [trackedOnly, setTrackedOnly] = useState(false);
  const [ingestInput, setIngestInput] = useState('');
  const [searching, setSearching] = useState(false);
  const [toast, setToast] = useState(null);

  const notify = useCallback((message, isError = false) => {
    setToast({ message, isError });
    setTimeout(() => {
      setToast((curr) => (curr && curr.message === message ? null : curr));
    }, 5000);
  }, []);

  const loadWorkspaceData = useCallback(async () => {
    try {
      const [profData, oppData, scoutData] = await Promise.all([
        apiRequest('/api/user/profile'),
        apiRequest(`/api/opportunities?q=${encodeURIComponent(boardFilter)}&tracked_only=${trackedOnly}`),
        apiRequest('/api/scouts'),
      ]);
      setUser(profData.user);
      setIntegrations(profData.integrations || {});
      if (profData.whatsapp_deep_link) setWaDeepLink(profData.whatsapp_deep_link);
      if (onUserUpdated) onUserUpdated(profData.user);
      const list = oppData.opportunities || [];
      setOpportunities(list);
      setScouts(scoutData.scouts || []);
      setSelectedId((prev) => prev || (list.length > 0 ? list[0].id : null));
    } catch (err) {
      notify(err.message, true);
    }
  }, [boardFilter, trackedOnly, onUserUpdated, notify]);

  const loadSelectedDetail = useCallback(async (oppId) => {
    if (!oppId) return;
    try {
      const data = await apiRequest(`/api/opportunities/${oppId}`);
      setSelectedDetail(data.opportunity);
      setQaHistoryMap((prev) => ({ ...prev, [oppId]: data.qa_history || [] }));
    } catch {
      // Ignore if deleted
    }
  }, []);

  useEffect(() => {
    let ignore = false;
    Promise.all([
      apiRequest('/api/user/profile'),
      apiRequest(`/api/opportunities?q=${encodeURIComponent(boardFilter)}&tracked_only=${trackedOnly}`),
      apiRequest('/api/scouts'),
    ])
      .then(([profData, oppData, scoutData]) => {
        if (ignore) return;
        setUser(profData.user);
        setIntegrations(profData.integrations || {});
        if (profData.whatsapp_deep_link) setWaDeepLink(profData.whatsapp_deep_link);
        if (onUserUpdated) onUserUpdated(profData.user);
        const list = oppData.opportunities || [];
        setOpportunities(list);
        setScouts(scoutData.scouts || []);
        setSelectedId((prev) => prev || (list.length > 0 ? list[0].id : null));
      })
      .catch((err) => {
        if (!ignore) notify(err.message, true);
      });
    return () => {
      ignore = true;
    };
  }, [boardFilter, trackedOnly, onUserUpdated, notify]);

  useEffect(() => {
    if (!selectedId) return undefined;
    let ignore = false;
    apiRequest(`/api/opportunities/${selectedId}`)
      .then((data) => {
        if (ignore) return;
        setSelectedDetail(data.opportunity);
        setQaHistoryMap((prev) => ({ ...prev, [selectedId]: data.qa_history || [] }));
      })
      .catch(() => {});
    return () => {
      ignore = true;
    };
  }, [selectedId]);

  const handleLanguageChange = async (lang) => {
    try {
      await apiRequest('/api/user/profile', {
        method: 'PUT',
        body: JSON.stringify({ preferred_language: lang }),
      });
      notify(`Language switched to ${lang === 'both' ? 'EN + हिंदी' : lang === 'hi' ? 'हिंदी' : 'English'}.`);
      await loadWorkspaceData();
    } catch (err) {
      notify(err.message, true);
    }
  };

  const handleDigestChange = async (mode) => {
    try {
      await apiRequest('/api/user/profile', {
        method: 'PUT',
        body: JSON.stringify({ digest_mode: mode }),
      });
      notify(`WhatsApp alert cadence set to ${mode}.`);
      await loadWorkspaceData();
    } catch (err) {
      notify(err.message, true);
    }
  };

  const handlePersonalizedSearch = async (e) => {
    e.preventDefault();
    if (!searchPrompt.trim()) return;
    setSearching(true);
    try {
      const data = await apiRequest('/api/search', {
        method: 'POST',
        body: JSON.stringify({
          query: searchPrompt.trim(),
          launch_scout: launchScout,
          send_to_whatsapp: sendSearchToWa,
        }),
      });
      setSearchPrompt('');
      await loadWorkspaceData();
      if (data.opportunities?.length > 0) {
        setSelectedId(data.opportunities[0].id);
      }
      notify(`Found ${data.count || 0} personalized matches (${data.source_mode || 'DONE'}).`);
    } catch (err) {
      notify(err.message, true);
    } finally {
      setSearching(false);
    }
  };

  const handleIngest = async (e) => {
    e.preventDefault();
    if (!ingestInput.trim()) return;
    setSearching(true);
    try {
      const data = await apiRequest('/api/opportunities/ingest', {
        method: 'POST',
        body: JSON.stringify({ content: ingestInput.trim(), notify_whatsapp: true }),
      });
      setIngestInput('');
      await loadWorkspaceData();
      if (data.opportunity?.id) setSelectedId(data.opportunity.id);
      notify('Opportunity parsed in English & हिंदी and synced to WhatsApp!');
    } catch (err) {
      notify(err.message, true);
    } finally {
      setSearching(false);
    }
  };

  const handleStopScout = async (taskId) => {
    try {
      await apiRequest(`/api/scouts/${taskId}`, { method: 'DELETE' });
      notify(`Stopped scout ${taskId.slice(0, 10)}.`);
      await loadWorkspaceData();
    } catch (err) {
      notify(err.message, true);
    }
  };

  const activeScouts = scouts.filter((s) => s.status === 'ACTIVE');

  return (
    <section className="ws-shell" id="workspace">
      {toast && (
        <div className={`ws-toast ${toast.isError ? 'err' : 'ok'}`}>
          {toast.message}
        </div>
      )}

      <div className="ws-header">
        <div>
          <span className="ws-kicker">OPPTRAX COMMAND WORKSPACE &middot; WHATSAPP CONNECTED</span>
          <h2>+{user?.whatsapp_phone}</h2>
          <div className="ws-status-row">
            <span className={`ws-pill ${integrations.live_scout !== false ? 'on' : ''}`}>
              LiveScout Active
            </span>
            <span className={`ws-pill ${integrations.sarvam_llm || integrations.sarvam || integrations.gemini ? 'on' : ''}`}>
              {integrations.sarvam_llm || integrations.sarvam
                ? 'Sarvam AI Live'
                : integrations.gemini
                ? 'Gemini Live'
                : 'Offline Router'}
            </span>
            <span className={`ws-pill ${integrations.whatsapp_meta || integrations.whatsapp_twilio ? 'on' : ''}`}>
              WhatsApp {integrations.whatsapp_meta ? 'Meta API' : integrations.whatsapp_twilio ? 'Twilio' : 'Live Sandbox Bridge'}
            </span>
          </div>
        </div>

        <div className="ws-controls">
          <div className="ws-control-group">
            <span className="ws-fact-label">Language</span>
            <div className="ws-lang-switch">
              {[
                { id: 'both', label: 'EN + हिंदी' },
                { id: 'en', label: 'EN' },
                { id: 'hi', label: 'हिंदी' },
              ].map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  className={`ws-mini-btn ${user?.preferred_language === opt.id ? 'active' : ''}`}
                  onClick={() => handleLanguageChange(opt.id)}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          <div className="ws-control-group">
            <span className="ws-fact-label">Alerts</span>
            <div className="ws-lang-switch">
              {['instant', 'daily'].map((mode) => (
                <button
                  key={mode}
                  type="button"
                  className={`ws-mini-btn ${user?.digest_mode === mode ? 'active' : ''}`}
                  onClick={() => handleDigestChange(mode)}
                >
                  {mode === 'instant' ? 'Instant' : 'Daily Digest'}
                </button>
              ))}
            </div>
          </div>

          <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-end' }}>
            {waDeepLink && (
              <a href={waDeepLink} target="_blank" rel="noreferrer" className="ws-btn ws-btn-green">
                Open WhatsApp &rarr;
              </a>
            )}
            <button type="button" className="ws-btn ws-btn-muted" onClick={onLogout}>
              Disconnect
            </button>
          </div>
        </div>
      </div>

      <div className="ws-tabs">
        <button
          type="button"
          className={`ws-tab ${activeTab === 'board' ? 'active' : ''}`}
          onClick={() => setActiveTab('board')}
        >
          Opportunities &amp; Search ({opportunities.length})
        </button>
        <button
          type="button"
          className={`ws-tab ${activeTab === 'whatsapp' ? 'active' : ''}`}
          onClick={() => setActiveTab('whatsapp')}
        >
          WhatsApp Live Bridge
        </button>
        <button
          type="button"
          className={`ws-tab ${activeTab === 'profile' ? 'active' : ''}`}
          onClick={() => setActiveTab('profile')}
        >
          Tracked Deadlines &amp; Profile
        </button>
      </div>

      {activeTab === 'board' && (
        <div className="ws-board-layout">
          <div className="ws-search-strip">
            <form onSubmit={handlePersonalizedSearch} className="ws-search-form">
              <input
                type="text"
                className="ws-input"
                placeholder="Ask LiveScout & AI in English or हिंदी (e.g. 'Find AI hackathons & Python internships', 'मेरे लिए रिमोट पायथन इंटर्नशिप खोजो')..."
                value={searchPrompt}
                onChange={(e) => setSearchPrompt(e.target.value)}
                disabled={searching}
              />
              <button type="submit" className="ws-btn ws-btn-green" disabled={searching || !searchPrompt.trim()}>
                {searching ? 'Searching...' : 'Search Live & Deploy Scout'}
              </button>
            </form>
            <div className="ws-search-opts">
              <label className="ws-checkbox-row">
                <input type="checkbox" checked={launchScout} onChange={(e) => setLaunchScout(e.target.checked)} />
                <span>Deploy Continuous LiveScout Monitor</span>
              </label>
              <label className="ws-checkbox-row">
                <input type="checkbox" checked={sendSearchToWa} onChange={(e) => setSendSearchToWa(e.target.checked)} />
                <span>Deliver matches to WhatsApp (EN/HI)</span>
              </label>
            </div>

            <form onSubmit={handleIngest} className="ws-ingest-form">
              <input
                type="text"
                className="ws-input"
                placeholder="Paste any official opportunity URL or text to extract EN + हिंदी brief & track deadline..."
                value={ingestInput}
                onChange={(e) => setIngestInput(e.target.value)}
                disabled={searching}
              />
              <button type="submit" className="ws-btn" disabled={searching || !ingestInput.trim()}>
                Ingest URL / Post
              </button>
            </form>
          </div>

          {activeScouts.length > 0 && (
            <div className="ws-scouts-bar">
              <span className="ws-fact-label">Active Scouts ({activeScouts.length}):</span>
              {activeScouts.map((s) => (
                <div key={s.yutori_task_id} className="ws-scout-chip">
                  <span>{String(s.query_instruction || '').slice(0, 46)}...</span>
                  <button type="button" onClick={() => handleStopScout(s.yutori_task_id)} title="Stop scout">
                    Stop
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="ws-split">
            <div className="ws-panel ws-feed">
              <div className="ws-feed-filters">
                <input
                  type="text"
                  className="ws-input"
                  placeholder="Filter found opportunities..."
                  value={boardFilter}
                  onChange={(e) => setBoardFilter(e.target.value)}
                />
                <label className="ws-checkbox-row">
                  <input type="checkbox" checked={trackedOnly} onChange={(e) => setTrackedOnly(e.target.checked)} />
                  <span>Tracked</span>
                </label>
              </div>

              {opportunities.length === 0 ? (
                <p className="ws-muted-note" style={{ marginTop: '1rem' }}>
                  No opportunities yet. Run a search above or message the WhatsApp bot!
                </p>
              ) : (
                <div className="ws-opp-list">
                  {opportunities.map((opp) => (
                    <button
                      key={opp.id}
                      type="button"
                      className={`ws-opp-item ${selectedId === opp.id ? 'active' : ''}`}
                      onClick={() => setSelectedId(opp.id)}
                    >
                      <div className="ws-badges">
                        <span className="ws-tag">{opp.category}</span>
                        {opp.priority_score != null && (
                          <span className="ws-tag ws-tag-green">{Math.round(opp.priority_score * 100)}%</span>
                        )}
                        {opp.is_tracked && <span className="ws-tag ws-tag-blue">Tracked</span>}
                      </div>
                      <div className="ws-opp-item-title">{opp.title}</div>
                      <div className="ws-opp-meta">
                        {opp.organization} &middot; {opp.deadline_iso || 'Rolling'}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>

            <OpportunityInspector
              opportunity={selectedDetail}
              qaHistory={selectedDetail ? qaHistoryMap[selectedDetail.id] || [] : []}
              preferredLanguage={user?.preferred_language}
              onOpportunityUpdated={async () => {
                await loadWorkspaceData();
                await loadSelectedDetail(selectedId);
              }}
              onQaUpdated={(oid, hist) => setQaHistoryMap((prev) => ({ ...prev, [oid]: hist }))}
              onNotify={notify}
            />
          </div>
        </div>
      )}

      {activeTab === 'whatsapp' && (
        <WhatsAppBridgeTab
          deepLink={waDeepLink}
          onSyncRequired={async () => {
            await loadWorkspaceData();
            await loadSelectedDetail(selectedId);
          }}
          onNotify={notify}
        />
      )}

      {activeTab === 'profile' && (
        <ProfileTab
          key={`${user?.whatsapp_phone || ''}_${JSON.stringify(user?.profile_json || {})}`}
          user={user}
          opportunities={opportunities}
          onUpdated={loadWorkspaceData}
          onNotify={notify}
          onSelectOpp={(oid) => {
            setSelectedId(oid);
            setActiveTab('board');
          }}
        />
      )}
    </section>
  );
}
