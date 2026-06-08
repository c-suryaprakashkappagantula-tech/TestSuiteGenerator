"""
theme_v3.py — Sunset Ember theme for TSG Dashboard V9.0
Warm amber/coral palette replacing the cool purple/blue of V8.

Palette:
  Page bg:      #0d0905  (near-black with warm undertone)
  Accent-1:     #f97316  (amber-500 — primary warm accent)
  Accent-2:     #ef4444  (red-500 — secondary / danger)
  Accent-3:     #fbbf24  (yellow-400 — highlight / success)
  Body text:    #fef3c7  (amber-100, ~9:1 on page bg)
  Muted text:   #d97706  (amber-600, ~4.8:1 on page bg)
  Caption:      #92400e  (amber-800, ~4.5:1 — captions / labels)

Everything else (structure, motion rules, font sizes) is preserved from theme_v2.
"""

CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap');

/* ── Animations — same minimal set as V2 ── */
@keyframes fadeSlideIn { from { opacity:0; transform:translateY(12px); } to { opacity:1; transform:translateY(0); } }
@keyframes pulse       { 0%,100% { opacity:1; } 50% { opacity:0.55; } }

/* Accessibility: honour user preference */
@media (prefers-reduced-motion: reduce) {
    * { animation: none !important; transition: none !important; }
}

/* ── Base ── */
html, body { margin:0; padding:0; scroll-behavior: smooth; }
.stApp {
    background: #0d0905;
    background-image:
        radial-gradient(ellipse at 20% 50%, rgba(249,115,22,0.09) 0%, transparent 50%),
        radial-gradient(ellipse at 80% 20%, rgba(239,68,68,0.06) 0%, transparent 50%);
    color: #fef3c7;
    font-family: "Inter", -apple-system, sans-serif;
    font-size: 14px;
}
[data-testid="stToolbar"], header[data-testid="stHeader"], #MainMenu, footer,
header[tabindex="-1"] { display:none !important; visibility:hidden !important; height:0 !important; }
.block-container { padding-top: 0.5rem !important; max-width: 100% !important; }

/* ── Glass Card ── */
.glass {
    background: rgba(25,14,6,0.84);
    backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
    border: 1px solid rgba(249,115,22,0.14);
    border-radius: 20px;
    padding: 18px 20px;
    margin-bottom: 12px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.40), inset 0 1px 0 rgba(251,191,36,0.05);
    animation: fadeSlideIn 0.4s ease-out;
    transition: border-color 0.25s ease, box-shadow 0.25s ease;
}
.glass:hover {
    border-color: rgba(249,115,22,0.28);
    box-shadow: 0 8px 32px rgba(0,0,0,0.50), 0 0 18px rgba(249,115,22,0.08);
}

