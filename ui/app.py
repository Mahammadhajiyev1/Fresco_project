"""Streamlit review UI.  Run:  streamlit run ui/app.py   (API must be up: uvicorn hwsets.api:app)"""
from __future__ import annotations

import io
import os

import pandas as pd
import requests
import streamlit as st
from PIL import Image, ImageDraw

API = os.getenv("HWSETS_API", "http://localhost:8000")
ZOOM = 1.5
COMP_COLS = ["qty", "description", "catalog_number", "mfr", "finish", "notes", "confidence"]

st.set_page_config(page_title="Hardware Set Extractor", layout="wide")
st.title("Hardware Set Extractor")

# ---------------------------------------------------------------- upload / load
with st.sidebar:
    up = st.file_uploader("Specbook PDF", type="pdf")
    pages = st.text_input("Pages (optional)", placeholder="e.g. 40-52,60")
    if up and st.button("Extract", type="primary"):
        with st.spinner("Extracting... (one Claude call per few pages)"):
            r = requests.post(f"{API}/extract", files={"file": (up.name, up.getvalue(), "application/pdf")},
                              params={"pages": pages} if pages else None, timeout=1800)
        if r.ok:
            st.session_state.update(doc_id=r.json()["id"], result=r.json()["result"])
        else:
            st.error(f"{r.status_code}: {r.text[:300]}")
    with st.expander("Load earlier result"):
        prev = st.text_input("Result id")
        if prev and st.button("Load"):
            r = requests.get(f"{API}/results/{prev}")
            if r.ok:
                st.session_state.update(doc_id=prev, result=r.json())
            else:
                st.error("not found")

result = st.session_state.get("result")
doc_id = st.session_state.get("doc_id")
if not result:
    st.info("Upload a specbook PDF and click Extract.")
    st.stop()

for w in result.get("warnings", []):
    st.warning(w)

# ---------------------------------------------------------------- set list
sets = result["sets"]
st.caption(f"{result['document']} - {len(sets)} sets - pages scanned: {result['pages_scanned']}")
# The labels are a SNAPSHOT taken when the result is loaded. If they were rebuilt from the live (edited) values,
# typing a new description would change the radio's options, Streamlit would treat it as a new widget and jump
# back to the first set, losing the click on "Save corrections".
labels_key = f"labels:{doc_id}"
if labels_key not in st.session_state or len(st.session_state[labels_key]) != len(sets):
    st.session_state[labels_key] = [
        f"Set {s['set_number']}" + (f" - {s['description']}" if s.get("description") else "")
        + ("  [NOT USED]" if s["status"] == "not_used" else "")
        + (f"  ⚠ {s['confidence']:.2f}" if s["confidence"] < 0.75 else "")
        for s in sets
    ]
labels = st.session_state[labels_key]
left, right = st.columns([3, 2])
idx = (
    st.sidebar.radio("Sets", range(len(sets)), format_func=lambda i: labels[i], key=f"set_pick:{doc_id}")
    if sets else None
)
if idx is None:
    st.stop()
s = sets[idx]

# ---------------------------------------------------------------- editable components
with left:
    st.subheader(labels[idx])
    c1, c2, c3 = st.columns(3)
    s["set_number"] = c1.text_input("Set number", s["set_number"], key=f"num{idx}")
    s["description"] = c2.text_input("Description", s.get("description") or "", key=f"desc{idx}") or None
    s["status"] = c3.selectbox("Status", ["active", "not_used"], index=["active", "not_used"].index(s["status"]),
                               key=f"st{idx}")
    df = pd.DataFrame(s["components"], columns=COMP_COLS + ["warnings"])
    edited = st.data_editor(
        df.drop(columns=["warnings"]), num_rows="dynamic", use_container_width=True, key=f"ed{idx}",
        column_config={"confidence": st.column_config.ProgressColumn("conf", min_value=0, max_value=1, format="%.2f"),
                       "qty": st.column_config.NumberColumn("qty", step=1)},
    )
    for i, w in enumerate(df["warnings"]):
        if w:
            st.caption(f"row {i + 1}: " + "; ".join(w))
    if st.button("Save corrections", key=f"save{idx}"):
        old = s["components"]
        new = []
        for i, row in edited.iterrows():
            base = dict(old[i]) if i < len(old) else {"source_line_ids": [], "warnings": []}
            base.update({k: (None if pd.isna(row[k]) or row[k] == "" else row[k]) for k in COMP_COLS if k != "confidence"})
            base["qty"] = None if base["qty"] is None else int(base["qty"])
            base["description"] = base["description"] or ""
            new.append(base)
        s["components"] = new
        r = requests.put(f"{API}/results/{doc_id}", json=result)
        st.success("Saved") if r.ok else st.error(r.text[:300])
    st.download_button("Download JSON", data=requests.get(f"{API}/results/{doc_id}").text,
                       file_name=f"{doc_id}.json", mime="application/json")

# ---------------------------------------------------------------- page image with bbox overlay
with right:
    locs = s["locations"]
    if not locs:
        st.warning("No location found for this set.")
    else:
        page_no = st.selectbox("Page", [l["page"] for l in locs])
        loc = next(l for l in locs if l["page"] == page_no)
        img = Image.open(io.BytesIO(requests.get(f"{API}/results/{doc_id}/page/{page_no}.png",
                                                 params={"zoom": ZOOM}).content)).convert("RGB")
        d = ImageDraw.Draw(img, "RGBA")
        b = loc["bbox"]
        d.rectangle([b["x0"] * ZOOM - 4, b["y0"] * ZOOM - 4, b["x1"] * ZOOM + 4, b["y1"] * ZOOM + 4],
                    outline=(220, 30, 30, 255), fill=(255, 0, 0, 30), width=3)
        st.image(img, caption=f"Page {page_no}", use_container_width=True)
