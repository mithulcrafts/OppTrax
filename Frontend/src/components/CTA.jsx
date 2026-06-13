import React, { useState } from 'react';

export default function CTA() {
  const [phone, setPhone] = useState('');
  const [buttonText, setButtonText] = useState('Get OTP \u2192');
  const [isSending, setIsSending] = useState(false);
  const [isSent, setIsSent] = useState(false);

  const handleOTP = () => {
    const val = phone.trim();
    if (!val) {
      const el = document.getElementById('phone-cta');
      if (el) el.focus();
      return;
    }
    setIsSending(true);
    setButtonText('Sending...');

    setTimeout(() => {
      setButtonText('OTP sent \u2713');
      setIsSent(true);
    }, 1200);
  };

  const buttonStyle = isSent ? { background: '#ccc' } : {};

  return (
    <section className="cta-section">
      <h2 className="reveal">
        Start finding opportunities
        <br />
        that fit <em style={{ fontStyle: 'normal', color: 'rgba(255,255,255,.3)' }}>you</em>
      </h2>
      <p className="reveal">No app needed. Just WhatsApp.</p>
      <div className="phone-row reveal">
        <input
          id="phone-cta"
          className="phone-input"
          type="tel"
          placeholder="+91 98765 43210"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
        />
        <button
          className="btn-primary"
          onClick={handleOTP}
          disabled={isSending}
          style={buttonStyle}
        >
          {buttonText}
        </button>
      </div>
      <p className="cta-note reveal">
        We&rsquo;ll send a one-time password to your WhatsApp number.
      </p>
    </section>
  );
}