/* ── Top Banner ── */
.banner {
    background: linear-gradient(135deg, rgba(249,115,22,0.15) 0%, rgba(239,68,68,0.10) 50%, rgba(251,191,36,0.08) 100%);
    backdrop-filter: blur(20px);
    border: 1px solid rgba(249,115,22,0.25);
    border-radius: 24px;
    padding: 20px 28px;
    margin-bottom: 16px;
    display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap;
    animation: fadeSlideIn 0.35s ease-out;
    position: relative; overflow: hidden;
}
/* Warm accent bar */
.banner::before {
    content: ""; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, #f97316, #fbbf24);
    opacity: 0.8;
}
/* Title: amber-to-yellow gradient — both stops ~8:1 on dark bg */
.banner .title {
    font-weight: 900; font-size: 26px; letter-spacing: -0.5px;
    background: linear-gradient(135deg, #fb923c 0%, #fbbf24 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.banner .sub  { color: #fcd34d; font-size: 13px; font-weight: 500; margin-top: 3px; }
.banner .badge {
    display: inline-flex; align-items: center; gap: 6px;
    background: rgba(249,115,22,0.15); border: 1px solid rgba(249,115,22,0.35);
    padding: 6px 14px; border-radius: 20px;
    font-size: 12px; font-weight: 700; color: #fed7aa;
}

/* ── Section Title ── */
.sec-title {
    font-size: 13px; font-weight: 700;
    color: #fb923c;
    margin-bottom: 10px;
    display: flex; align-items: center; gap: 10px;
}
.sec-title .icon { font-size: 16px; }
.sec-title::after {
    content: ""; flex: 1; height: 1px;
    background: linear-gradient(90deg, rgba(249,115,22,0.35), transparent);
}

/* ── Buttons ── */
.stButton > button {
    background: linear-gradient(135deg, rgba(249,115,22,0.18), rgba(239,68,68,0.14)) !important;
    color: #fef3c7 !important;
    border: 1px solid rgba(249,115,22,0.28) !important;
    padding: 0.35rem 0.6rem !important;
    border-radius: 12px !important;
    font-weight: 700 !important; font-size: 12px !important;
    min-height: 0 !important; line-height: 1.2 !important;
    backdrop-filter: blur(10px) !important;
    transition: background 0.2s ease, box-shadow 0.2s ease !important;
}
.stButton > button:hover {
    background: linear-gradient(135deg, rgba(249,115,22,0.38), rgba(239,68,68,0.28)) !important;
    box-shadow: 0 6px 20px rgba(249,115,22,0.28) !important;
    border-color: rgba(249,115,22,0.50) !important;
}
/* Primary button */
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #ea580c, #dc2626) !important;
    border: none !important;
    color: #fff !important;
    padding: 0.7rem 1.2rem !important;
    font-size: 14px !important;
    box-shadow: 0 4px 15px rgba(234,88,12,0.35) !important;
}
.stButton > button[kind="primary"]:hover {
    box-shadow: 0 8px 28px rgba(234,88,12,0.50) !important;
}

/* ── CLI Terminal ── */
.cli-header {
    font-weight: 700; color: #fed7aa; letter-spacing: 0.2px;
    background: linear-gradient(135deg, rgba(249,115,22,0.12), rgba(239,68,68,0.08));
    border: 1px solid rgba(249,115,22,0.22);
    border-radius: 12px; padding: 8px 14px; margin: 0 0 8px 0;
    display: flex; align-items: center; gap: 8px; font-size: 13px;
}
.cli-header::before { content: ""; width:8px; height:8px; border-radius:50%;
    background: #22c55e; animation: pulse 2s infinite; box-shadow: 0 0 6px rgba(34,197,94,0.4); }
.cli-box {
    background: rgba(10,5,2,0.90);
    border: 1px solid rgba(249,115,22,0.14);
    border-radius: 16px; padding: 14px 16px;
    height: 420px; min-height: 420px; max-height: 420px;
    overflow-y: auto; overflow-x: auto;
    font-family: "JetBrains Mono", monospace; font-size: 13px; line-height: 1.65;
    color: #fcd34d;
    box-shadow: inset 0 2px 10px rgba(0,0,0,0.50);
}
.cli-box pre { margin: 0; white-space: pre-wrap; word-wrap: break-word; }
.cli-box::-webkit-scrollbar { width: 6px; }
.cli-box::-webkit-scrollbar-track { background: rgba(249,115,22,0.05); border-radius: 3px; }
.cli-box::-webkit-scrollbar-thumb { background: rgba(249,115,22,0.30); border-radius: 3px; }
.cli-box::-webkit-scrollbar-thumb:hover { background: rgba(249,115,22,0.50); }

/* ── Pipeline Status Strip ── */
.pipeline-strip {
    display: flex; gap: 6px; flex-wrap: wrap; align-items: center;
    background: rgba(20,10,4,0.82);
    border: 1px solid rgba(249,115,22,0.18);
    border-radius: 12px; padding: 8px 14px; margin-bottom: 8px;
    font-size: 12px; font-family: "Inter", sans-serif;
}
.ps-stage {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 3px 10px; border-radius: 20px;
    font-weight: 600; font-size: 12px;
}
.ps-stage.done    { background: rgba(34,197,94,0.12);  color: #4ade80; border: 1px solid rgba(34,197,94,0.25); }
.ps-stage.running { background: rgba(249,115,22,0.14); color: #fdba74; border: 1px solid rgba(249,115,22,0.30); animation: pulse 1.4s infinite; }
.ps-stage.pending { background: rgba(120,90,60,0.12);  color: #a8856a; border: 1px solid rgba(120,90,60,0.20); }
.ps-stage.error   { background: rgba(239,68,68,0.12);  color: #fca5a5; border: 1px solid rgba(239,68,68,0.25); }

/* ── Stats Row ── */
.stats-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 12px 0; }
.stat-card {
    background: rgba(25,12,4,0.84);
    backdrop-filter: blur(16px);
    border: 1px solid rgba(249,115,22,0.12);
    border-radius: 16px; padding: 16px; text-align: center;
    position: relative; overflow: hidden;
    animation: fadeSlideIn 0.5s ease-out;
    transition: transform 0.25s ease, box-shadow 0.25s ease;
}
.stat-card::before {
    content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px;
    background: var(--accent); opacity: 0.85;
}
.stat-card:hover { transform: translateY(-3px); box-shadow: 0 10px 28px rgba(0,0,0,0.35); }
.stat-card .icon  { font-size: 24px; margin-bottom: 4px; }
.stat-card .label { font-size: 12px; color: #b45309; font-weight: 600; text-transform: uppercase; letter-spacing: 0.4px; }
.stat-card .value { font-size: 28px; font-weight: 900; color: #fef9c3; margin-top: 2px; }

/* ── Exit Report ── */
.exit-report {
    background: linear-gradient(135deg, rgba(34,197,94,0.08), rgba(249,115,22,0.05));
    border: 1px solid rgba(34,197,94,0.2);
    border-radius: 20px; padding: 18px 20px; margin-top: 12px;
    animation: fadeSlideIn 0.45s ease-out;
}
.exit-report.error {
    background: linear-gradient(135deg, rgba(239,68,68,0.08), rgba(249,115,22,0.05));
    border-color: rgba(239,68,68,0.2);
}
.exit-report h4 { margin: 0 0 10px 0; font-weight: 800; color: #4ade80; }
.exit-report.error h4 { color: #f87171; }
.exit-report ul { margin: 0; padding-left: 20px; color: #fcd34d; }
.exit-report li { margin: 4px 0; font-size: 14px; }

/* ── Form Elements ── */
.stTextInput label, .stSelectbox label, .stMultiSelect label, .stRadio label,
.stFileUploader label, .stCheckbox label, .stCheckbox span,
[data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] label,
[data-testid="stMarkdownContainer"] p,
.stRadio > div > label, .stMultiSelect > label,
.stCaption, [data-testid="stCaptionContainer"] p,
.stSelectbox [data-testid="stMarkdownContainer"] p,
.stRadio > div > div > label {
    color: #fed7aa !important; font-weight: 600 !important; font-size: 14px !important;
}
.stCaption, [data-testid="stCaptionContainer"] p {
    color: #b45309 !important; font-size: 13px !important;
}
.stTextInput input {
    color: #1c0a00 !important; background: rgba(255,245,235,0.94) !important;
    border: 1px solid rgba(249,115,22,0.25) !important;
    border-radius: 12px !important; font-weight: 600 !important;
    font-size: 14px !important;
    transition: border-color 0.2s ease, box-shadow 0.2s ease !important;
}
.stTextInput input:focus {
    border-color: rgba(249,115,22,0.55) !important;
    box-shadow: 0 0 14px rgba(249,115,22,0.14) !important;
}
.stCheckbox > label > div:first-child[data-checked="true"] {
    background: linear-gradient(135deg, #ea580c, #dc2626) !important;
    border-color: transparent !important;
}
.stDownloadButton > button {
    background: rgba(249,115,22,0.12) !important;
    border: 1px solid rgba(249,115,22,0.25) !important;
    color: #fed7aa !important;
    border-radius: 12px !important; font-weight: 700 !important; font-size: 13px !important;
}
.stDownloadButton > button:hover {
    background: rgba(249,115,22,0.22) !important;
    box-shadow: 0 4px 14px rgba(249,115,22,0.22) !important;
    transform: translateY(-2px) !important;
}

/* ── Alerts ── */
[data-testid="stAlert"] {
    border-radius: 14px !important;
    backdrop-filter: blur(10px) !important;
    font-size: 14px !important;
}
</style>"""
