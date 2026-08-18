"""
Streamlit front-end for the Pre-Scripting Validation tool.

Run locally:    streamlit run streamlit_app.py
Deploy:         push this repo to GitHub, then deploy on share.streamlit.io
                pointing at this file (see README.md).
"""
import os
import tempfile

import streamlit as st

import run_validation as rv
import pre_extract as pe

st.set_page_config(page_title="Pre-Scripting Validation", page_icon="📡", layout="centered")

st.title("📡 Pre-Scripting Validation")
st.caption("CIQ + EDP + RFDS + Pre kget-all logs → validation PDF")

with st.form("inputs"):
    st.subheader("Required files")
    ciq_file = st.file_uploader("CIQ (.xlsx)", type=["xlsx"])
    edp_file = st.file_uploader("EDP (.xls)", type=["xls"])

    st.subheader("Optional")
    rfds_file = st.file_uploader("RFDS (.pdf)", type=["pdf"])
    log_files = st.file_uploader(
        "Pre kget-all logs (.log / .txt) — one per node, node ID is read from each file",
        type=["log", "txt"], accept_multiple_files=True,
    )

    submitted = st.form_submit_button("Generate report", type="primary")

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
    st.download_button("⬇️ Download PDF", data=pdf_bytes, file_name="validation_report.pdf", mime="application/pdf")
