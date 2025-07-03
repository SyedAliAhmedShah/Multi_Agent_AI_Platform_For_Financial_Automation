import streamlit as st
from PIL import Image
import os, re, time, glob
from Fully_multi_agent import execute_task

# ── helpers ─────────────────────────────────────────────────────────
def remove_ansi(text: str) -> str:
    return re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', text)

def run_and_render(prompt: str):
    start = time.time()
    with st.spinner("🤖 Thinking…"):
        try:
            result = execute_task(prompt)
        except Exception as e:
            st.error(f"❌ Error: {e}")
            return

    output = result.get("output", result) if isinstance(result, dict) else result
    st.write(output)

    trace = remove_ansi(result.get("trace_log", "")) if isinstance(result, dict) else ""
    if trace:
        with st.expander("🧠 Agent Reasoning Trace", expanded=True):
            for line in trace.splitlines():
                if "Thought:" in line:
                    st.markdown(f"**Thought** → {line.split('Thought:')[-1].strip()}")
                elif "Action:" in line:
                    st.markdown(f"**Action** → `{line.split('Action:')[-1].strip()}`")
                elif "Observation:" in line:
                    st.code(line.split('Observation:')[-1].strip(), language="json")
                elif "Final Answer:" in line:
                    st.success(line.split('Final Answer:')[-1].strip())

    st.markdown("### 📂 Files Generated")
    cols = st.columns(3)
    pdf_dirs = ["invoices", "payslips", "reports"]
    new_pdfs = [
        p for d in pdf_dirs for p in glob.glob(f"{d}/*.pdf")
        if os.path.getmtime(p) >= start
    ]
    for i, p in enumerate(sorted(new_pdfs)):
        with open(p, "rb") as f:
            cols[i % 3].download_button(
                label=f"📥 {os.path.basename(p)}",
                data=f,
                mime="application/pdf",
                file_name=os.path.basename(p),
                key=p,
            )

    new_charts = [
        c for c in glob.glob("reports/*.png") if os.path.getmtime(c) >= start
    ]
    if new_charts:
        st.markdown("### 📊 Charts & Visuals")
        for c in new_charts:
            st.image(Image.open(c), caption=os.path.basename(c), use_column_width=True)

# ── page config ────────────────────────────────────────────────────
st.set_page_config(page_title="Finance Multi-Agent", layout="wide")

# ── custom css (unchanged) ─────────────────────────────────────────
st.markdown(
    """
    <style>
    [data-testid="stApp"] {
        background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 45%, #2563eb 100%);
        background-attachment: fixed;
        color: #e5e7eb;
        font-family: 'Segoe UI', sans-serif;
    }
    .card {
        background: rgba(17, 24, 39, 0.8);
        border-radius: 18px;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
        padding: 2rem 1.8rem;
        margin-bottom: 1.5rem;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
        color: #f1f5f9;
    }
    .card:hover {
        transform: translateY(-4px);
        box-shadow: 0 14px 34px rgba(0, 0, 0, 0.5);
    }
    .stButton>button,
    .stDownloadButton>button {
        background-color: #4f46e5;
        color: #f8fafc;
        border: none;
        border-radius: 30px;
        padding: 0.6rem 1.6rem;
        font-weight: 600;
        transition: background 0.2s;
    }
    .stButton>button:hover,
    .stDownloadButton>button:hover {
        background-color: #3730a3;
    }
    textarea {
        border-radius: 12px !important;
        font-size: 1rem !important;
        min-height: 130px !important;
        background-color: #f9fafb;
        color: #111 !important;
    }
    label[for] {
        color: #f8fafc !important;
        font-weight: 500;
    }
    div[data-testid="stExpander"] {
        background: rgba(17, 24, 39, 0.8) !important;
        border: 8px solid #475569 !important;
        border-radius: 12px !important;
        margin: 1rem 0 !important;
    }
    div[data-testid="stExpander"] > div {
        background: transparent !important;
    }
    div[data-testid="stExpander"] label {
        color: #f1f5f9 !important;
        font-weight: 600 !important;
        font-size: 1.5rem !important; 
        padding: 0.5rem 1rem !important;
    }
    div[data-testid="stExpander"] label:hover {
        color: #93c5fd !important;
    }
    div[data-testid="stExpander"] div[role="button"] {
        width: 100% !important;
    }

    
    </style>
    """,
    unsafe_allow_html=True,
)

