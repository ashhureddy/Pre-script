"""
Streamlit front-end for the Pre-Scripting Validation tool.

Styling matches QUICKIX (ashhureddy/integration-template-generator) exactly -
same sticky navy topbar, same button gradient, same card style, same MasTec
accent palette (Prussian Blue #00284e, Endeavour #024ea4, Orange #ff5b24) -
with the real MasTec logo image in place of QUICKIX's CSS-text placeholder.

Run locally:    streamlit run "Streamlit app.py"
Deploy:         push this repo to GitHub, then deploy on share.streamlit.io
"""
import base64
import os
import tempfile

import streamlit as st

import run_validation as rv
import pre_extract as pe

st.set_page_config(page_title="Pre-Scripting Validation", page_icon="📡", layout="wide")

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mastec_logo_trim.png")


def _logo_b64():
    if not os.path.exists(LOGO_PATH):
        return None
    with open(LOGO_PATH, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


# ---- sticky top bar + shared styling (same palette/structure as QUICKIX) ----
_logo = _logo_b64()
_logo_html = f'<img src="data:image/png;base64,{_logo}" style="height:28px;filter:brightness(0) invert(1);" />' if _logo else '<div class="qkx-logo">MAS<span>TEC</span></div>'

st.markdown(f"""
<style>
  .stApp {{
      background: linear-gradient(180deg, #eef3fa 0%, #f7f9fc 100%);
  }}
  .qkx-topbar {{
      position: sticky; top: 0; z-index: 999;
      display: flex; justify-content: space-between; align-items: center;
      padding: 0.9rem 1.75rem; margin: -1rem -1rem 1.5rem -1rem;
      background: linear-gradient(90deg, #011b36 0%, #012a4e 100%);
      border-bottom: 1px solid rgba(255,91,36,0.55);
      box-shadow: 0 4px 18px rgba(0,0,0,0.2);
  }}
  .qkx-topbar .qkx-logo {{ font-size: 1.4rem; font-weight: 900; color: #ffffff; letter-spacing: 1px; }}
  .qkx-topbar .qkx-logo span {{ color: #ffffff; }}
  .qkx-topbar .qkx-credit {{ font-size: 0.78rem; color: #cfe0f5; text-align: right; line-height: 1.3; }}

  .qkx-hero {{ text-align: center; margin: 1rem 0 2.5rem 0; }}
  .qkx-hero h1 {{
      font-size: 2.6rem; font-weight: 900; letter-spacing: 2px; margin-bottom: 0.3rem;
      color: #012a4e;
  }}
  .qkx-hero p {{ color: #4a5b70; font-size: 1.02rem; }}

  div[data-testid="stButton"] button, div[data-testid="stFormSubmitButton"] button {{
      border-radius: 10px; font-weight: 700; border: 1.5px solid #013a6b;
      background: linear-gradient(135deg, #024ea4, #013a6b); color: #ffffff;
      box-shadow: 0 3px 8px rgba(1,42,78,0.25);
      transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
  }}
  div[data-testid="stButton"] button:hover, div[data-testid="stFormSubmitButton"] button:hover {{
      border-color: #ff5b24; color: #ffffff; transform: translateY(-1px);
      box-shadow: 0 6px 14px rgba(255,91,36,0.35);
  }}
  div[data-testid="stButton"] button:active, div[data-testid="stFormSubmitButton"] button:active {{ transform: translateY(0); }}

  /* Bordered containers / file uploaders - clean light cards */
  div[data-testid="stVerticalBlockBorderWrapper"], div[data-testid="stFileUploaderDropzone"] {{
      background: #ffffff !important;
      border: 1px solid #dde5ef !important;
      border-radius: 12px !important;
      box-shadow: 0 2px 10px rgba(1,42,78,0.06);
  }}

  .qkx-section-label {{ font-weight: 700; color: #012a4e; font-size: 0.95rem; margin: 0.6rem 0 0.3rem 0; }}
</style>
<div class="qkx-topbar">
  {_logo_html}
  <div class="qkx-credit">Pre-Scripting Validation<br>Powered by <b>MASTEC</b></div>
</div>
""", unsafe_allow_html=True)

st.markdown("""
<div class="qkx-hero">
  <h1>PRE-SCRIPT</h1>
  <p>Pre-Scripting Validation — CIQ vs EDP vs RFDS vs Pre kget-all</p>
</div>
""", unsafe_allow_html=True)

with st.form("inputs"):
    st.markdown('<div class="qkx-section-label">Required files</div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        ciq_file = st.file_uploader("CIQ (.xlsx)", type=["xlsx"])
    with c2:
        edp_file = st.file_uploader("EDP (.xls)", type=["xls"])

    st.markdown('<div class="qkx-section-label">Optional</div>', unsafe_allow_html=True)
    c3, c4 = st.columns(2)
    with c3:
        rfds_file = st.file_uploader("RFDS (.pdf)", type=["pdf"])
    with c4:
        log_files = st.file_uploader(
            "Pre kget-all logs (.log / .txt) — one per node",
            type=["log", "txt"], accept_multiple_files=True,
        )

    submitted = st.form_submit_button("Generate report", type="primary", use_container_width=True)

if submitted:
    if not ciq_file or not edp_file:
        st.error("CIQ and EDP are both required.")
        st.stop()

    with st.spinner("Running validation checks..."):
        with tempfile.TemporaryDirectory() as tmp:
            ciq_path = os.path.join(tmp, ciq_file.name)
            with open(ciq_path, "wb") as f:
                f.write(ciq_file.getbuffer())

            edp_path = os.path.join(tmp, edp_file.name)
            with open(edp_path, "wb") as f:
                f.write(edp_file.getbuffer())

            rfds_path = None
            if rfds_file:
                rfds_path = os.path.join(tmp, rfds_file.name)
                with open(rfds_path, "wb") as f:
                    f.write(rfds_file.getbuffer())

            node_log_paths = {}
            for lf in (log_files or []):
                text = lf.getvalue().decode("utf-8", errors="replace")
                node_id = pe.node_id_from_log(text) or os.path.splitext(lf.name)[0]
                log_path = os.path.join(tmp, lf.name)
                with open(log_path, "w") as f:
                    f.write(text)
                node_log_paths[node_id] = log_path

            out_pdf = os.path.join(tmp, "validation_report.pdf")
            try:
                rv.run(ciq_path, edp_path, rfds_path, node_log_paths, out_pdf)
            except Exception as e:
                st.error(f"Report generation failed: {e}")
                st.stop()

            with open(out_pdf, "rb") as f:
                pdf_bytes = f.read()

    st.success("Report generated.")
    st.download_button("⬇️ Download PDF", data=pdf_bytes, file_name="validation_report.pdf",
                        mime="application/pdf", use_container_width=True)
