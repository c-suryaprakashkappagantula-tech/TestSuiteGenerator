"""
theme_v2.py — Premium UI theme for TSG Dashboard V2.0
Glassmorphism + Neon + Rainbow + Animations
"""

CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap');

/* ── Animations ── */
@keyframes fadeSlideIn { from { opacity:0; transform:translateY(18px); } to { opacity:1; transform:translateY(0); } }
@keyframes shimmer { 0% { background-position: -200% 0; } 100% { background-position: 200% 0; } }
@keyframes glow { 0%,100% { box-shadow: 0 0 20px rgba(139,92,246,0.3); } 50% { box-shadow: 0 0 40px rgba(59,130,246,0.5); } }
@keyframes float { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-4px); } }
@keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.5; } }
@keyframes rainbowBorder {
    0% { border-color: #f43f5e; }
    16% { border-color: #f97316; }
    33% { border-color: #eab308; }
    50% { border-color: #22c55e; }
    66% { border-color: #3b82f6; }
    83% { border-color: #8b5cf6; }
    100% { border-color: #f43f5e; }
}
@keyframes gradientShift {
    0% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
    100% { background-position: 0% 50%; }
}
@keyframes neonPulse {
    0%,100% { text-shadow: 0 0 10px rgba(139,92,246,0.5), 0 0 20px rgba(139,92,246,0.3); }
    50% { text-shadow: 0 0 20px rgba(59,130,246,0.8), 0 0 40px rgba(59,130,246,0.4); }
}

/* ── Base ── */
html, body { margin:0; padding:0; scroll-behavior: smooth; }
.stApp {
    background: #0a0a0f;
    background-image:
        radial-gradient(ellipse at 20% 50%, rgba(139,92,246,0.08) 0%, transparent 50%),
        radial-gradient(ellipse at 80% 20%, rgba(59,130,246,0.06) 0%, transparent 50%),
        radial-gradient(ellipse at 50% 80%, rgba(236,72,153,0.05) 0%, transparent 50%);
    color: #e2e8f0;
    font-family: "Inter", -apple-system, sans-serif;
}
[data-testid="stToolbar"], header[data-testid="stHeader"], #MainMenu, footer,
header[tabindex="-1"] { display:none !important; visibility:hidden !important; height:0 !important; }
.block-container { padding-top: 0.5rem !important; max-width: 100% !important; }

/* ── Glass Card ── */
.glass {
    background: rgba(15,15,25,0.6);
    backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 20px;
    padding: 18px 20px;
    margin-bottom: 12px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.05);
    animation: fadeSlideIn 0.5s ease-out;
    transition: all 0.3s cubic-bezier(0.4,0,0.2,1);
}
.glass:hover {
    border-color: rgba(139,92,246,0.2);
    box-shadow: 0 8px 32px rgba(0,0,0,0.4), 0 0 20px rgba(139,92,246,0.08);
}

/* ── Top Banner ── */
.banner {
    background: linear-gradient(135deg, rgba(139,92,246,0.15) 0%, rgba(59,130,246,0.1) 50%, rgba(236,72,153,0.1) 100%);
    backdrop-filter: blur(20px);
    border: 1px solid rgba(139,92,246,0.2);
    border-radius: 24px;
    padding: 20px 28px;
    margin-bottom: 16px;
    display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap;
    animation: fadeSlideIn 0.4s ease-out;
    position: relative; overflow: hidden;
}
.banner::before {
    content: ""; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, #f43f5e, #f97316, #eab308, #22c55e, #3b82f6, #8b5cf6, #ec4899);
    background-size: 200% 100%;
    animation: gradientShift 4s ease infinite;
}
.banner .title {
    font-weight: 900; font-size: 26px; letter-spacing: -0.5px;
    background: linear-gradient(135deg, #c084fc 0%, #60a5fa 40%, #34d399 70%, #fbbf24 100%);
    background-size: 200% 200%;
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    animation: gradientShift 6s ease infinite, neonPulse 3s ease infinite;
}
.banner .sub { color: #94a3b8; font-size: 13px; font-weight: 500; margin-top: 3px; }
.banner .badge {
    display: inline-flex; align-items: center; gap: 6px;
    background: rgba(139,92,246,0.15); border: 1px solid rgba(139,92,246,0.3);
    padding: 6px 14px; border-radius: 20px;
    font-size: 11px; font-weight: 700; color: #c084fc;
    animation: float 3s ease-in-out infinite;
}

/* ── Section Title ── */
.sec-title {
    font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 1.5px;
    color: #8b5cf6; margin-bottom: 10px;
    display: flex; align-items: center; gap: 10px;
}
.sec-title .icon { font-size: 16px; animation: float 2s ease-in-out infinite; }
.sec-title::after {
    content: ""; flex: 1; height: 1px;
    background: linear-gradient(90deg, rgba(139,92,246,0.3), transparent);
}

/* ── PI Buttons ── */
.stButton > button {
    background: linear-gradient(135deg, rgba(139,92,246,0.2), rgba(59,130,246,0.2)) !important;
    color: #e2e8f0 !important;
    border: 1px solid rgba(139,92,246,0.25) !important;
    padding: 0.35rem 0.6rem !important;
    border-radius: 12px !important;
    font-weight: 700 !important; font-size: 11px !important;
    min-height: 0 !important; line-height: 1.2 !important;
    backdrop-filter: blur(10px) !important;
    transition: all 0.25s cubic-bezier(0.4,0,0.2,1) !important;
}
.stButton > button:hover {
    transform: translateY(-3px) scale(1.03) !important;
    background: linear-gradient(135deg, rgba(139,92,246,0.4), rgba(59,130,246,0.3)) !important;
    box-shadow: 0 8px 25px rgba(139,92,246,0.3), 0 0 15px rgba(139,92,246,0.15) !important;
    border-color: rgba(139,92,246,0.5) !important;
}
.stButton > button:active { transform: translateY(-1px) scale(0.98) !important; }
/* Primary button override */
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #7c3aed, #3b82f6, #06b6d4) !important;
    background-size: 200% 200% !important;
    animation: gradientShift 4s ease infinite !important;
    border: none !important;
    color: #fff !important;
    padding: 0.7rem 1.2rem !important;
    font-size: 13px !important;
    box-shadow: 0 4px 15px rgba(124,58,237,0.3) !important;
}
.stButton > button[kind="primary"]:hover {
    box-shadow: 0 8px 30px rgba(124,58,237,0.5), 0 0 20px rgba(59,130,246,0.3) !important;
}

/* ── CLI Terminal ── */
.cli-header {
    font-weight: 700; color: #c084fc; letter-spacing: 0.3px;
    background: linear-gradient(135deg, rgba(139,92,246,0.12), rgba(59,130,246,0.08));
    border: 1px solid rgba(139,92,246,0.2);
    border-radius: 12px; padding: 8px 14px; margin: 0 0 8px 0;
    display: flex; align-items: center; gap: 8px; font-size: 13px;
}
.cli-header::before { content: ""; width:8px; height:8px; border-radius:50%;
    background: #22c55e; animation: pulse 2s infinite; box-shadow: 0 0 8px rgba(34,197,94,0.5); }
.cli-box {
    background: rgba(8,8,16,0.8);
    border: 1px solid rgba(139,92,246,0.12);
    border-radius: 16px; padding: 14px 16px;
    height: 420px; min-height: 420px; max-height: 420px;
    overflow-y: auto; overflow-x: auto;
    font-family: "JetBrains Mono", monospace; font-size: 11.5px; line-height: 1.6;
    color: #a5b4fc;
    box-shadow: inset 0 2px 10px rgba(0,0,0,0.4);
}
.cli-box pre { margin: 0; white-space: pre-wrap; word-wrap: break-word; }
.cli-box::-webkit-scrollbar { width: 6px; }
.cli-box::-webkit-scrollbar-track { background: rgba(139,92,246,0.05); border-radius: 3px; }
.cli-box::-webkit-scrollbar-thumb { background: rgba(139,92,246,0.3); border-radius: 3px; }
.cli-box::-webkit-scrollbar-thumb:hover { background: rgba(139,92,246,0.5); }

/* ── Stats Row ── */
.stats-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 12px 0; }
.stat-card {
    background: rgba(15,15,25,0.6);
    backdrop-filter: blur(16px);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 16px; padding: 16px; text-align: center;
    position: relative; overflow: hidden;
    animation: fadeSlideIn 0.6s ease-out;
    transition: all 0.3s ease;
}
.stat-card::before {
    content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px;
    background: var(--accent);
    opacity: 0.8;
}
.stat-card:hover { transform: translateY(-4px); box-shadow: 0 12px 30px rgba(0,0,0,0.3); }
.stat-card .icon { font-size: 24px; margin-bottom: 4px; animation: float 3s ease-in-out infinite; }
.stat-card .label { font-size: 10px; color: #64748b; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }
.stat-card .value { font-size: 28px; font-weight: 900; color: #fff; margin-top: 2px;
    text-shadow: 0 0 20px rgba(139,92,246,0.2); }

/* ── Exit Report ── */
.exit-report {
    background: linear-gradient(135deg, rgba(34,197,94,0.08), rgba(59,130,246,0.05));
    border: 1px solid rgba(34,197,94,0.2);
    border-radius: 20px; padding: 18px 20px; margin-top: 12px;
    animation: fadeSlideIn 0.5s ease-out;
}
.exit-report.error {
    background: linear-gradient(135deg, rgba(239,68,68,0.08), rgba(236,72,153,0.05));
    border-color: rgba(239,68,68,0.2);
}
.exit-report h4 { margin: 0 0 10px 0; font-weight: 800;
    background: linear-gradient(135deg, #34d399, #60a5fa);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.exit-report.error h4 {
    background: linear-gradient(135deg, #f87171, #ec4899);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.exit-report ul { margin: 0; padding-left: 20px; color: #94a3b8; }
.exit-report li { margin: 4px 0; font-size: 13px; }

/* ── Form Elements ── */
.stTextInput label, .stSelectbox label, .stMultiSelect label, .stRadio label,
.stFileUploader label, .stCheckbox label, .stCheckbox span,
[data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] label,
[data-testid="stMarkdownContainer"] p,
.stRadio > div > label, .stMultiSelect > label,
.stCaption, [data-testid="stCaptionContainer"] p,
.stSelectbox [data-testid="stMarkdownContainer"] p,
.stRadio > div > div > label {
    color: #c4b5fd !important; font-weight: 700 !important;
}
.stTextInput input {
    color: #1e1b4b !important; background: rgba(255,255,255,0.92) !important;
    border: 1px solid rgba(139,92,246,0.2) !important;
    border-radius: 12px !important; font-weight: 600 !important;
    transition: all 0.2s ease !important;
}
.stTextInput input:focus {
    border-color: rgba(139,92,246,0.5) !important;
    box-shadow: 0 0 20px rgba(139,92,246,0.15) !important;
}
.stCheckbox > label > div:first-child[data-checked="true"] {
    background: linear-gradient(135deg, #7c3aed, #3b82f6) !important;
    border-color: transparent !important;
}
.stDownloadButton > button {
    background: rgba(139,92,246,0.1) !important;
    border: 1px solid rgba(139,92,246,0.2) !important;
    color: #c084fc !important;
    border-radius: 12px !important; font-weight: 700 !important;
}
.stDownloadButton > button:hover {
    background: rgba(139,92,246,0.2) !important;
    box-shadow: 0 4px 15px rgba(139,92,246,0.2) !important;
    transform: translateY(-2px) !important;
}

/* ── Success/Info/Warning ── */
[data-testid="stAlert"] {
    border-radius: 14px !important;
    backdrop-filter: blur(10px) !important;
}
</style>"""