# ── header ────────────────────────────────────────────────────────
st.markdown(
    """
    <div style='padding: 1rem 2rem; text-align: center;'>
        <h1 style='margin-bottom:0; color:#f1f5f9;'>💼 Multi-Agent Finance Automation Platform</h1>
        <p style='font-size:1.05rem; color:#cbd5e1;'>Multi-agent AI platform built for automating core finance operations: Employee Payroll, Customer Invoicing, Annual Reporting & Procurement.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


# ── predefined agent prompts ───────────────────────────────────────
AGENT_PROMPTS = {
    "Invoicing":   "Generate all customer invoices, send them via email, remind overdue ones, and mark paid invoices in the sheet.",
    "Payroll":     "Fetch payroll data, calculate employee salaries based on attendance, generate payslips, and email them to employees and HR.",
    "Reporting":   "Generate and send the annual financial report by fetching data from Google Sheets, calculating key metrics, generating income, balance sheet, and cash flow charts with insights, creating an LLM-based summary, compiling a PDF.",
    "Procurement": "Fetch procurement data, process budgets and inventory, summarize results, handle approvals, send notifications, generate the report, and email it. Follow all steps in exact sequence. Never stop or return a final answer before the last step.",
}

# ── section 1: quick-action buttons ───────────────────────────────
# ── section 1: quick-action buttons (aesthetic and functional) ─────────────────────────
# ── section 1: quick-action buttons (clean and functional) ──────────────
st.markdown("## ⚡ Quick Actions")

# Inject card + button styling
st.markdown("""
<style>
.quick-action-card {
    background: rgba(18, 1, 1, 0.431);
    border-radius: 16px;
    padding: 1.5rem 1rem;
    margin-bottom: 1.5rem;
    text-align: center;
    transition: all 0.3s ease-in-out;
    border: 1px solid #47556930;
    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
}
.quick-action-card:hover {
    background: rgba(255, 255, 255, 0.12);
    transform: translateY(-4px);
    box-shadow: 0 6px 20px rgba(0,0,0,0.2);
}
.quick-action-card h4 {
    color: #f1f5f9;
    margin-bottom: 0.5rem;
}
.quick-action-card p {
    font-size: 0.85rem;
    color: #cbd5e1;
}
.quick-action-button {
    background-color: #6366f1;
    color: white;
    border: none;
    border-radius: 20px;
    padding: 0.5rem 1.5rem;
    font-weight: 600;
    margin-top: 1rem;
    cursor: pointer;
    transition: background 0.2s ease-in-out;
}
.quick-action-button:hover {
    background-color: #4338ca;
}
            
            
</style>
""", unsafe_allow_html=True)

AGENT_DESCRIPTIONS = {
    "Invoicing": "Send invoices, email reminders, and track payments automatically.",
    "Payroll": "Calculate salaries and send payslips based on attendance data.",
    "Reporting": "Compile annual financial charts and LLM summaries in a PDF.",
    "Procurement": "Monitor budgets, inventory, and approvals with auto-generated reports."
}

AGENT_EMOJIS = {
    "Invoicing": "📨",
    "Payroll": "💸",
    "Reporting": "📊",
    "Procurement": "📦"
}

# Helper to generate card HTML
def html_card_button(name):
    button_key = name.lower()
    emoji = AGENT_EMOJIS.get(name, "🤖")
    description = AGENT_DESCRIPTIONS.get(name, "Run financial task")
    return f"""
    <div class='quick-action-card'>
        <h4>{emoji} {name}</h4>
        <p>{description}</p>
        <a href='?action={button_key}'><button class='quick-action-button'>Run {name}</button></a>
    </div>
    """


# Render cards in 4 columns
cols = st.columns(4)
for i, name in enumerate(AGENT_PROMPTS.keys()):  # ✅ just name
    with cols[i % 4]:
        st.markdown(html_card_button(name), unsafe_allow_html=True)


# Handle query param
query_params = st.query_params
action = query_params.get("action", None)
clicked_prompt = None

if action:
    for name, prompt in AGENT_PROMPTS.items():
        if action == name.lower():
            clicked_prompt = prompt
            break
    # Clear the query param
    st.query_params.clear()

st.markdown("---")


# ── section 2: main logic based on action ─────────────────────────
if clicked_prompt:
    run_and_render(clicked_prompt)
else:
    st.markdown("## 📝 Custom Task Prompt")
    user_input = st.text_area(
        "🔧 Specify the financial task you'd like the system to automate:",
        placeholder="e.g., Generate all customer invoices, send them via email, remind overdue ones, and mark paid invoices in the sheet."
    )
    if st.button("🚀 Run"):
        if not user_input.strip():
            st.warning("Please enter a prompt first.")
        else:
            run_and_render(user_input.strip())
    else:
        st.markdown(
    """
    <div style="background-color: #1e40af; padding: 1rem; border-radius: 10px; color: #f8fafc; font-weight: 500;">
        ⬆️ Click a quick-action button above or type a prompt, then press <strong>Run</strong>.
    </div>
    """,
    unsafe_allow_html=True
)

# ── vertical spacing between sections ─────────────────────
st.markdown("<br><br><br>", unsafe_allow_html=True)  # Add space before diagram

# At the end of your app:
import streamlit as st
import os
import base64

# ---- Title ----
st.markdown("## 🧭 Financial Automation Workflow Diagram")

# ---- Path to your PNG file ----
image_path = r"D:\panacealogics\Project\Flow Diagrams\Editor _ Mermaid Chart-2025-06-24-101035.png"

# ---- Show inside expandable + centered with zoom ----
if os.path.exists(image_path):
    with open(image_path, "rb") as image_file:
        encoded_image = base64.b64encode(image_file.read()).decode()

    with st.expander("📊 Show Multi-Agent Workflow Diagram", expanded=False):
        st.markdown(
            f"""
            <div style='text-align: center;'>
                <img src="data:image/png;base64,{encoded_image}" 
                     style="max-width:100%; width:900px; border-radius:12px; cursor: zoom-in;" 
                     onclick="window.open(this.src)" 
                     title="Click to open in full size">
                <div style="color:#94a3b8; font-size:0.85rem; margin-top:0.5rem;">
                    🔁 Multi-Agent Workflow Overview
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )
else:
    st.error(f"❌ PNG not found at: {image_path}")
