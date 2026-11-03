import os
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from api import BackendError, NovaAPI
from styles import APP_CSS


st.set_page_config(
    page_title="Nova AI",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded"
)
st.markdown(APP_CSS, unsafe_allow_html=True)

DEFAULT_API_URL = os.getenv("NOVA_API_URL", "http://127.0.0.1:8000")
api = NovaAPI(DEFAULT_API_URL)
auth_storage = components.declare_component(
    "nova_ai_auth_storage",
    path=str(Path(__file__).parent / "auth_storage")
)


def initialize_state() -> None:
    defaults = {
        "auth_token": None,
        "auth_user": None,
        "auth_ready": False,
        "storage_action": "read",
        "auth_mode": "Sign in",
        "conversation_id": None,
        "conversation_created_at": None,
        "messages": [],
        "conversation_titles": {},
        "notice": None,
        "restore_attempted": False
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def clear_conversation_query_param() -> None:
    if "conversation_id" in st.query_params:
        del st.query_params["conversation_id"]


def display_time(value: str | None) -> str:
    if not value:
        return "Recent conversation"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%b %d · %H:%M")
    except ValueError:
        return value


def normalize_preview(value: str, limit: int = 42) -> str:
    text = " ".join(value.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def sync_browser_token() -> None:
    action = st.session_state["storage_action"]
    result = auth_storage(
        action=action,
        token=st.session_state["auth_token"],
        key="nova_ai_auth_storage"
    )
    if not isinstance(result, dict) or not result.get("ready"):
        return

    st.session_state["auth_ready"] = True
    if action == "read" and not st.session_state["auth_token"]:
        st.session_state["auth_token"] = result.get("token")
    elif action in ("write", "clear"):
        st.session_state["storage_action"] = "read"


def clear_local_user_state() -> None:
    st.session_state["auth_token"] = None
    st.session_state["auth_user"] = None
    st.session_state["conversation_id"] = None
    st.session_state["conversation_created_at"] = None
    st.session_state["messages"] = []
    st.session_state["conversation_titles"] = {}
    st.session_state["notice"] = None
    st.session_state["restore_attempted"] = False
    st.session_state["storage_action"] = "clear"
    clear_conversation_query_param()


def finish_authentication(payload: dict[str, Any]) -> None:
    st.session_state["auth_token"] = payload["access_token"]
    st.session_state["auth_user"] = payload["user"]
    st.session_state["storage_action"] = "write"
    st.session_state["conversation_id"] = None
    st.session_state["conversation_created_at"] = None
    st.session_state["messages"] = []
    st.session_state["conversation_titles"] = {}
    st.session_state["restore_attempted"] = False
    clear_conversation_query_param()
    st.rerun()


def render_auth_screen() -> None:
    left, center, right = st.columns([1, 1.15, 1])
    with center:
        st.markdown(
            """
            <div class="auth-card">
              <div class="brand-mark auth-mark">✦</div>
              <div class="eyebrow">A little more clarity, every day</div>
              <h1>Welcome to Nova AI</h1>
              <p>Sign in to continue to your private conversations.</p>
            </div>
            """,
            unsafe_allow_html=True
        )
        mode = st.radio(
            "Account",
            ("Sign in", "Create account"),
            horizontal=True,
            label_visibility="collapsed",
            key="auth_mode"
        )
        with st.form(f"auth_form_{mode}"):
            username = st.text_input(
                "Username",
                max_chars=32,
                autocomplete="username"
            )
            password = st.text_input(
                "Password",
                type="password",
                max_chars=128,
                autocomplete=(
                    "new-password" if mode == "Create account" else "current-password"
                )
            )
            confirm = ""
            if mode == "Create account":
                confirm = st.text_input(
                    "Confirm password",
                    type="password",
                    max_chars=128,
                    autocomplete="new-password"
                )
            submitted = st.form_submit_button(
                "Create account" if mode == "Create account" else "Sign in",
                type="primary",
                use_container_width=True
            )

        if submitted:
            if mode == "Create account" and password != confirm:
                st.error("Those passwords don't match.")
                return
            try:
                payload = (
                    api.register(username, password)
                    if mode == "Create account"
                    else api.login(username, password)
                )
                finish_authentication(payload)
            except BackendError as error:
                st.error(str(error))

        try:
            api.health()
        except BackendError:
            st.caption(
                "The backend is currently unavailable. Start FastAPI and try again."
            )


def validate_saved_token() -> bool:
    token = st.session_state["auth_token"]
    if not token:
        return False
    try:
        st.session_state["auth_user"] = api.current_user(token)
        return True
    except BackendError as error:
        clear_local_user_state()
        if error.status_code != 401:
            st.session_state["notice"] = str(error)
        else:
            st.session_state["notice"] = (
                "Your session expired. Please sign in again."
            )
        return False


def load_conversation(conversation_id: str) -> None:
    try:
        conversation = api.conversation(
            conversation_id,
            st.session_state["auth_token"]
        )
    except BackendError as error:
        if error.status_code == 404:
            st.session_state["conversation_id"] = None
            st.session_state["messages"] = []
            st.session_state["conversation_created_at"] = None
            clear_conversation_query_param()
            st.session_state["notice"] = (
                "That conversation is no longer available. Start a new chat "
                "to continue."
            )
            return
        st.error(str(error))
        return

    messages = conversation.get("messages", [])
    st.session_state["conversation_id"] = conversation_id
    st.session_state["conversation_created_at"] = conversation.get("created_at")
    st.query_params["conversation_id"] = conversation_id
    st.session_state["messages"] = [
        {"role": item["role"], "content": item["content"]}
        for item in messages
        if item.get("role") in ("user", "assistant")
    ]
    first_user_message = next(
        (
            item["content"] for item in st.session_state["messages"]
            if item["role"] == "user"
        ),
        None
    )
    if first_user_message:
        st.session_state["conversation_titles"][conversation_id] = (
            normalize_preview(first_user_message)
        )


def start_new_chat() -> None:
    st.session_state["conversation_id"] = None
    st.session_state["conversation_created_at"] = None
    st.session_state["messages"] = []
    st.session_state["notice"] = None
    clear_conversation_query_param()


def logout() -> None:
    token = st.session_state["auth_token"]
    try:
        if token:
            api.logout(token)
    except BackendError as error:
        st.warning(str(error))
    finally:
        clear_local_user_state()
    st.rerun()


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown(
            """
            <div class="brand">
              <div class="brand-mark">✦</div>
              <div>
                <div class="brand-name">Nova AI</div>
                <div class="brand-caption">Your thoughtful AI companion</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True
        )
        username = st.session_state["auth_user"]["username"]
        st.caption(f"Signed in as **{username}**")
        if st.button("Sign out", key="logout", use_container_width=True):
            logout()

        try:
            api.health()
            st.markdown(
                '<div class="connection"><span class="connection-dot online"></span>'
                'Backend connected</div>',
                unsafe_allow_html=True
            )
        except BackendError:
            st.markdown(
                '<div class="connection"><span class="connection-dot"></span>'
                'Backend unavailable</div>',
                unsafe_allow_html=True
            )

        if st.button(
            "＋  New chat",
            type="primary",
            use_container_width=True,
            key="new_chat"
        ):
            start_new_chat()
            st.rerun()

        st.markdown(
            '<div class="section-label">Your conversations</div>',
            unsafe_allow_html=True
        )
        try:
            conversations = api.conversations(st.session_state["auth_token"])
        except BackendError as error:
            if error.status_code == 401:
                clear_local_user_state()
                st.rerun()
            st.caption(str(error))
            conversations = []

        if not conversations:
            st.caption("Your chat history will show up here.")

        for item in conversations:
            conversation_id = item["conversation_id"]
            active = conversation_id == st.session_state["conversation_id"]
            title = st.session_state["conversation_titles"].get(
                conversation_id
            ) or normalize_preview(
                item.get("preview") or "New conversation"
            )
            count = int(item.get("message_count", 0))
            label = f"{'●' if active else '○'}  {title}"
            if not active and count:
                label += f"  ·  {count}"
            if st.button(
                label,
                key=f"conversation_{conversation_id}",
                type="primary" if active else "secondary",
                use_container_width=True,
                help=f"{display_time(item.get('created_at'))} · {count} messages"
            ):
                load_conversation(conversation_id)
                st.rerun()

        st.markdown(
            '<div class="brand-caption" style="margin-top:1.4rem">'
            'Your conversations are private to your account.</div>',
            unsafe_allow_html=True
        )


def render_welcome() -> str | None:
    st.markdown(
        """
        <div class="welcome">
          <div class="eyebrow">A little more clarity, every day</div>
          <h1>Meet Nova AI.</h1>
          <p>
            A thoughtful space to ask questions, explore ideas, and make
            progress on what matters to you. What would you like to work on?
          </p>
          <div class="prompt-label">A few ideas to get started</div>
        </div>
        """,
        unsafe_allow_html=True
    )
    prompts = [
        "Explain a complex idea simply",
        "Help me brainstorm a project",
        "Teach me something new"
    ]
    columns = st.columns(3)
    for column, prompt in zip(columns, prompts):
        with column:
            if st.button(
                prompt,
                key=f"example_{prompt}",
                use_container_width=True
            ):
                return prompt
    return None


def render_messages() -> None:
    for message in st.session_state["messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])


def send_message(prompt: str) -> None:
    token = st.session_state["auth_token"]
    conversation_id = st.session_state["conversation_id"]
    try:
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Nova is thinking…"):
                result = api.send_message(
                    prompt,
                    conversation_id,
                    token
                )
    except BackendError as error:
        if error.status_code == 404:
            st.session_state["conversation_id"] = None
            st.session_state["conversation_created_at"] = None
            st.session_state["messages"] = []
            clear_conversation_query_param()
            st.session_state["notice"] = (
                "This conversation is no longer available. Start a new chat "
                "to continue."
            )
        elif error.status_code == 401:
            clear_local_user_state()
            st.session_state["notice"] = "Your session expired. Please sign in again."
        else:
            st.error(str(error))
        return

    response_id = result["conversation_id"]
    st.session_state["conversation_id"] = response_id
    st.session_state["conversation_created_at"] = (
        st.session_state["conversation_created_at"]
        or datetime.now().isoformat()
    )
    st.query_params["conversation_id"] = response_id
    st.session_state["messages"].extend([
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": result["response"]}
    ])
    st.session_state["conversation_titles"][response_id] = (
        normalize_preview(prompt)
    )
    st.session_state["notice"] = None
    st.rerun()


initialize_state()
sync_browser_token()

if not st.session_state["auth_ready"] and not st.session_state["auth_token"]:
    st.info("Restoring your session…")
    st.stop()

if not validate_saved_token():
    if st.session_state.get("notice"):
        st.warning(st.session_state["notice"])
    render_auth_screen()
    st.stop()

if not st.session_state["restore_attempted"]:
    st.session_state["restore_attempted"] = True
    saved_conversation_id = st.query_params.get("conversation_id")
    if saved_conversation_id:
        load_conversation(saved_conversation_id)

render_sidebar()

if st.session_state.get("notice"):
    st.warning(st.session_state["notice"])

if st.session_state["messages"]:
    render_messages()
else:
    example_prompt = render_welcome()
    if example_prompt:
        send_message(example_prompt)

prompt = st.chat_input(
    "Message Nova AI…",
    max_chars=12000,
    key="chat_input"
)
if prompt and prompt.strip():
    send_message(prompt.strip())
