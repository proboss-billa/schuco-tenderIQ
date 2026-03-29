import os
import streamlit as st
import requests
import pandas as pd
from io import BytesIO

# --- CONFIG & STYLING ---
st.set_page_config(page_title="TenderIQ | AI Analysis", layout="wide", initial_sidebar_state="expanded")

API_BASE = os.getenv("API_BASE", "http://localhost:8000")

st.markdown("""
    <style>
    .stApp { background: #0f172a; color: white; }
    [data-testid="stSidebar"] { background-color: #0f172a !important; border-right: 1px solid rgba(255,255,255,0.1); }
    .metric-card {
        background: rgba(255, 255, 255, 0.03);
        padding: 20px; border-radius: 15px; border: 1px solid rgba(255, 255, 255, 0.1);
        text-align: center;
    }
    /* Modern button styling */
    .stButton>button { border-radius: 8px; transition: 0.3s; }
    </style>
    """, unsafe_allow_html=True)

# --- SESSION STATE ---
if "token" not in st.session_state:
    st.session_state["token"] = None
if "current_page" not in st.session_state:
    st.session_state["current_page"] = "🏠 Dashboard"
if "current_project_id" not in st.session_state:
    st.session_state["current_project_id"] = None
if "processing_complete" not in st.session_state:
    st.session_state["processing_complete"] = False


# --- HELPER: AUTH HEADERS ---
def get_headers():
    return {"Authorization": f"Bearer {st.session_state['token']}"} if st.session_state["token"] else {}


# --- AUTH PAGE ---
def show_auth_page():
    col1, col2, col3 = st.columns([1, 1.5, 1])
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.image("https://cdn-icons-png.flaticon.com/512/1055/1055644.png", width=70)
        st.title("TenderIQ")

        tab_log, tab_sign = st.tabs(["Login", "Create Account"])

        with tab_log:
            e = st.text_input("Email", key="l_email")
            p = st.text_input("Password", type="password", key="l_pass")
            if st.button("Sign In", use_container_width=True, type="primary"):
                res = requests.post(f"{API_BASE}/login", params={"email": e, "password": p})
                if res.status_code == 200:
                    st.session_state["token"] = res.json()["access_token"]
                    st.rerun()
                else:
                    st.error("Invalid credentials")

        with tab_sign:
            se = st.text_input("Email", key="s_email")
            sp = st.text_input("Password", type="password", key="s_pass")
            if st.button("Register", use_container_width=True):
                res = requests.post(f"{API_BASE}/signup", params={"email": se, "password": sp})
                if res.status_code == 200:
                    st.success("Account created! Switch to Login tab.")
                else:
                    st.error("Registration failed.")


# --- MAIN DASHBOARD ---
def show_main_app():
    # SIDEBAR NAV
    with st.sidebar:
        st.title("TenderIQ")
        st.markdown("---")
        for pg in ["🏠 Dashboard", "📂 Projects", "⚙️ Settings"]:
            if st.button(pg, use_container_width=True,
                         type="primary" if st.session_state["current_page"] == pg else "secondary"):
                st.session_state["current_page"] = pg
                st.rerun()

        st.markdown("---")
        if st.button("Logout", use_container_width=True):
            st.session_state.clear()
            st.rerun()

    # ROUTING
    page = st.session_state["current_page"]

    if page == "🏠 Dashboard":
        st.title("🚀 Intelligence Dashboard")

        # Stats Row
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown('<div class="metric-card"><h3>Active</h3><h2>12</h2></div>', unsafe_allow_html=True)
        c2.markdown('<div class="metric-card"><h3>Confidence</h3><h2>94%</h2></div>', unsafe_allow_html=True)
        c3.markdown('<div class="metric-card"><h3>Analyzed</h3><h2>158</h2></div>', unsafe_allow_html=True)
        c4.markdown('<div class="metric-card"><h3>Saved</h3><h2>42h</h2></div>', unsafe_allow_html=True)

        st.markdown("---")

        # UPLOAD SECTION
        st.subheader("📤 Analysis Hub")
        col_up1, col_up2 = st.columns([1, 1])
        with col_up1:
            p_name = st.text_input("Tender Reference Name", placeholder="e.g. Saudi-Rail-2026-01")
            p_desc = st.text_area("Scope / Description")

        with col_up2:
            up_files = st.file_uploader("Upload Tender Docs (PDF/Excel)", accept_multiple_files=True)
            if st.button("⚡ Create Project & Upload", use_container_width=True, type="primary"):
                if not p_name or not up_files:
                    st.warning("Project name and files are required.")
                else:
                    with st.status("Uploading documents...", expanded=True) as status:
                        # Prepare files for FastAPI
                        files_payload = [("files", (f.name, f.getvalue(), f.type)) for f in up_files]
                        data_payload = {"project_name": p_name, "project_description": p_desc}

                        resp = requests.post(f"{API_BASE}/projects/create",
                                             data=data_payload,
                                             files=files_payload,
                                             headers=get_headers())

                        if resp.status_code == 200:
                            st.session_state["current_project_id"] = resp.json()["project_id"]
                            status.update(label="Upload Complete!", state="complete")
                            st.toast(f"Project {p_name} created.")
                        else:
                            st.error(f"Error: {resp.text}")

        # PROCESSING & RESULTS
        if st.session_state["current_project_id"]:
            pid = st.session_state["current_project_id"]
            st.markdown("---")
            st.info(f"Project ID: **{pid}** is ready for neural extraction.")

            if st.button("🔍 Run Neural Analysis", use_container_width=True):
                with st.spinner("AI is analyzing clauses and BOQ items... (This may take a moment)"):
                    proc_res = requests.post(f"{API_BASE}/projects/{pid}/process", headers=get_headers())
                    if proc_res.status_code == 200:
                        st.session_state["processing_complete"] = True
                        st.balloons()

            if st.session_state["processing_complete"]:
                tab1, tab2 = st.tabs(["📊 Extracted Parameters", "💬 AI Tender Chat"])

                with tab1:
                    param_res = requests.get(f"{API_BASE}/projects/{pid}/parameters", headers=get_headers())
                    if param_res.status_code == 200:
                        df = pd.DataFrame(param_res.json()["parameters"])
                        st.dataframe(df, use_container_width=True)

                        # Export
                        buffer = BytesIO()
                        df.to_excel(buffer, index=False)
                        st.download_button("📥 Download Excel Report", data=buffer.getvalue(),
                                           file_name=f"tender_{pid}.xlsx")

                with tab2:
                    st.markdown("#### Chat with your Documents")
                    user_q = st.text_input("Ask a question (e.g., 'What are the liquidated damages?')")
                    if user_q:
                        with st.spinner("Consulting AI..."):
                            q_res = requests.post(f"{API_BASE}/projects/{pid}/query",
                                                  data={"query": user_q},
                                                  headers=get_headers())
                            if q_res.status_code == 200:
                                st.chat_message("assistant").write(q_res.json()["answer"])
                                with st.expander("View Sources"):
                                    st.write(q_res.json()["sources"])

    elif page == "📂 Projects":
        st.title("Project Archive")
        st.info("Querying database for historical projects...")
        # Add a GET /projects endpoint to your FastAPI to list these!


# --- ENTRY POINT ---
if st.session_state["token"] is None:
    show_auth_page()
else:
    show_main_app()