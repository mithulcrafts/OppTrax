import { useState, useEffect, useRef } from 'react';
import { apiRequest, setStoredSessionToken } from '../lib/api';

const LANG_OPTIONS = [
  { id: 'both', label: 'EN + हिंदी (Both)' },
  { id: 'en', label: 'English' },
  { id: 'hi', label: 'हिंदी (Hindi)' },
];

export default function CTA({ onAuthenticated }) {
  const [step, setStep] = useState('phone'); // 'phone' | 'otp' | 'success'
  const [phone, setPhone] = useState('');
  const [otp, setOtp] = useState('');
  const [preferredLanguage, setPreferredLanguage] = useState('both');
  const [deepLink, setDeepLink] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [warning, setWarning] = useState('');
  const [resendCountdown, setResendCountdown] = useState(0);
  const timerRef = useRef(null);

  useEffect(() => {
    if (resendCountdown > 0) {
      timerRef.current = setTimeout(() => {
        setResendCountdown((prev) => prev - 1);
      }, 1000);
    }
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [resendCountdown]);

  const handleSendOTP = async (e) => {
    if (e) e.preventDefault();
    const cleanPhone = phone.trim();
    if (!cleanPhone) {
      setError('Please enter your WhatsApp phone number.');
      const el = document.getElementById('phone-cta');
      if (el) el.focus();
      return;
    }

    setLoading(true);
    setError('');
    setWarning('');

    try {
      const data = await apiRequest('/api/auth/send-otp', {
        method: 'POST',
        body: JSON.stringify({ phone: cleanPhone }),
      });

      if (data.whatsapp_deep_link) {
        setDeepLink(data.whatsapp_deep_link);
      }

      if (data.dev_otp) {
        setOtp(data.dev_otp);
      }

      if (data.status === 'sandbox_error' || data.status === 'sandbox_mode') {
        setWarning('Instant verification active — your 6-digit code is filled below.');
      }
      setStep('otp');
      setResendCountdown(30);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyOTP = async (e) => {
    if (e) e.preventDefault();
    const cleanOtp = otp.trim();
    if (cleanOtp.length !== 6) {
      setError('OTP must be a 6-digit code.');
      return;
    }

    setLoading(true);
    setError('');

    try {
      const data = await apiRequest('/api/auth/verify-otp', {
        method: 'POST',
        body: JSON.stringify({
          phone: phone.trim(),
          otp: cleanOtp,
          preferred_language: preferredLanguage,
        }),
      });

      setStoredSessionToken(data.session_token);
      if (data.whatsapp_deep_link) {
        setDeepLink(data.whatsapp_deep_link);
      }
      setStep('success');
      setWarning('');
      if (onAuthenticated) {
        onAuthenticated(data.session_token, data.user, data.whatsapp_deep_link);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleResend = () => {
    if (resendCountdown > 0) return;
    handleSendOTP();
  };

  const handleGoBack = () => {
    setStep('phone');
    setOtp('');
    setError('');
    setWarning('');
  };

  return (
    <section className="cta-section" id="connect">
      <style>{`
        .cta-section {
          position: relative;
          z-index: 1;
        }
        .cta-card {
          max-width: 480px;
          margin: 0 auto;
          background: rgba(255, 255, 255, 0.015);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 16px;
          padding: 2.75rem 2.25rem;
          backdrop-filter: blur(8px);
          -webkit-backdrop-filter: blur(8px);
          box-shadow: 0 24px 60px -15px rgba(0, 0, 0, 0.7);
        }
        .lang-pills {
          display: flex;
          gap: 8px;
          justify-content: center;
          flex-wrap: wrap;
          margin-bottom: 1rem;
        }
        .lang-pill {
          background: rgba(255, 255, 255, 0.04);
          border: 1px solid rgba(255, 255, 255, 0.14);
          color: rgba(255, 255, 255, 0.68);
          padding: 7px 12px;
          border-radius: 8px;
          font-size: 12px;
          cursor: pointer;
          transition: all 0.2s;
        }
        .lang-pill.active {
          background: rgba(0, 168, 132, 0.18);
          border-color: #00a884;
          color: #fff;
        }
        .otp-input-container {
          position: relative;
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 12px;
          width: 100%;
          margin: 1.5rem 0;
        }
        .otp-digits-input {
          background: #0d0d0d;
          border: 0.5px solid rgba(255, 255, 255, .18);
          color: #fff;
          padding: 12px;
          border-radius: 7px;
          font-size: 20px;
          font-weight: 600;
          width: 180px;
          text-align: center;
          letter-spacing: 6px;
          outline: none;
          transition: all 0.2s;
        }
        .otp-digits-input:focus {
          border-color: rgba(255, 255, 255, 0.6);
          box-shadow: 0 0 10px rgba(255, 255, 255, 0.05);
        }
        .error-banner {
          background: rgba(239, 68, 68, 0.08);
          border: 1.5px solid rgba(239, 68, 68, 0.25);
          color: #ef4444;
          padding: 10px 14px;
          border-radius: 8px;
          font-size: 13px;
          margin-bottom: 1.5rem;
          text-align: left;
          display: flex;
          align-items: center;
          gap: 8px;
          animation: msgIn 0.3s ease both;
        }
        .warning-banner {
          background: rgba(0, 168, 132, 0.1);
          border: 1.5px solid rgba(0, 168, 132, 0.35);
          color: #6ee7b7;
          padding: 10px 14px;
          border-radius: 8px;
          font-size: 13px;
          margin-bottom: 1.5rem;
          text-align: left;
          display: flex;
          flex-direction: column;
          gap: 6px;
          animation: msgIn 0.3s ease both;
          line-height: 1.45;
        }
        .spinner {
          display: inline-block;
          width: 16px;
          height: 16px;
          border: 2px solid rgba(0,0,0,0.1);
          border-left-color: #000;
          border-radius: 50%;
          animation: spin 0.8s linear infinite;
          vertical-align: middle;
          margin-right: 8px;
        }
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
        .back-link {
          background: none;
          border: none;
          color: rgba(255, 255, 255, 0.45);
          font-size: 12px;
          cursor: pointer;
          transition: color 0.2s;
          margin-top: 1rem;
          text-decoration: underline;
        }
        .back-link:hover {
          color: #fff;
        }
        .resend-btn {
          background: none;
          border: none;
          color: #00a884;
          font-size: 13px;
          font-weight: 500;
          cursor: pointer;
          transition: opacity 0.2s;
        }
        .resend-btn:disabled {
          color: rgba(255, 255, 255, 0.3);
          cursor: not-allowed;
        }
        .success-checkmark {
          width: 60px;
          height: 60px;
          background: rgba(0, 168, 132, 0.1);
          border: 2px solid #00a884;
          color: #00a884;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 28px;
          margin: 0 auto 1.5rem;
          animation: scaleUp 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275) both;
        }
        @keyframes scaleUp {
          from { transform: scale(0.6); opacity: 0; }
          to { transform: scale(1); opacity: 1; }
        }
      `}</style>

      <div className="cta-card reveal visible">
        {step === 'phone' && (
          <form onSubmit={handleSendOTP}>
            <h2 style={{ fontSize: '28px', marginBottom: '0.75rem', letterSpacing: '-0.5px' }}>
              Connect Your WhatsApp
            </h2>
            <p style={{ color: 'rgba(255,255,255,0.5)', fontSize: '13.5px', marginBottom: '1.25rem', lineHeight: '1.5' }}>
              Verify once to launch personalized LiveScout feeds, bilingual EN/हिंदी alerts, deadline reminders &amp; AI Q&amp;A.
            </p>

            <div className="lang-pills" aria-label="Preferred Alert Language">
              {LANG_OPTIONS.map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  className={`lang-pill ${preferredLanguage === opt.id ? 'active' : ''}`}
                  onClick={() => setPreferredLanguage(opt.id)}
                >
                  {opt.label}
                </button>
              ))}
            </div>

            {error && (
              <div className="error-banner">
                <span>{error}</span>
              </div>
            )}

            <div className="phone-row" style={{ display: 'flex', flexDirection: 'column', gap: '12px', alignItems: 'stretch' }}>
              <input
                id="phone-cta"
                className="phone-input"
                type="tel"
                placeholder="+91 98765 43210"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                disabled={loading}
                style={{ width: '100%', padding: '13px 16px', fontSize: '15px' }}
              />
              <button
                type="submit"
                className="btn-primary"
                disabled={loading || !phone.trim()}
                style={{ width: '100%', padding: '13px 16px', fontSize: '15px', position: 'relative' }}
              >
                {loading ? (
                  <>
                    <span className="spinner"></span>
                    Sending OTP...
                  </>
                ) : (
                  'Get OTP \u2192'
                )}
              </button>
            </div>
            <p className="cta-note" style={{ fontSize: '11px', color: 'rgba(255,255,255,0.32)', marginTop: '1.25rem' }}>
              Enter country code (e.g. +91 for India). Works with live Meta/Twilio WhatsApp or local sandbox bridge.
            </p>
          </form>
        )}

        {step === 'otp' && (
          <form onSubmit={handleVerifyOTP}>
            <h2 style={{ fontSize: '28px', marginBottom: '0.75rem', letterSpacing: '-0.5px' }}>
              Enter Verification Code
            </h2>
            <p style={{ color: 'rgba(255,255,255,0.5)', fontSize: '13.5px', marginBottom: '1.25rem', lineHeight: '1.5' }}>
              Enter the 6-digit code generated for <strong>{phone}</strong>.
            </p>

            {warning && (
              <div className="warning-banner">
                <span><strong>Quick Verification Ready:</strong> {warning}</span>
                {deepLink && (
                  <a
                    href={deepLink}
                    target="_blank"
                    rel="noreferrer"
                    style={{ color: '#25D366', textDecoration: 'underline', fontWeight: 500 }}
                  >
                    Or tap here to verify via WhatsApp &rarr;
                  </a>
                )}
              </div>
            )}

            {error && (
              <div className="error-banner">
                <span>{error}</span>
              </div>
            )}

            <div className="otp-input-container">
              <input
                className="otp-digits-input"
                type="text"
                maxLength={6}
                placeholder="000000"
                value={otp}
                onChange={(e) => setOtp(e.target.value.replace(/\D/g, ''))}
                disabled={loading}
                autoFocus
              />
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', width: '100%', marginTop: '1.5rem' }}>
              <button
                type="submit"
                className="btn-primary"
                disabled={loading || otp.length !== 6}
                style={{ width: '100%', padding: '13px 16px', fontSize: '15px' }}
              >
                {loading ? (
                  <>
                    <span className="spinner"></span>
                    Verifying...
                  </>
                ) : (
                  'Confirm & Activate Workspace'
                )}
              </button>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '1.5rem', fontSize: '13px' }}>
              <button type="button" className="back-link" onClick={handleGoBack} disabled={loading} style={{ margin: 0, padding: 0 }}>
                &larr; Change number
              </button>
              <div>
                {resendCountdown > 0 ? (
                  <span style={{ color: 'rgba(255,255,255,0.3)' }}>Resend in {resendCountdown}s</span>
                ) : (
                  <button type="button" className="resend-btn" onClick={handleResend} disabled={loading}>
                    Resend OTP
                  </button>
                )}
              </div>
            </div>
          </form>
        )}

        {step === 'success' && (
          <div style={{ textAlign: 'center' }}>
            <div className="success-checkmark">&#10003;</div>
            <h2 style={{ fontSize: '26px', marginBottom: '0.75rem', letterSpacing: '-0.5px', color: '#fff' }}>
              WhatsApp Connected!
            </h2>
            <p style={{ color: 'rgba(255,255,255,0.65)', fontSize: '14px', lineHeight: '1.6', marginBottom: '1.5rem' }}>
              Your workspace and bilingual WhatsApp bot are now synchronized.
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {deepLink && (
                <a
                  href={deepLink}
                  target="_blank"
                  rel="noreferrer"
                  className="btn-primary"
                  style={{ display: 'inline-block', textDecoration: 'none', padding: '11px 20px' }}
                >
                  Open Chat in WhatsApp &rarr;
                </a>
              )}
              <button
                onClick={handleGoBack}
                className="btn-ghost"
                style={{ padding: '10px 24px', fontSize: '13.5px' }}
              >
                Verify another number
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
