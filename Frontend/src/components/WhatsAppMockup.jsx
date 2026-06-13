import React, { useState, useEffect } from 'react';

export default function WhatsAppMockup() {
  const [messages, setMessages] = useState([]);
  const [isTyping, setIsTyping] = useState(false);
  const [timeStr, setTimeStr] = useState('');

  useEffect(() => {
    // Generate time string on component mount
    const now = new Date();
    const hh = now.getHours();
    const mm = String(now.getMinutes()).padStart(2, '0');
    const ampm = hh >= 12 ? 'PM' : 'AM';
    const hr = hh % 12 || 12;
    setTimeStr(`${hr}:${mm} ${ampm}`);

    const sequence = [
      {
        action: 'message',
        data: { type: 'in', text: 'Hi! What opportunities are you looking for?' },
        delay: 700,
      },
      {
        action: 'message',
        data: { type: 'out', text: 'AI internships in Hyderabad' },
        delay: 1600,
      },
      {
        action: 'typing',
        data: true,
        delay: 2500,
      },
      {
        action: 'typing',
        data: false,
        delay: 3700,
      },
      {
        action: 'message',
        data: {
          type: 'in',
          text: 'Found 4 opportunities for you ✦\n\nGoogle STEP Internship\n₹80k/mo · Hyderabad · Aug 15',
          chips: ['Interested', 'Track', 'Ask AI'],
        },
        delay: 3700,
      },
    ];

    const timers = [];

    sequence.forEach((step) => {
      const timer = setTimeout(() => {
        if (step.action === 'message') {
          setMessages((prev) => [...prev, step.data]);
        } else if (step.action === 'typing') {
          setIsTyping(step.data);
        }
      }, step.delay);
      timers.push(timer);
    });

    return () => {
      timers.forEach((t) => clearTimeout(t));
    };
  }, []);

  return (
    <div className="wa-wrap">
      <div className="wa-float">
        <div className="wa-preview">
          <div className="wa-header">
            <div className="wa-avatar">OT</div>
            <div style={{ textAlign: 'left' }}>
              <div className="wa-name">OppTrax</div>
              <div className="wa-online">
                <div className="wa-online-dot"></div>online
              </div>
            </div>
          </div>
          <div className="wa-body" id="wa-body">
            {messages.map((m, idx) => (
              <div
                key={idx}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  animation: 'msgIn .35s cubic-bezier(.22,1,.36,1) both',
                  alignSelf: m.type === 'out' ? 'flex-end' : 'flex-start',
                }}
              >
                <div className={m.type === 'out' ? 'msg msg-out' : 'msg msg-in'}>
                  <div style={{ padding: '8px 12px 6px' }}>
                    <div style={{ whiteSpace: 'pre-line' }}>{m.text}</div>
                    <div className={m.type === 'out' ? 'msg-time-r' : 'msg-time'}>
                      {timeStr}
                    </div>
                  </div>
                  {m.chips && (
                    <div className="wa-action-buttons">
                      {m.chips.map((chip, cIdx) => (
                        <div
                          key={cIdx}
                          className="wa-action-btn"
                          style={{
                            animation: `chipIn .3s ${cIdx * 0.1 + 0.15}s both`,
                          }}
                        >
                          {chip}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {isTyping && (
              <div className="typing" id="typing-indicator">
                <span></span>
                <span></span>
                <span></span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
