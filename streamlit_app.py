# streamlit_app.py
import os
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from tat_engine import run_tat_pipeline, list_sheets


st.set_page_config(page_title="Workshop TAT Calculator (PoC)", layout="wide")

st.title("🚚 Workshop TAT Calculator (PoC)")
st.caption("Upload Gate Register + System/Tableau Extract → Click Calculate → Download corrected outputs.")

with st.expander("⚠️ PoC note (read once)", expanded=True):
    st.write(
        "This is a proof-of-concept hosted on Streamlit Community Cloud. "
        "Avoid uploading sensitive client data. For production/on-prem, we will deploy inside your network."
    )

col1, col2 = st.columns(2)
with col1:
    gate_file = st.file_uploader("1) Upload Gate Register (Primary Source)", type=["xlsx", "csv"])
with col2:
    sys_file = st.file_uploader("2) Upload System/Tableau Extract (Validation Only)", type=["xlsx", "csv"])

gate_sheet = None
sys_sheet = None

def _save_upload(uploaded, folder: str) -> str:
    path = Path(folder) / uploaded.name
    path.write_bytes(uploaded.getvalue())
    return str(path)

if gate_file is not None:
    st.markdown("#### Gate Register preview")
    try:
        if gate_file.name.lower().endswith(("xlsx","xlsm","xls")):
            # Need to save first to list sheets reliably
            with tempfile.TemporaryDirectory() as _tmp:
                tmp_path = _save_upload(gate_file, _tmp)
                sheets = list_sheets(tmp_path)
            if sheets:
                gate_sheet = st.selectbox("Gate Register sheet", sheets, index=0)
        # show preview (first 10 rows)
        gate_df_preview = pd.read_excel(gate_file, sheet_name=gate_sheet) if gate_file.name.lower().endswith(("xlsx","xlsm","xls")) else pd.read_csv(gate_file)
        st.dataframe(gate_df_preview.head(10), use_container_width=True)
    except Exception as e:
        st.error(f"Could not preview Gate Register file: {e}")

if sys_file is not None:
    st.markdown("#### System/Tableau Extract preview")
    try:
        if sys_file.name.lower().endswith(("xlsx","xlsm","xls")):
            with tempfile.TemporaryDirectory() as _tmp:
                tmp_path = _save_upload(sys_file, _tmp)
                sheets = list_sheets(tmp_path)
            if sheets:
                sys_sheet = st.selectbox("System/Tableau sheet", sheets, index=0)
        sys_df_preview = pd.read_excel(sys_file, sheet_name=sys_sheet) if sys_file.name.lower().endswith(("xlsx","xlsm","xls")) else pd.read_csv(sys_file)
        st.dataframe(sys_df_preview.head(10), use_container_width=True)
    except Exception as e:
        st.error(f"Could not preview System/Tableau file: {e}")

st.divider()

run_btn = st.button("✅ Calculate TAT", type="primary", disabled=(gate_file is None or sys_file is None))

