
# tat_engine.py
# Deterministic TAT engine (Gate Register = source of truth)

from __future__ import annotations
import os, re, zipfile
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional, List, Dict, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# -------------------------
# File reading helpers
# -------------------------

def _is_excel(path: str) -> bool:
    p = path.lower()
    return p.endswith(".xlsx") or p.endswith(".xlsm") or p.endswith(".xls")

def list_sheets(path: str) -> List[str]:
    if not _is_excel(path):
        return []
    return pd.ExcelFile(path).sheet_names

def read_table(path: str, sheet_name: Optional[str] = None) -> pd.DataFrame:
    if _is_excel(path):
        return pd.read_excel(path, sheet_name=sheet_name)
    return pd.read_csv(path)

def find_column(df: pd.DataFrame, candidates: List[str], required: bool = True) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    if required:
        raise KeyError(f"Missing required column. Tried: {candidates}. Found: {list(df.columns)}")
    return None


# -------------------------
# Registration cleaning
# -------------------------

STRICT_CLASSES = ["L","L","D","D","L","L","D","D","D","D"]
LETTER_TO_DIGIT = {"O":"0","I":"1","S":"5","B":"8","Z":"2"}
DIGIT_TO_LETTER = {"0":"O","1":"I","5":"S","2":"Z","8":"B"}

def clean_reg_alnum(reg) -> Optional[str]:
    if pd.isna(reg):
        return None
    s = re.sub(r"[^A-Z0-9]", "", str(reg).strip().upper())
    return s or None

def _score_window(window10: str) -> Tuple[int, str]:
    out = []
    ok = 0
    for i, ch in enumerate(window10):
        exp = STRICT_CLASSES[i]
        if exp == "D":
            if ch.isalpha():
                ch = LETTER_TO_DIGIT.get(ch, ch)
            if ch.isdigit():
                ok += 1
        else:
            if ch.isdigit():
                ch = DIGIT_TO_LETTER.get(ch, ch)
            if ch.isalpha():
                ok += 1
        out.append(ch)
    return ok, "".join(out)

def clean_reg_strict(reg) -> Tuple[Optional[str], bool]:
    """
    Returns (AA00AA0000 or None, invalid_flag).
    Deterministic: best 10-char window by pattern score, then earliest.
    """
    s = clean_reg_alnum(reg)
    if not s or len(s) < 10:
        return None, True

    best_score, best = -1, None
    for start in range(0, len(s) - 10 + 1):
        score, corrected = _score_window(s[start:start+10])
        if score > best_score:
            best_score, best = score, corrected

    return (best, False) if (best_score == 10 and best is not None) else (None, True)


# -------------------------
# Date parsing
# -------------------------

MMDDYYYY = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s*$")
TIME = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$")
SYS_DT = re.compile(r"^\s*(\d{1,2}):(\d{1,2}):(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})\s*$")

def parse_gate_dt(date_val, time_val) -> Optional[pd.Timestamp]:
    if pd.isna(date_val) or pd.isna(time_val):
        return None

    # if date already datetime
    if isinstance(date_val, (pd.Timestamp, datetime)) and not pd.isna(date_val):
        d = pd.Timestamp(date_val).date()
        t_str = str(time_val).strip().replace(".", ":")
        m = TIME.match(t_str)
        if not m:
            return None
        hh, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        try:
            return pd.Timestamp(datetime(d.year, d.month, d.day, hh, mm, ss))
        except Exception:
            return None

    m = MMDDYYYY.match(str(date_val).strip())
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000

    t_str = str(time_val).strip().replace(".", ":")
    m2 = TIME.match(t_str)
    if not m2:
        return None
    hh, mm, ss = int(m2.group(1)), int(m2.group(2)), int(m2.group(3) or 0)

    try:
        return pd.Timestamp(datetime(year, month, day, hh, mm, ss))
    except Exception:
        return None

def parse_system_dt(val) -> Optional[pd.Timestamp]:
    if pd.isna(val):
        return None
    if isinstance(val, (pd.Timestamp, datetime)) and not pd.isna(val):
        return pd.Timestamp(val)
    m = SYS_DT.match(str(val).strip())
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh, mm, ss = int(m.group(4)), int(m.group(5)), int(m.group(6))
    try:
        return pd.Timestamp(datetime(year, month, day, hh, mm, ss))
    except Exception:
        return None

