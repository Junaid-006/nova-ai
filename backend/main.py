
from contextlib import asynccontextmanager
import re
import sqlite3
import uuid

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from backend import auth
from backend.context_manager import MAX_RECENT_MESSAGES, get_managed_context
from backend.llm import generate_response
from backend.database import (
    initialize_database,
    create_conversation_with_exchange,
    save_exchange,
    get_conversation_history,
    get_conversation,
    list_conversations,
    check_database,
    create_user,
    get_user_by_username,
    ConversationNotFoundError
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="Nova AI",
    description="An intelligent conversational assistant.",
    version="1.0.0",
    lifespan=lifespan
)


class ChatRequest(BaseModel):
    message: str = Field(max_length=12000)
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    response: str


class ConversationSummary(BaseModel):
    conversation_id: str
    created_at: str
    message_count: int
    preview: str


class MessageResponse(BaseModel):
    message_id: int
    role: str
    content: str
    created_at: str


class ConversationDetail(BaseModel):
    conversation_id: str
    created_at: str
    messages: list[MessageResponse]


class Credentials(BaseModel):
    username: str
    password: str


class UserInfo(BaseModel):
    user_id: str
    username: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    user: UserInfo


bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)
) -> dict:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"}
        )

    try:
        user = auth.get_session_user(credentials.credentials)
    except sqlite3.Error:
        raise HTTPException(
            status_code=503,
            detail="The database is temporarily unavailable."
        ) from None
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Session is invalid or expired.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return user


def _validate_username_and_password(username: str, password: str) -> str:
    normalized_username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,32}", normalized_username):
        raise HTTPException(
            status_code=400,
            detail="Username must be 3–32 characters using letters, numbers, '.', '_' or '-'."
        )
    if len(password) < 8 or len(password) > 128:
        raise HTTPException(
            status_code=400,
            detail="Password must be between 8 and 128 characters."
        )
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(
            status_code=400,
            detail="Password must be at most 72 UTF-8 bytes."
        )
    return normalized_username


def _issue_auth_response(user: dict) -> AuthResponse:
    token, expires_at = auth.create_session(user["user_id"])
    return AuthResponse(
        access_token=token,
        expires_at=expires_at,
        user={
            "user_id": user["user_id"],
            "username": user["username"]
        }
    )


@app.get("/")
def home():
    return {
        "name": "Nova AI",
        "status": "online"
    }


@app.get("/health")
def health():
    try:
        check_database()
    except sqlite3.Error:
        raise HTTPException(
            status_code=503,
            detail="The database is temporarily unavailable."
        ) from None
    return {"status": "ok", "database": "ok"}


@app.post("/auth/register", response_model=AuthResponse, status_code=201)
def register(request: Credentials):
    username = _validate_username_and_password(
        request.username,
        request.password
    )
    try:
        if get_user_by_username(username):
            raise HTTPException(
                status_code=409,
                detail="Username is unavailable."
            )
        user = {
            "user_id": str(uuid.uuid4()),
            "username": username
        }
        create_user(
            user["user_id"],
            username,
            auth.hash_password(request.password)
        )
        return _issue_auth_response(user)
    except HTTPException:
        raise
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="Username is unavailable."
        ) from None
    except sqlite3.Error:
        raise HTTPException(
            status_code=503,
            detail="The database is temporarily unavailable."
        ) from None


@app.post("/auth/login", response_model=AuthResponse)
def login(request: Credentials):
    username = request.username.strip().lower()
    try:
        user = get_user_by_username(username)
        if (
            user is None
            or not auth.verify_password(
                request.password,
                user["password_hash"]
            )
        ):
            raise HTTPException(
                status_code=401,
                detail="Invalid username or password.",
                headers={"WWW-Authenticate": "Bearer"}
            )
        return _issue_auth_response(user)
    except HTTPException:
        raise
    except sqlite3.Error:
        raise HTTPException(
            status_code=503,
            detail="The database is temporarily unavailable."
        ) from None


@app.get("/auth/me", response_model=UserInfo)
def current_user(current_user: dict = Depends(get_current_user)):
    return {
        "user_id": current_user["user_id"],
        "username": current_user["username"]
    }


@app.post("/auth/logout", status_code=204)
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    current_user: dict = Depends(get_current_user)
):
    del current_user
    try:
        auth.revoke_session(credentials.credentials)
    except sqlite3.Error:
        raise HTTPException(
            status_code=503,
            detail="The database is temporarily unavailable."
        ) from None


@app.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    current_user: dict = Depends(get_current_user)
):

    # Validate message
    if not request.message.strip():
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty."
        )

    try:
        conversation_id = request.conversation_id
        if conversation_id and not get_conversation(
            conversation_id,
            current_user["user_id"]
        ):
            raise HTTPException(
                status_code=404,
                detail="Conversation not found."
            )

        if conversation_id:
            history = get_conversation_history(
                conversation_id,
                current_user["user_id"]
            )
            context = get_managed_context(
                conversation_id,
                history,
                max_recent_messages=MAX_RECENT_MESSAGES
            )
        else:
            context = {"recent_messages": [], "summary": ""}

        answer = generate_response(
            prompt=request.message,
            history=context["recent_messages"],
            summary=context["summary"]
        )

        if conversation_id:
            save_exchange(
                conversation_id,
                current_user["user_id"],
                request.message,
                answer
            )
        else:
            conversation_id = create_conversation_with_exchange(
                current_user["user_id"],
                request.message,
                answer
            )

        return ChatResponse(
            conversation_id=conversation_id,
            response=answer
        )

    except HTTPException:
        raise
    except RuntimeError:
        raise HTTPException(
            status_code=502,
            detail="Nova AI is temporarily unable to respond."
        ) from None
    except ConversationNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found."
        ) from None
    except sqlite3.Error:
        raise HTTPException(
            status_code=503,
            detail="The database is temporarily unavailable."
        ) from None


@app.get("/conversations", response_model=list[ConversationSummary])
def conversations(current_user: dict = Depends(get_current_user)):
    try:
        return list_conversations(current_user["user_id"])
    except sqlite3.Error:
        raise HTTPException(
            status_code=503,
            detail="The database is temporarily unavailable."
        ) from None


@app.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail
)
def conversation_detail(
    conversation_id: str,
    current_user: dict = Depends(get_current_user)
):
    try:
        conversation = get_conversation(
            conversation_id,
            current_user["user_id"]
        )
        if not conversation:
            raise HTTPException(
                status_code=404,
                detail="Conversation not found."
            )
        return {
            **conversation,
            "messages": get_conversation_history(
                conversation_id,
                current_user["user_id"]
            )
        }
    except HTTPException:
        raise
    except sqlite3.Error:
        raise HTTPException(
            status_code=503,
            detail="The database is temporarily unavailable."
        ) from None
