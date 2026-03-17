# Workshop TAT Calculator (PoC)

This is a proof-of-concept Streamlit portal:
- Upload Gate Register (Primary)
- Upload System/Tableau Extract (Validation)
- Click **Calculate**
- Download outputs (Main Output + audit files + charts)

## Files
- `streamlit_app.py` : Streamlit UI (entrypoint)
- `tat_engine.py` : Processing engine (cleaning, matching, TAT, flags, outputs)
- `requirements.txt` : Dependencies

## Deploy on Streamlit Community Cloud
1. Create a new GitHub repo.
2. Upload these files to the repo root (keep folder `.streamlit/` too).
3. In Streamlit Community Cloud: **New app** → select your repo/branch → set **Main file path** to:
   - `streamlit_app.py`
4. Click **Deploy**.

## Notes
- PoC is hosted on Streamlit Community Cloud → avoid sensitive client data.
- For production we can package this as an on‑prem Docker app later.
