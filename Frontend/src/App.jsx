import React from 'react';
import Navbar from './components/Navbar';
import Hero from './components/Hero';
import ProblemStatement from './components/ProblemStatement';
import HowItWorks from './components/HowItWorks';
import OpportunityTracking from './components/OpportunityTracking';
import Features from './components/Features';
import CTA from './components/CTA';
import Footer from './components/Footer';
import useIntersectionObserver from './hooks/useIntersectionObserver';

function App() {
  // Activate scroll reveal observer
  useIntersectionObserver();

  return (
    <>
      <Navbar />
      <Hero />
      <ProblemStatement />
      <HowItWorks />
      <OpportunityTracking />
      <Features />
      <CTA />
      <Footer />
    </>
  );
}

export default App;
