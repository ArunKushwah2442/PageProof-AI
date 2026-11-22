import os
import requests
import streamlit as st

API = os.getenv("API_URL", "http://127.0.0.1:8000")
st.set_page_config(page_title="Smart Document Q&A", page_icon="📄")
st.title("📄 Smart Document Q&A Assistant")


def headers():
    return {"Authorization": f"Bearer {st.session_state.token}"}


# ---------- Login / Register ----------
if "token" not in st.session_state:
    tab1, tab2 = st.tabs(["Login", "Register"])
    with tab1:
        u = st.text_input("Username", key="lu")
        p = st.text_input("Password", type="password", key="lp")
        if st.button("Login"):
            r = requests.post(f"{API}/login", data={"username": u, "password": p})
            if r.ok:
                st.session_state.token = r.json()["access_token"]
                st.rerun()
            else:
                st.error(r.json().get("detail", "Login failed"))
    with tab2:
        u = st.text_input("New username", key="ru")
        p = st.text_input("New password", type="password", key="rp")
        if st.button("Register"):
            r = requests.post(f"{API}/register", json={"username": u, "password": p})
            if r.ok:
                st.success("Registered! Ab login karo.")
            else:
                st.error(r.json().get("detail"))
    st.stop()

# ---------- Sidebar: upload + document list ----------
with st.sidebar:
    if st.button("Logout"):
        del st.session_state.token
        st.rerun()

    st.header("Upload PDF")
    pdf = st.file_uploader("Choose a PDF", type="pdf")
    if pdf and st.button("Upload & Process"):
        with st.spinner("Processing PDF..."):
            r = requests.post(f"{API}/upload", headers=headers(),
                              files={"file": (pdf.name, pdf.getvalue(), "application/pdf")})
        if r.ok:
            st.success("Done!")
        else:
            st.error(r.json().get("detail"))

    docs = requests.get(f"{API}/documents", headers=headers()).json()
    st.header("Your documents")
    choice = None
    if docs:
        choice = st.selectbox("Select", docs, format_func=lambda d: f"{d['filename']} ({d['pages']} pages)")

if not choice:
    st.info("Pehle sidebar se ek PDF upload karo.")
    st.stop()

# ---------- Chat ----------
doc_id = choice["id"]
for chat in requests.get(f"{API}/history/{doc_id}", headers=headers()).json():
    st.chat_message("user").write(chat["question"])
    with st.chat_message("assistant"):
        st.write(chat["answer"])
        if chat["source_pages"]:
            st.caption(f"Pages: {chat['source_pages']}")

q = st.chat_input("Document ke baare mein kuch bhi pucho...")
if q:
    st.chat_message("user").write(q)
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            r = requests.post(f"{API}/ask", headers=headers(), json={"document_id": doc_id, "question": q})
        if r.ok:
            data = r.json()
            st.write(data["answer"])
            with st.expander("Sources"):
                for s in data["sources"]:
                    st.markdown(f"**Page {s['page']}**: {s['snippet']}...")
        else:
            st.error(r.json().get("detail", "Error"))
