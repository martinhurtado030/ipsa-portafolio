"""
supabase_client.py — Supabase connection helpers.

Two clients:
  - anon_client : uses the publishable/anon key → for Auth operations (sign-in, sign-up).
  - admin_client: uses the service/secret key   → for all data operations (bypasses RLS).

Both are cached as Streamlit resources (created once per server process).
"""

import streamlit as st
from supabase import create_client, Client


@st.cache_resource(show_spinner=False)
def get_anon_client() -> Client:
    """Anon/publishable key client — Auth operations only."""
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_ANON_KEY"],
    )


@st.cache_resource(show_spinner=False)
def get_admin_client() -> Client:
    """Service/secret key client — all DB operations (bypasses RLS)."""
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_SVC_KEY"],
    )