if run_btn:
    with st.spinner("Running cleaning → matching → TAT calculation → validations → outputs..."):
        with tempfile.TemporaryDirectory() as tmp:
            gate_path = _save_upload(gate_file, tmp)
            sys_path  = _save_upload(sys_file, tmp)
            out_dir = os.path.join(tmp, "outputs")
            os.makedirs(out_dir, exist_ok=True)

            try:
                result_paths = run_tat_pipeline(
                    gate_path=gate_path,
                    sys_path=sys_path,
                    out_dir=out_dir,
                    gate_sheet=gate_sheet,
                    sys_sheet=sys_sheet
                )
            except Exception as e:
                st.error("❌ Processing failed. This usually happens due to unexpected column names or date formats.")
                st.exception(e)
                st.stop()

            # Load main output for summary
            main_df = pd.read_csv(result_paths["main_csv"])

            # Compute final metrics (same logic as requirement)
            # Load main output for summary
            main_df = pd.read_csv(result_paths["main_csv"])

            # -----------------------------
            # NEW KPIs (as per your request)
            # -----------------------------

            # ROT column name can differ depending on engine version
            rot_col = None
            if "System_ROT_Hours" in main_df.columns:
                rot_col = "System_ROT_Hours"
            elif "System_ROT_Hours_Matched" in main_df.columns:
                rot_col = "System_ROT_Hours_Matched"

            # 1) Average TAT = avg corrected TAT of ALL valid rows
            # "Valid" here means: corrected TAT exists and is non-negative
            valid_mask = main_df["Corrected_TAT_Hours"].notna() & (main_df["Corrected_TAT_Hours"] >= 0)
            avg_tat_all_valid = float(main_df.loc[valid_mask, "Corrected_TAT_Hours"].mean()) if valid_mask.any() else None

            # 2) Minor jobs = ROT <= 2 hours (only where ROT is available)
            if rot_col:
                rot_series = pd.to_numeric(main_df[rot_col], errors="coerce")
                minor_mask = valid_mask & rot_series.notna() & (rot_series <= 2)
                jobs_with_rot = int(rot_series.notna().sum())
            else:
                rot_series = None
                minor_mask = pd.Series([False] * len(main_df))
                jobs_with_rot = 0

            minor_jobs_count = int(minor_mask.sum())

            # 3) Avg TAT of minor jobs
            avg_tat_minor = float(main_df.loc[minor_mask, "Corrected_TAT_Hours"].mean()) if minor_jobs_count else None

            # 4) Avg GIGO TAT Delay (all jobs where it exists)
            gigo_mask = main_df["GIGO_TAT_Delay_Hours"].notna()
            avg_gigo_all = float(main_df.loc[gigo_mask, "GIGO_TAT_Delay_Hours"].mean()) if gigo_mask.any() else None

            # 5) Avg GIGO TAT Delay for minor jobs
            minor_gigo_mask = minor_mask & main_df["GIGO_TAT_Delay_Hours"].notna()
            avg_gigo_minor = float(main_df.loc[minor_gigo_mask, "GIGO_TAT_Delay_Hours"].mean()) if minor_gigo_mask.any() else None

            # Top counters (keep these — they’re useful)
            c1, c2, c3 = st.columns(3)
            c1.metric("Total Gate records", int(len(main_df)))
            c2.metric("Manual review records", int((main_df["Manual_Review_Flag"] == True).sum()))
            c3.metric(
                "Corrections (system bill used)",
                int((main_df["TAT_Correction_Source"] == "System_BillDate_As_GateOut").sum())
            )

            # Show the NEW KPIs
            st.markdown("### Key Metrics")

            k1, k2, k3 = st.columns(3)
            k1.metric("Average TAT (all valid rows)", f"{avg_tat_all_valid:.2f} hrs" if avg_tat_all_valid is not None else "N/A")
            k2.metric("# Minor jobs (ROT ≤ 2h)", f"{minor_jobs_count}")
            k3.metric("Avg TAT of minor jobs", f"{avg_tat_minor:.2f} hrs" if avg_tat_minor is not None else "N/A")

            k4, k5 = st.columns(2)
            k4.metric("Avg GIGO TAT Delay (all jobs)", f"{avg_gigo_all:.2f} hrs" if avg_gigo_all is not None else "N/A")
            k5.metric("Avg GIGO Delay (minor jobs)", f"{avg_gigo_minor:.2f} hrs" if avg_gigo_minor is not None else "N/A")

            # Helpful small note so clients understand the minor-job counts
            if rot_col:
                st.caption(f"Minor jobs are calculated only where ROT hours are available (jobs with ROT available: {jobs_with_rot}).")
            else:
                st.caption("ROT hours column not found in output, so minor job metrics are not available.")

           
            st.markdown("### Charts")
            st.image(result_paths["chart_no_manual"], caption="TAT buckets (No manual check required)", use_container_width=True)
            st.image(result_paths["chart_all_tat"], caption="TAT buckets (All records with TAT)", use_container_width=True)
            st.image(result_paths["chart_waterfall"], caption="Waterfall distribution", use_container_width=True)

            st.markdown("### Download outputs")
            def _download_button(label: str, path: str):
                data = Path(path).read_bytes()
                st.download_button(label, data, file_name=Path(path).name)

            dl1, dl2, dl3, dl4 = st.columns(4)
            with dl1: _download_button("⬇️ Main Output CSV", result_paths["main_csv"])
            with dl2: _download_button("⬇️ Manual Review CSV", result_paths["manual_csv"])
            with dl3: _download_button("⬇️ Reg Suggestions CSV", result_paths["suggestions_csv"])
            with dl4: _download_button("⬇️ Statistics CSV", result_paths["stats_csv"])

            st.download_button(
                "⬇️ Download EVERYTHING (ZIP)",
                data=Path(result_paths["zip"]).read_bytes(),
                file_name="TAT_Outputs.zip",
                type="primary"
            )

            st.markdown("### Main Output preview (first 25 rows)")
            st.dataframe(main_df.head(25), use_container_width=True)