def _fmt_date(ts) -> Optional[str]:
    return None if pd.isna(ts) else pd.Timestamp(ts).strftime("%m/%d/%Y")

def _fmt_time(ts) -> Optional[str]:
    return None if pd.isna(ts) else pd.Timestamp(ts).strftime("%H:%M:%S")


# -------------------------
# Matching
# -------------------------

def ratio(a: Optional[str], b: Optional[str]) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio() * 100.0

def _best_match(
    gate_reg: Optional[str],
    gate_start: Optional[pd.Timestamp],
    sys: pd.DataFrame,
    sys_reg_key: str,
    sys_gatein: str,
    sys_job: Optional[str],
    min_score: float = 90.0
) -> Tuple[Optional[int], float, str, Optional[float]]:
    if not gate_reg:
        return None, 0.0, "No_Registration", None

    scores = sys[sys_reg_key].fillna("").astype(str).apply(lambda x: ratio(gate_reg, x))
    best_score = float(scores.max()) if len(scores) else 0.0
    hi = sys.loc[scores >= min_score].copy()
    if hi.empty:
        return None, best_score, "No_Match", None

    hi["_score"] = scores.loc[hi.index].astype(float)
    if gate_start is not None and pd.notna(gate_start):
        hi["_td"] = hi[sys_gatein].apply(lambda x: abs((pd.Timestamp(x) - pd.Timestamp(gate_start)).total_seconds())/3600.0 if pd.notna(x) else float("inf"))
    else:
        hi["_td"] = float("inf")

    # deterministic tie-breakers
    hi["_sys_gatein_sort"] = pd.to_datetime(hi[sys_gatein], errors="coerce").fillna(pd.Timestamp.max)
    if sys_job and sys_job in hi.columns:
        hi["_job_sort"] = pd.to_numeric(hi[sys_job], errors="coerce").fillna(1e18)
    else:
        hi["_job_sort"] = 1e18

    best = hi.sort_values(["_td","_score","_sys_gatein_sort","_job_sort"], ascending=[True,False,True,True], kind="mergesort").iloc[0]
    idx = int(best.name)
    sc = float(best["_score"])
    td = None if best["_td"] == float("inf") else float(best["_td"])
    mt = "Exact" if abs(sc-100.0) < 1e-9 else "Fuzzy"
    return idx, sc, mt, td


# -------------------------
# Pipeline
# -------------------------

