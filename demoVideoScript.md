"This is Provenance Guard, a backend that classifies text as AI-generated, human-written, or uncertain, using two independent signals."

"I combine an LLM-based signal from Groq — which judges tone and coherence — with a stylometric signal, pure Python stats like sentence-length variance and vocabulary diversity. I weight the LLM signal higher, 60/40, since it's the stronger standalone predictor."

"Here's a submission — you can see the confidence score, the attribution, and the transparency label text that would be shown to a reader."

"If a creator disagrees, they can appeal — this flips the status to under_review and logs the appeal alongside the original decision."

"The submission endpoint is rate-limited — 10 per minute, 100 per day — to prevent a script from flooding the classifier. I verified this separately, temporarily lowering the limit to catch it on camera, and confirmed it returns 429 once the limit is hit." 

"Every submission and appeal gets written to a structured audit log, capturing both signal scores and the final decision."