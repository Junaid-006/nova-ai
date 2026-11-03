"""Shared visual theme for the Nova AI Streamlit app."""

APP_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');

:root {
  color-scheme: dark;
  --nova-bg: #101113;
  --nova-panel: #17191d;
  --nova-panel-hover: #202329;
  --nova-border: rgba(255,255,255,.085);
  --nova-muted: #9298a3;
  --nova-text: #f2f3f5;
  --nova-accent: #a78bfa;
  --nova-accent-2: #67e8d2;
}

html, body, [class*="css"] {
  font-family: 'DM Sans', sans-serif;
}

.stApp {
  background:
    radial-gradient(ellipse at 75% -15%, rgba(116, 87, 190, .12), transparent 40%),
    var(--nova-bg);
  color: var(--nova-text);
}

[data-testid="stHeader"] { background: transparent; }
[data-testid="stToolbar"] { right: 1.5rem; }
[data-testid="stSidebar"] {
  background: #141518;
  border-right: 1px solid var(--nova-border);
}
[data-testid="stSidebar"] > div:first-child { padding-top: 1rem; }

.block-container {
  max-width: 930px;
  padding-top: 2rem;
  padding-bottom: 7rem;
}

.brand {
  display: flex;
  align-items: center;
  gap: .75rem;
  margin: .25rem 0 1.5rem;
}
.brand-mark {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  border: 1px solid rgba(167,139,250,.35);
  border-radius: 14px;
  background: linear-gradient(145deg, rgba(167,139,250,.22), rgba(103,232,210,.08));
  color: #d7ccff;
  font-size: 1.25rem;
  box-shadow: 0 8px 28px rgba(111,78,194,.13);
}
.brand-name {
  font: 700 1.12rem 'Space Grotesk', sans-serif;
  letter-spacing: -.035em;
  color: #f7f5ff;
}
.brand-caption {
  color: var(--nova-muted);
  font-size: .73rem;
  margin-top: .12rem;
}

.section-label {
  color: #8d929c;
  font-size: .68rem;
  font-weight: 700;
  letter-spacing: .12em;
  text-transform: uppercase;
  margin: 1.3rem .15rem .55rem;
}
.connection {
  display: flex;
  align-items: center;
  gap: .5rem;
  margin: 1rem .15rem;
  color: #a6abb4;
  font-size: .78rem;
}
.connection-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #f87171;
}
.connection-dot.online {
  background: #5ee0ae;
  box-shadow: 0 0 10px rgba(94,224,174,.6);
}

.welcome {
  min-height: 48vh;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 2rem 0 1.3rem;
}
.eyebrow {
  color: var(--nova-accent-2);
  text-transform: uppercase;
  letter-spacing: .16em;
  font-size: .72rem;
  font-weight: 700;
}
.welcome h1 {
  font: 600 clamp(2.6rem, 6vw, 4.1rem)/1.08 'Space Grotesk', sans-serif;
  letter-spacing: -.065em;
  margin: .7rem 0 .8rem;
  background: linear-gradient(110deg, #fff 25%, #c8b7ff 70%, #a1f2df);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.welcome p {
  color: #a3a8b2;
  max-width: 590px;
  font-size: 1.05rem;
  line-height: 1.7;
}
.prompt-label {
  color: #a0a5af;
  font-size: .78rem;
  font-weight: 600;
  margin: 1.5rem 0 .5rem;
}

div.stButton > button {
  border: 1px solid var(--nova-border);
  border-radius: 12px;
  background: rgba(255,255,255,.025);
  color: #d7d9de;
  transition: border-color .15s ease, background .15s ease, transform .15s ease;
}
div.stButton > button:hover {
  border-color: rgba(167,139,250,.48);
  background: rgba(167,139,250,.075);
  color: white;
  transform: translateY(-1px);
}
div.stButton > button[kind="primary"] {
  background: linear-gradient(110deg, #8567dc, #6e8fe2);
  border-color: transparent;
  color: white;
}

[data-testid="stChatMessage"] {
  background: transparent;
  border: 0;
  padding: 1rem .3rem;
}
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
  border-radius: 14px;
}
[data-testid="stChatMessageContent"] {
  line-height: 1.72;
}
[data-testid="stChatInput"] {
  border: 1px solid rgba(255,255,255,.13);
  border-radius: 17px;
  background: #1a1c20;
  box-shadow: 0 12px 40px rgba(0,0,0,.24);
}
[data-testid="stChatInput"]:focus-within {
  border-color: rgba(167,139,250,.58);
  box-shadow: 0 0 0 1px rgba(167,139,250,.18), 0 12px 40px rgba(0,0,0,.24);
}
[data-testid="stChatInput"] textarea {
  color: #f3f3f5;
}

.conversation-meta {
  color: #878d97;
  font-size: .76rem;
  padding: .2rem .15rem .9rem;
  border-bottom: 1px solid var(--nova-border);
  margin-bottom: 1rem;
}
.auth-card {
  text-align: center;
  margin: 14vh 0 1.5rem;
}
.auth-mark {
  margin: 0 auto 1.1rem;
}
.auth-card h1 {
  font: 600 2.15rem/1.15 'Space Grotesk', sans-serif;
  letter-spacing: -.05em;
  margin: .55rem 0;
  color: #f5f2ff;
}
.auth-card p {
  color: #a3a8b2;
  margin-bottom: 1.2rem;
}
.stAlert { border-radius: 12px; }

@media (max-width: 700px) {
  .block-container { padding: 1.2rem 1rem 6rem; }
  .welcome { min-height: 38vh; }
}
</style>
"""
