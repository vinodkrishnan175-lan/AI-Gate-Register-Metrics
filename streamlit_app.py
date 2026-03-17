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
            ok_df = main_df[(main_df["Manual_Review_Flag"] == False) & (main_df["Corrected_TAT_Hours"].notna()) & (main_df["Corrected_TAT_Hours"] >= 0)]
            all_df = main_df[(main_df["Corrected_TAT_Hours"].notna()) & (main_df["Corrected_TAT_Hours"] >= 0)]

            avg_ok = float(ok_df["Corrected_TAT_Hours"].mean()) if len(ok_df) else None
            avg_all = float(all_df["Corrected_TAT_Hours"].mean()) if len(all_df) else None

            c1, c2, c3 = st.columns(3)
            c1.metric("Total Gate records", int(len(main_df)))
            c2.metric("Manual review records", int((main_df["Manual_Review_Flag"] == True).sum()))
            c3.metric("Corrections (system bill used)", int((main_df["TAT_Correction_Source"] == "System_BillDate_As_GateOut").sum()))

            st.markdown("### Final Result")
            colA, colB = st.columns(2)
            with colA:
                st.metric("Average TAT (no manual check required)", f"{avg_ok:.2f} hrs" if avg_ok is not None else "N/A")
            with colB:
                st.metric("Average TAT (all records with TAT)", f"{avg_all:.2f} hrs" if avg_all is not None else "N/A")

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