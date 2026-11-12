import { useCallback, useEffect, useState } from 'react';
import { apiRequest } from '../lib/api';

const QUICK_COMMANDS = [
  { label: 'help', text: 'help' },
  { label: 'saved board', text: 'saved' },
  { label: 'Find AI Internships', text: 'Find remote AI and Python internships matching my skills' },
  { label: 'हिंदी खोज (Search HI)', text: 'मेरे लिए भारत में रिमोट पायथन इंटर्नशिप खोजो' },
  { label: 'lang hi', text: 'lang hi' },
  { label: 'lang both', text: 'lang both' },
];

export default function WhatsAppBridgeTab({ deepLink, onSyncRequired, onNotify }) {
  const [messages, setMessages] = useState([]);
  const [composer, setComposer] = useState('');
  const [sending, setSending] = useState(false);

  const loadMessages = useCallback(async () => {
    try {
      const data = await apiRequest('/api/whatsapp/messages?limit=80');
      setMessages(data.messages || []);
    } catch {
      // Ignore transient poll errors
    }
  }, []);

  useEffect(() => {
    let ignore = false;
    apiRequest('/api/whatsapp/messages?limit=80')
      .then((data) => {
        if (!ignore) setMessages(data.messages || []);
      })
      .catch(() => {});
    const timer = setInterval(() => {
      apiRequest('/api/whatsapp/messages?limit=80')
        .then((data) => {
          if (!ignore) setMessages(data.messages || []);
        })
        .catch(() => {});
    }, 5000);
    return () => {
      ignore = true;
      clearInterval(timer);
    };
  }, []);

  const sendWhatsAppPayload = async ({ text = null, interactive_id = null }) => {
    setSending(true);
    try {
      await apiRequest('/api/whatsapp/simulate', {
        method: 'POST',
        body: JSON.stringify({ text, interactive_id }),
      });
      if (text !== null) {
        setComposer('');
      }
      await loadMessages();
      if (onSyncRequired) {
        await onSyncRequired();
      }
    } catch (err) {
      onNotify(err.message, true);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="ws-panel ws-bridge-panel">
      <div className="ws-bridge-header">
        <div>
          <h3>Live WhatsApp Bridge &amp; Bot Console</h3>
          <p className="ws-muted-note">
            Every alert, bilingual card, deadline reminder, and Q&amp;A sent via WhatsApp appears here in real time. Click interactive buttons directly or chat below!
          </p>
        </div>
        <div className="ws-bridge-actions">
          {deepLink && (
            <a href={deepLink} target="_blank" rel="noreferrer" className="ws-btn ws-btn-green">
              Open Real WhatsApp &rarr;
            </a>
          )}
          <button type="button" className="ws-btn" onClick={loadMessages}>
            Refresh
          </button>
        </div>
      </div>

      <div className="ws-qa-starters">
        {QUICK_COMMANDS.map((cmd) => (
          <button
            key={cmd.label}
            type="button"
            className="ws-chip"
            disabled={sending}
            onClick={() => sendWhatsAppPayload({ text: cmd.text })}
          >
            {cmd.label}
          </button>
        ))}
      </div>

      <div className="ws-wa-log">
        {messages.length === 0 ? (
          <p className="ws-muted-note">No WhatsApp messages logged yet. Send a message below to test the bilingual agent!</p>
        ) : (
          messages.map((msg, idx) => (
            <div key={msg._id || idx} className={`ws-wa-bubble ${msg.direction === 'inbound' ? 'inbound' : 'outbound'}`}>
              <div className="ws-wa-meta">
                <span>{msg.direction === 'inbound' ? 'You -> Bot' : 'OppTrax Bot -> You'}</span>
                <span>{msg.delivery_status}</span>
              </div>
              <pre className="ws-pre">{msg.text}</pre>
              {Array.isArray(msg.actions) && msg.actions.length > 0 && (
                <div className="ws-wa-buttons">
                  {msg.actions.map((act) => (
                    <button
                      key={act.id}
                      type="button"
                      className="ws-wa-action-btn"
                      disabled={sending}
                      onClick={() => sendWhatsAppPayload({ interactive_id: act.id })}
                    >
                      {act.title}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))
        )}
      </div>

      <form
        className="ws-qa-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (composer.trim()) {
            sendWhatsAppPayload({ text: composer.trim() });
          }
        }}
      >
        <input
          type="text"
          className="ws-input"
          placeholder="Message WhatsApp Bot (English, हिंदी, URL, 'help', 'saved', 'details <id>', 'lang hi')..."
          value={composer}
          onChange={(e) => setComposer(e.target.value)}
          disabled={sending}
        />
        <button type="submit" className="ws-btn ws-btn-green" disabled={sending || !composer.trim()}>
          {sending ? 'Processing...' : 'Send to Bot'}
        </button>
      </form>
    </div>
  );
}
