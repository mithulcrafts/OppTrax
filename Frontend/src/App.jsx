import { useEffect, useState } from 'react';
import Navbar from './components/Navbar';
import Hero from './components/Hero';
import ProblemStatement from './components/ProblemStatement';
import HowItWorks from './components/HowItWorks';
import OpportunityTracking from './components/OpportunityTracking';
import Features from './components/Features';
import CTA from './components/CTA';
import Dashboard from './components/Dashboard';
import Footer from './components/Footer';
import useIntersectionObserver from './hooks/useIntersectionObserver';
import { apiRequest, getStoredSessionToken, setStoredSessionToken } from './lib/api';

function App() {
  useIntersectionObserver();

  const [sessionUser, setSessionUser] = useState(null);
  const [deepLink, setDeepLink] = useState('');

  useEffect(() => {
    const token = getStoredSessionToken();
    if (!token) return;
    apiRequest('/api/auth/session')
      .then((data) => {
        setSessionUser(data.user);
        setDeepLink(data.whatsapp_deep_link || '');
      })
      .catch(() => {
        setStoredSessionToken('');
        setSessionUser(null);
      });
  }, []);

  const scrollToWorkspace = () => {
    const el = document.getElementById('workspace');
    if (el) el.scrollIntoView({ behavior: 'smooth' });
  };

  const handleAuthenticated = (_token, user, link) => {
    setSessionUser(user);
    if (link) setDeepLink(link);
    setTimeout(scrollToWorkspace, 120);
  };

  const handleLogout = () => {
    setStoredSessionToken('');
    setSessionUser(null);
    setDeepLink('');
  };

  return (
    <>
      <Navbar
        sessionUser={sessionUser}
        onOpenWorkspace={scrollToWorkspace}
        onLogout={handleLogout}
      />

      {sessionUser ? (
        <Dashboard
          sessionUser={sessionUser}
          deepLink={deepLink}
          onUserUpdated={setSessionUser}
          onLogout={handleLogout}
        />
      ) : (
        <>
          <Hero isAuthenticated={false} onOpenWorkspace={scrollToWorkspace} />
          <ProblemStatement />
          <HowItWorks />
          <OpportunityTracking />
          <Features />
          <CTA onAuthenticated={handleAuthenticated} />
        </>
      )}

      {sessionUser && (
        <>
          <HowItWorks />
          <Features />
        </>
      )}

      <Footer />
    </>
  );
}

export default App;