def run_tat_pipeline(
    gate_path: str,
    sys_path: str,
    out_dir: str,
    gate_sheet: Optional[str] = None,
    sys_sheet: Optional[str] = None,
) -> Dict[str, str]:
    """
    Writes outputs into out_dir and returns a dict of file paths.
    """
    os.makedirs(out_dir, exist_ok=True)

    gate = read_table(gate_path, sheet_name=gate_sheet)
    sys  = read_table(sys_path, sheet_name=sys_sheet)

    # Gate columns
    g_reg = find_column(gate, ["Reg No.","Regn. No.","Reg No","Registration No","Vehicle Reg"])
    g_rep_date = find_column(gate, ["Reporting Date"])
    g_rep_time = find_column(gate, ["Reporting Time"])
    g_in_date  = find_column(gate, ["Workshop In Date ","Workshop In Date","Gate In Date"])
    g_in_time  = find_column(gate, ["Workshop In Time","Gate In Time"])
    g_out_date = find_column(gate, ["Workshop Out Date","Gate Out Date"])
    g_out_time = find_column(gate, ["Workshop Out Time","Gate Out Time"])

    # System columns
    s_reg = find_column(sys, ["License Plate Number","Reg No.","Regn. No.","Registration No"])
    s_job = find_column(sys, ["Job Card No.","JobCard No.","JC No"], required=False)
    s_gatein = find_column(sys, ["Actual Gate in Date/Time","Gate In Date/Time"])
    s_bill   = find_column(sys, ["Bill Date/Time"])
    s_rot    = find_column(sys, ["Total ROT Hours","ROT Hours","ROT Hrs"], required=False)

    # Clean regs
    gate["Clean_Reg_Alnum"] = gate[g_reg].apply(clean_reg_alnum)
    tmp = gate[g_reg].apply(clean_reg_strict)
    gate["Clean_Reg_Strict"] = tmp.apply(lambda x: x[0])
    gate["Flag_Invalid_Reg_Format"] = tmp.apply(lambda x: bool(x[1]))
    gate["Match_Reg_Key"] = gate["Clean_Reg_Strict"].fillna(gate["Clean_Reg_Alnum"])

    sys["Clean_Reg_Alnum"] = sys[s_reg].apply(clean_reg_alnum)
    tmp2 = sys[s_reg].apply(clean_reg_strict)
    sys["Clean_Reg_Strict"] = tmp2.apply(lambda x: x[0])
    sys["Match_Reg_Key"] = sys["Clean_Reg_Strict"].fillna(sys["Clean_Reg_Alnum"])

    # Parse gate datetimes
    gate["Gate_Reporting_DT"] = gate.apply(lambda r: parse_gate_dt(r[g_rep_date], r[g_rep_time]), axis=1)
    gate["Gate_In_DT"]        = gate.apply(lambda r: parse_gate_dt(r[g_in_date],  r[g_in_time]), axis=1)
    gate["Gate_Out_DT"]       = gate.apply(lambda r: parse_gate_dt(r[g_out_date], r[g_out_time]), axis=1)

    gate["TAT_Start_DT"] = gate["Gate_Reporting_DT"].where(gate["Gate_Reporting_DT"].notna(), gate["Gate_In_DT"])
    gate["TAT_Start_Source"] = np.where(gate["Gate_Reporting_DT"].notna(), "Reporting", np.where(gate["Gate_In_DT"].notna(), "Gate_In", "Missing"))
    gate["Flag_Invalid_Gate_DateTime"] = gate["TAT_Start_DT"].isna() | gate["Gate_Out_DT"].isna()

    # Parse system datetimes
    sys["System_Gate_In_DT"] = sys[s_gatein].apply(parse_system_dt)
    sys["System_Bill_DT"]    = sys[s_bill].apply(parse_system_dt)
    sys["System_ROT_Hours"]  = pd.to_numeric(sys[s_rot], errors="coerce") if (s_rot and s_rot in sys.columns) else np.nan

    # Match
    idxs=[]; scores=[]; types=[]; tds=[]
    for _, r in gate.iterrows():
        i, sc, mt, td = _best_match(r["Match_Reg_Key"], r["TAT_Start_DT"], sys, "Match_Reg_Key", "System_Gate_In_DT", s_job, 90.0)
        idxs.append(i); scores.append(sc); types.append(mt); tds.append(td)
    gate["Matched_System_Row"] = idxs
    gate["Match_Score"] = scores
    gate["Match_Type"] = types
    gate["Time_Diff_Hours"] = tds

    def _sysval(i, col):
        if i is None or (isinstance(i, float) and np.isnan(i)):
            return None
        try:
            return sys.loc[int(i), col]
        except Exception:
            return None

    gate["Matched_JobCard_No"] = gate["Matched_System_Row"].apply(lambda i: _sysval(i, s_job) if s_job else None)
    gate["System_Reg_No"]      = gate["Matched_System_Row"].apply(lambda i: _sysval(i, s_reg))
    gate["System_ROT_Hours_Matched"] = gate["Matched_System_Row"].apply(lambda i: _sysval(i, "System_ROT_Hours"))
    gate["System_Gate_In_DT_Matched"] = gate["Matched_System_Row"].apply(lambda i: _sysval(i, "System_Gate_In_DT"))
    gate["System_Bill_DT_Matched"]    = gate["Matched_System_Row"].apply(lambda i: _sysval(i, "System_Bill_DT"))

    # TAT
    def hours(a,b):
        if pd.isna(a) or pd.isna(b):
            return np.nan
        return (pd.Timestamp(b) - pd.Timestamp(a)).total_seconds()/3600.0

    gate["Original_TAT_Hours"] = gate.apply(lambda r: hours(r["TAT_Start_DT"], r["Gate_Out_DT"]), axis=1)
    gate["GIGO_TAT_Delay_Hours"] = np.where(
        gate["Original_TAT_Hours"].notna() & gate["System_ROT_Hours_Matched"].notna(),
        gate["Original_TAT_Hours"] - gate["System_ROT_Hours_Matched"] - 4.0,
        np.nan
    )

    # Correction: only negative TAT via system bill DT
    gate["Corrected_Gate_In_DT"] = gate["TAT_Start_DT"]
    gate["Corrected_Gate_Out_DT"] = gate["Gate_Out_DT"]
    gate["Corrected_TAT_Hours"] = gate["Original_TAT_Hours"]
    gate["TAT_Correction_Source"] = "Gate_Register"

    neg = gate["Original_TAT_Hours"].notna() & (gate["Original_TAT_Hours"] < 0)
    for i in gate[neg].index:
        start = gate.at[i, "TAT_Start_DT"]
        bill  = gate.at[i, "System_Bill_DT_Matched"]
        if pd.notna(start) and pd.notna(bill):
            new_tat = hours(start, bill)
            if pd.notna(new_tat) and new_tat >= 0:
                gate.at[i, "Corrected_Gate_Out_DT"] = bill
                gate.at[i, "Corrected_TAT_Hours"] = new_tat
                gate.at[i, "TAT_Correction_Source"] = "System_BillDate_As_GateOut"

    # Validations on corrected TAT
    gate["Flag_GateIn_After_GateOut"] = gate["Corrected_Gate_In_DT"].notna() & gate["Corrected_Gate_Out_DT"].notna() & (gate["Corrected_Gate_In_DT"] > gate["Corrected_Gate_Out_DT"])
    gate["Flag_TAT_Too_Short"] = gate["Corrected_TAT_Hours"].notna() & (gate["Corrected_TAT_Hours"] >= 0) & (gate["Corrected_TAT_Hours"] < 0.25)
    gate["Flag_TAT_Too_Long"]  = gate["Corrected_TAT_Hours"].notna() & (gate["Corrected_TAT_Hours"] > 336)
    gate["Flag_Missing_TAT"]   = gate["Corrected_TAT_Hours"].isna()
    gate["Flag_Low_No_Match"]  = gate["Match_Score"].isna() | (gate["Match_Score"] < 90.0)
    gate["Flag_ROT_Mismatch_GT_TAT"] = False
    rot_app = gate["Flag_TAT_Too_Short"] & gate["System_ROT_Hours_Matched"].notna()
    gate.loc[rot_app, "Flag_ROT_Mismatch_GT_TAT"] = gate.loc[rot_app, "System_ROT_Hours_Matched"] > gate.loc[rot_app, "Corrected_TAT_Hours"]

    gate["_Neg_Unresolved"] = neg & (gate["TAT_Correction_Source"] != "System_BillDate_As_GateOut")

    def reason_row(r) -> str:
        order = [
            ("Flag_Invalid_Reg_Format","Invalid_Reg"),
            ("Flag_Invalid_Gate_DateTime","Invalid_Gate_DT"),
            ("Flag_Missing_TAT","Missing_TAT"),
            ("_Neg_Unresolved","Negative_TAT_Unresolved"),
            ("Flag_GateIn_After_GateOut","GateIn_After_GateOut"),
            ("Flag_TAT_Too_Short","Very_Short_TAT"),
            ("Flag_TAT_Too_Long","Very_Long_TAT"),
            ("Flag_Low_No_Match","Low_No_Match"),
            ("Flag_ROT_Mismatch_GT_TAT","ROT_Mismatch_GT_TAT"),
        ]
        out=[]
        for f, lab in order:
            if bool(r.get(f, False)):
                out.append(lab)
        return "; ".join(out)

    gate["Manual_Review_Reason"] = gate.apply(reason_row, axis=1)
    gate["Manual_Review_Flag"] = gate["Manual_Review_Reason"].astype(str).str.len() > 0

    # Suggestions (±2h) for invalid reg OR low/no match
    sugg=[]
    for row_i, r in gate.iterrows():
        need = bool(r["Flag_Invalid_Reg_Format"]) or bool(r["Flag_Low_No_Match"]) or (r["Match_Type"] in ["No_Match","No_Registration"])
        start = r["TAT_Start_DT"]
        if (not need) or pd.isna(start):
            continue
        tmp = sys.copy()
        tmp["_diff_h"] = tmp["System_Gate_In_DT"].apply(lambda x: abs((pd.Timestamp(x)-pd.Timestamp(start)).total_seconds())/3600.0 if pd.notna(x) else np.inf)
        cand = tmp[tmp["_diff_h"]<=2.0].sort_values("_diff_h", kind="mergesort").head(5)
        for _, c in cand.iterrows():
            diff=float(c["_diff_h"])
            conf="High" if diff<=0.5 else "Medium" if diff<=1.0 else "Low"
            sugg.append({
                "Gate_S_No": int(row_i)+1,
                "Gate_Reg_No": r[g_reg],
                "Gate_TAT_Start_DT": start,
                "System_Reg_No": c[s_reg],
                "System_JobCard_No": c[s_job] if s_job else None,
                "System_Gate_In_DT": c["System_Gate_In_DT"],
                "System_Bill_DT": c["System_Bill_DT"],
                "Time_Diff_Hours": diff,
                "Confidence": conf,
                "Reason": "Invalid_Reg" if bool(r["Flag_Invalid_Reg_Format"]) else "Low_No_Match"
            })
    sugg_df = pd.DataFrame(sugg)

    # Add system split strings to gate output (matched)
    gate["System_GateIn_Date"] = gate["System_Gate_In_DT_Matched"].apply(_fmt_date)
    gate["System_GateIn_Time"] = gate["System_Gate_In_DT_Matched"].apply(_fmt_time)
    gate["System_Bill_Date"]   = gate["System_Bill_DT_Matched"].apply(_fmt_date)
    gate["System_Bill_Time"]   = gate["System_Bill_DT_Matched"].apply(_fmt_time)

    # Outputs
    main = gate.copy()
    main.insert(0, "S_No", range(1, len(main)+1))

    main_csv = os.path.join(out_dir, "TAT_Analysis_Main_Output.csv")
    manual_csv = os.path.join(out_dir, "TAT_Analysis_Manual_Review.csv")
    sugg_csv = os.path.join(out_dir, "TAT_Analysis_Registration_Corrections.csv")
    stats_csv = os.path.join(out_dir, "TAT_Analysis_Statistics_Summary.csv")

    main.to_csv(main_csv, index=False)
    main[main["Manual_Review_Flag"]==True].to_csv(manual_csv, index=False)
    sugg_df.to_csv(sugg_csv, index=False)

    # Stats (valid corrected TAT only)
    valid = main[main["Corrected_TAT_Hours"].notna() & (main["Corrected_TAT_Hours"]>=0)]
    rows=[]
    def add(k,v): rows.append({"Metric":k,"Value":v})
    add("Total records processed", len(main))
    add("Records with TAT calculated (valid corrected)", int(len(valid)))
    add("Records with system validation available (>=90)", int((main["Match_Score"]>=90).sum()))
    add("Corrections applied (system bill used)", int((main["TAT_Correction_Source"]=="System_BillDate_As_GateOut").sum()))
    if len(valid):
        s = valid["Corrected_TAT_Hours"].astype(float)
        add("Mean TAT (hrs)", float(s.mean()))
        add("Median TAT (hrs)", float(s.median()))
        add("Min TAT (hrs)", float(s.min()))
        add("Max TAT (hrs)", float(s.max()))
        add("P25 TAT (hrs)", float(s.quantile(0.25)))
        add("P75 TAT (hrs)", float(s.quantile(0.75)))
        add("P90 TAT (hrs)", float(s.quantile(0.90)))
        add("P95 TAT (hrs)", float(s.quantile(0.95)))
    else:
        for k in ["Mean TAT (hrs)","Median TAT (hrs)","Min TAT (hrs)","Max TAT (hrs)","P25 TAT (hrs)","P75 TAT (hrs)","P90 TAT (hrs)","P95 TAT (hrs)"]:
            add(k, None)
    add("Match Exact (100)", int((main["Match_Score"]==100).sum()))
    add("Match Fuzzy (90-99.999)", int(((main["Match_Score"]>=90)&(main["Match_Score"]<100)).sum()))
    add("Match < 90", int((main["Match_Score"]<90).sum()))
    pd.DataFrame(rows).to_csv(stats_csv, index=False)

    # Charts
    def bucket(series):
        labels=["0-4 Hours","4-8 Hours","8-16 Hours","16-24 Hours","24-48 Hours","48+ Hours"]
        bins=[0,4,8,16,24,48,np.inf]
        if series.empty:
            return pd.Series([0]*6, index=labels)
        cat = pd.cut(series, bins=bins, labels=labels, include_lowest=True)
        return cat.value_counts().reindex(labels, fill_value=0)

    ok = main[(main["Manual_Review_Flag"]==False) & main["Corrected_TAT_Hours"].notna() & (main["Corrected_TAT_Hours"]>=0)]
    all_tat = main[main["Corrected_TAT_Hours"].notna() & (main["Corrected_TAT_Hours"]>=0)]

    b1 = bucket(ok["Corrected_TAT_Hours"].astype(float)) if len(ok) else bucket(pd.Series(dtype=float))
    b2 = bucket(all_tat["Corrected_TAT_Hours"].astype(float)) if len(all_tat) else bucket(pd.Series(dtype=float))

    c1 = os.path.join(out_dir, "TAT_Buckets_No_Manual_Check.png")
    plt.figure(); b1.plot(kind="bar"); plt.title("TAT Buckets (No Manual Check Required)")
    plt.xlabel("TAT buckets"); plt.ylabel("Records"); plt.tight_layout(); plt.savefig(c1); plt.close()

    c2 = os.path.join(out_dir, "TAT_Buckets_All_With_TAT.png")
    plt.figure(); b2.plot(kind="bar"); plt.title("TAT Buckets (All Records with TAT)")
    plt.xlabel("TAT buckets"); plt.ylabel("Records"); plt.tight_layout(); plt.savefig(c2); plt.close()

    # Waterfall (disjoint precedence: corrected > no match > fuzzy > ok)
    total = len(main)
    is_corrected = main["TAT_Correction_Source"]=="System_BillDate_As_GateOut"
    after_corr = main[~is_corrected]
    is_no_match = after_corr["Match_Type"].isin(["No_Match","No_Registration"])
    after_nomatch = after_corr[~after_corr["Match_Type"].isin(["No_Match","No_Registration"])]
    is_fuzzy = (after_nomatch["Match_Type"]=="Fuzzy")
    corrected_cnt = int(is_corrected.sum())
    no_match_cnt  = int(is_no_match.sum())
    fuzzy_cnt     = int(is_fuzzy.sum())
    ok_cnt        = total - corrected_cnt - no_match_cnt - fuzzy_cnt

    c3 = os.path.join(out_dir, "TAT_Waterfall_Distribution.png")
    labels=["Total","Corrected","JC not created","Fuzzy (uncertain)","OK data"]
    vals=[total, -corrected_cnt, -no_match_cnt, -fuzzy_cnt, ok_cnt]
    plt.figure(); plt.bar(range(len(vals)), vals); plt.xticks(range(len(vals)), labels, rotation=12)
    plt.title("Waterfall: Record Distribution"); plt.ylabel("Count"); plt.tight_layout(); plt.savefig(c3); plt.close()

    # Zip bundle
    zip_path = os.path.join(out_dir, "TAT_Outputs.zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for f in [main_csv, manual_csv, sugg_csv, stats_csv, c1, c2, c3]:
            if os.path.exists(f):
                z.write(f, arcname=os.path.basename(f))

    return {
        "main_csv": main_csv,
        "manual_csv": manual_csv,
        "suggestions_csv": sugg_csv,
        "stats_csv": stats_csv,
        "chart_no_manual": c1,
        "chart_all_tat": c2,
        "chart_waterfall": c3,
        "zip": zip_path,
    }
