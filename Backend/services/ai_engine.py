import json
from google.genai import types
from config import gemini_client, GATEKEEPER_MODEL

# =====================================================================
#  DUAL-ROUTING GATEKEEPER ENGINE
# =====================================================================

def analyze_intent(raw_text: str, user_profile: dict = None) -> dict:
    print(f"[GATEKEEPER] Analyzing raw intent: {raw_text[:50]}...", flush=True)
    profile_context = f"\nUser Profile Data Matrix: {user_profile}" if user_profile else "\nUser Profile Status: Anonymous"
    
    prompt = f"""You are the Dual-Routing Intent Center for OppTrax, an autonomous web-agent platform.
    The user has just texted: "{raw_text}"
    {profile_context}
    
    Your job is to strictly categorize this input into ONE of these 4 operational tracks:
    1. "CAREER": The user is asking to track/find jobs, internships, hackathons, fellowships, or certificates.
    2. "GENERAL": The user is asking to track ANYTHING ELSE (e.g. flight drops, cricket scores, amazon prices, tennis slots).
    3. "STOP": The user is asking to terminate, stop, or delete a running task.
    4. "INVALID": The input is pure gibberish or un-actionable.
    
    If CAREER: 
    Generate a highly specific, exhaustive search query (`refined_instruction`) that will be sent to our headless web crawler. 
    It MUST explicitly instruct the crawler to extract everything requested and, critically, TO EXTRACT THE CLOSING DEADLINE AND APPLICATION URL.
    
    If GENERAL:
    Generate a highly specific search query (`refined_instruction`) that will be sent to our headless web crawler.
    It MUST explicitly instruct the crawler to extract EXACTLY what the user requested. DO NOT ask for deadlines or application URLs unless they are relevant to the user's specific request.
    
    If INVALID or STOP:
    `refined_instruction` should be null.
    Provide a `reply` string that will be sent directly to the user's WhatsApp acknowledging the error or action.
    
    Return a STRICT JSON object in this format:
    {{
        "intent": "CAREER | GENERAL | STOP | INVALID",
        "refined_instruction": "string or null",
        "category": "CAREER | GENERAL",
        "reply": "string or null"
    }}
    """
    try:
        response = gemini_client.models.generate_content(
            model=GATEKEEPER_MODEL, 
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        return json.loads(response.text.strip())
    except Exception as e:
        print(f"[GATEKEEPER] Error: {e}", flush=True)
        return {"intent": "INVALID", "reply": "My routing engine encountered an anomaly. Please try again."}

def build_user_profile(raw_text: str):
    prompt = f"""You are an expert tech recruiter and career counselor. Analyze this resume text and extract comprehensive profile information into JSON format.
    Extract EVERYTHING relevant to build a deep, personalized profile matrix.
    
    Resume Text:
    {raw_text}
    
    Format EXACTLY as this STRICT JSON:
    {{
        "skills": ["Python", "React", "AWS", etc],
        "experience_level": "Student/Junior/Mid/Senior",
        "education": ["B.Tech Computer Science from XYZ University", etc],
        "experience": ["Software Engineer Intern at ABC Corp (May 2023 - Aug 2023) - Built backend APIs", etc],
        "project_themes": ["Web3", "AI/ML", "E-Commerce Backend", etc],
        "interests": ["Frontend", "Backend", "AI", "Mobile", etc],
        "summary": "A comprehensive 3-4 sentence professional overview of their technical strengths, background, and specific domain expertise."
    }}"""
    try:
        res = gemini_client.models.generate_content(
            model=GATEKEEPER_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        return json.loads(res.text.strip())
    except Exception as e:
        print(f"Error building user profile: {e}")
        return None

def enrich_findings_with_websearch(findings_list: list) -> str:
    if not findings_list:
        return "No findings to enrich."
    
    findings_context = "\n".join([f"- {f.get('text', '')}" for f in findings_list])
    
    prompt = f"""
    You are OppTrax, an elite autonomous research agent. 
    I have just scraped the following raw findings from the web:
    {findings_context}
    
    Your task:
    1. Read through these findings.
    2. USE GOOGLE SEARCH to research the companies, events, or items mentioned in the findings to find MISSING but highly valuable context (e.g. Is the company legitimate? What is the exact stipend/prize? Are there user reviews? What are the exact deadlines if missing?).
    3. Synthesize the raw findings + your web research into a clean, highly readable summary.
    4. MUST INCLUDE: You must provide a direct URL link for every single finding so the user can apply or learn more. Do not miss this.
    5. Formatting: Format your final response entirely for WhatsApp (use *bold* for headings, • for bullets). Do not use markdown headers (#) or HTML.
    6. CRITICAL: NEVER include conversational filler like "Here is your summary" or "I have researched this". Start immediately with the first bullet point.
    
    Deliver the final WhatsApp-ready message directly.
    """
    try:
        res = gemini_client.models.generate_content(
            model=GATEKEEPER_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}]
            )
        )
        return res.text.strip()
    except Exception as e:
        print(f"Error enriching findings: {e}", flush=True)
        return "Error enriching findings. Fallback raw data:\n\n" + findings_context
