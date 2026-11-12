import { useState } from 'react';
import { apiRequest } from '../lib/api';

export default function ProfileTab({ user, opportunities, onUpdated, onNotify, onSelectOpp }) {
  const prof = user?.profile_json || {};
  const [skillsText, setSkillsText] = useState((prof.skills || []).join(', '));
  const [rolesText, setRolesText] = useState((prof.target_roles || []).join(', '));
  const [locationsText, setLocationsText] = useState((prof.preferred_locations || []).join(', '));
  const [summaryText, setSummaryText] = useState(prof.summary || '');
  const [rawBio, setRawBio] = useState('');
  const [saving, setSaving] = useState(false);

  const trackedOpps = (opportunities || []).filter((o) => o.is_tracked);

  const handleSaveProfile = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      await apiRequest('/api/user/profile', {
        method: 'PUT',
        body: JSON.stringify({
          skills: skillsText.split(',').map((s) => s.trim()).filter(Boolean),
          target_roles: rolesText.split(',').map((s) => s.trim()).filter(Boolean),
          preferred_locations: locationsText.split(',').map((s) => s.trim()).filter(Boolean),
          summary: summaryText,
          raw_resume_text: rawBio.trim() || undefined,
        }),
      });
      setRawBio('');
      onNotify('Personalized profile saved! Future LiveScout searches & scores will tailor to your profile.');
      await onUpdated();
    } catch (err) {
      onNotify(err.message, true);
    } finally {
      setSaving(false);
    }
  };

  const handleFileUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setSaving(true);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('notify_whatsapp', 'true');
      await apiRequest('/api/user/resume', { method: 'POST', body: form });
      onNotify('Resume parsed and synced to WhatsApp profile!');
      await onUpdated();
    } catch (err) {
      onNotify(err.message, true);
    } finally {
      setSaving(false);
    }
  };

  const handleReminderSweep = async () => {
    setSaving(true);
    try {
      const data = await apiRequest('/api/reminders/check', { method: 'POST' });
      onNotify(`Deadline sweep finished: ${data.reminders_sent || 0} reminder alert(s) triggered.`);
      await onUpdated();
    } catch (err) {
      onNotify(err.message, true);
    } finally {
      setSaving(false);
    }
  };

  const handleSendDigest = async () => {
    setSaving(true);
    try {
      const data = await apiRequest('/api/digest/send', { method: 'POST' });
      onNotify(`Daily digest dispatched (${data.digested_count || 0} item(s)).`);
      await onUpdated();
    } catch (err) {
      onNotify(err.message, true);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="ws-profile-grid">
      <div className="ws-panel">
        <h3>Personalized Profile &amp; Resume</h3>
        <p className="ws-muted-note" style={{ marginBottom: '1rem' }}>
          LiveScout &amp; Sarvam/Gemini AI use your skills, target roles, and preferred locations to rank and customize opportunities.
        </p>

        <div className="ws-upload-box">
          <label className="ws-btn" style={{ cursor: 'pointer', display: 'inline-block' }}>
            Upload PDF / TXT Resume
            <input type="file" accept=".pdf,.txt" hidden onChange={handleFileUpload} disabled={saving} />
          </label>
        </div>

        <form onSubmit={handleSaveProfile} className="ws-profile-form">
          <label>
            <span>Skills (comma separated)</span>
            <input
              type="text"
              className="ws-input"
              placeholder="Python, React, FastAPI, Machine Learning"
              value={skillsText}
              onChange={(e) => setSkillsText(e.target.value)}
            />
          </label>
          <label>
            <span>Target Roles / Opportunity Types</span>
            <input
              type="text"
              className="ws-input"
              placeholder="Software Engineering Intern, AI Hackathons, Fellowships"
              value={rolesText}
              onChange={(e) => setRolesText(e.target.value)}
            />
          </label>
          <label>
            <span>Preferred Locations</span>
            <input
              type="text"
              className="ws-input"
              placeholder="Remote, Bengaluru, India"
              value={locationsText}
              onChange={(e) => setLocationsText(e.target.value)}
            />
          </label>
          <label>
            <span>Career Summary</span>
            <textarea
              className="ws-textarea"
              rows={3}
              placeholder="Brief overview of your background..."
              value={summaryText}
              onChange={(e) => setSummaryText(e.target.value)}
            />
          </label>
          <label>
            <span>Or Paste Resume / Bio Text to Auto-Extract</span>
            <textarea
              className="ws-textarea"
              rows={3}
              placeholder="Paste full resume text here to auto-populate structured skills & roles..."
              value={rawBio}
              onChange={(e) => setRawBio(e.target.value)}
            />
          </label>
          <button type="submit" className="ws-btn ws-btn-green" disabled={saving}>
            {saving ? 'Saving...' : 'Save Personalization Profile'}
          </button>
        </form>
      </div>

      <div className="ws-panel">
        <div className="ws-bridge-header" style={{ marginBottom: '1rem' }}>
          <div>
            <h3>Tracked Deadlines &amp; Reminders</h3>
            <p className="ws-muted-note">Automated 48h, 24h &amp; 6h pre-deadline WhatsApp reminders.</p>
          </div>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <button type="button" className="ws-btn" disabled={saving} onClick={handleReminderSweep}>
              Check Deadlines Now
            </button>
            <button type="button" className="ws-btn" disabled={saving} onClick={handleSendDigest}>
              Send Digest
            </button>
          </div>
        </div>

        {trackedOpps.length === 0 ? (
          <p className="ws-muted-note">No tracked opportunities yet. Click "Save &amp; Track Deadline" on any opportunity!</p>
        ) : (
          <div className="ws-tracked-list">
            {trackedOpps.map((opp) => (
              <div key={opp.id} className="ws-tracked-item">
                <div>
                  <strong>{opp.title}</strong>
                  <div className="ws-opp-meta">
                    Deadline: {opp.tracking?.deadline_iso || opp.deadline_iso || 'Not specified'} &middot; Status:{' '}
                    <code>{opp.tracking?.status || 'interested'}</code>
                  </div>
                  <div className="ws-opp-meta">
                    Reminders sent:{' '}
                    {(opp.tracking?.reminders_sent || []).length > 0
                      ? opp.tracking.reminders_sent.join(', ')
                      : 'Scheduled (48h / 24h / 6h prior)'}
                  </div>
                </div>
                <button type="button" className="ws-btn" onClick={() => onSelectOpp(opp.id)}>
                  Inspect &rarr;
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
