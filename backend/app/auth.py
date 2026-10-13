"""Password accounts, revocable opaque sessions and administrator operations."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import database as db

router = APIRouter(prefix="/api")
COOKIE = "plasma_session"
PASSWORD_ITERATIONS = 600_000


def get_session():
    with db.session() as session:
        yield session


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PASSWORD_ITERATIONS).hex()
    return f"pbkdf2-sha256${PASSWORD_ITERATIONS}${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, rounds, salt, digest = encoded.split("$")
        if scheme != "pbkdf2-sha256" or not 100_000 <= int(rounds) <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, digest)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(token: str) -> str:
    return hashlib.sha256(("csrf:" + token).encode()).hexdigest()


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def auth_lock(session: Session) -> None:
    if session.bind.dialect.name == "postgresql":
        # Serialize account administration and login throttle updates across API
        # processes, including simultaneous attempts against a new throttle key.
        session.execute(text("SELECT pg_advisory_xact_lock(1732050808)"))
    elif session.bind.dialect.name == "sqlite":
        connection = session.connection().connection.driver_connection
        if not connection.in_transaction:
            session.execute(text("BEGIN IMMEDIATE"))


def account_response(account: db.Account) -> dict:
    return {"id": account.id, "username": account.username, "display_name": account.display_name,
            "is_admin": account.is_admin, "is_active": account.is_active,
            "must_change_password": account.must_change_password,
            "created_at": account.created_at.isoformat(),
            "deleted_at": account.deleted_at.isoformat() if account.deleted_at else None}


def require_user(request: Request, session: Session = Depends(get_session)) -> db.Account:
    token = request.cookies.get(COOKIE, "")
    record = session.get(db.LoginSession, token_hash(token)) if 20 <= len(token) <= 200 else None
    if record is None or aware(record.expires_at) <= db.utcnow():
        raise HTTPException(401, "ログインしてください")
    account = session.get(db.Account, record.account_id)
    if account is None or not account.is_active or account.deleted_at is not None:
        raise HTTPException(401, "ログインしてください")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(supplied, csrf_token(token)):
            raise HTTPException(403, "操作を確認できません。画面を再読み込みしてください")
    if account.must_change_password and request.url.path not in {"/api/auth/me", "/api/auth/password", "/api/auth/logout"}:
        raise HTTPException(403, {"code": "password_change_required", "message": "仮パスワードを変更してください"})
    return account


def require_admin(user: db.Account = Depends(require_user)) -> db.Account:
    if not user.is_admin:
        raise HTTPException(403, "管理者のみ操作できます")
    return user


def check_login_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    trusted = {item.strip().rstrip("/") for item in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if item.strip()}
    trusted.add(str(request.base_url).rstrip("/"))
    if (origin and origin.rstrip("/") not in trusted) or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "このアクセス元からログインできません")


def issue_session(session: Session, account: db.Account, response: Response) -> str:
    token = secrets.token_urlsafe(32)
    seconds = max(900, min(int(os.getenv("SESSION_TTL_SECONDS", "28800")), 86400))
    session.execute(delete(db.LoginSession).where(db.LoginSession.expires_at <= db.utcnow()))
    session.add(db.LoginSession(token_hash=token_hash(token), account_id=account.id,
                                expires_at=db.utcnow() + timedelta(seconds=seconds)))
    response.set_cookie(COOKIE, token, max_age=seconds, httponly=True,
                        secure=os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true",
                        samesite="strict", path="/api")
    response.headers["Cache-Control"] = "no-store"
    return token


def invalidate_sessions(session: Session, account_id: str) -> None:
    session.execute(delete(db.LoginSession).where(db.LoginSession.account_id == account_id))


def audit(session: Session, actor: db.Account, target: db.Account, action: str) -> None:
    session.add(db.AccountEvent(actor_id=actor.id, target_id=target.id, action=action))


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)


class ChangePassword(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class CreateAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=100)
    is_admin: bool = Field(default=False, strict=True)

    @field_validator("username")
    @classmethod
    def username_value(cls, value):
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,79}", value):
            raise ValueError("ログインIDには英数字と _ . @ - を使用してください")
        return value

    @field_validator("display_name")
    @classmethod
    def display_value(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("表示名を入力してください")
        return value


class UpdateAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=100)
    is_admin: bool = Field(strict=True)
    is_active: bool = Field(strict=True)

    @field_validator("display_name")
    @classmethod
    def display_value(cls, value):
        return CreateAccount.display_value(value)


@router.get("/auth/status")
def auth_status(response: Response, session: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    initialized = session.scalar(select(db.Account.id).where(db.Account.is_admin.is_(True),
                  db.Account.is_active.is_(True), db.Account.deleted_at.is_(None)).limit(1)) is not None
    return {"initialized": initialized}


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, response: Response, session: Session = Depends(get_session)):
    check_login_origin(request)
    auth_lock(session)
    username = payload.username.strip()
    now = db.utcnow()
    session.execute(delete(db.LoginThrottle).where(db.LoginThrottle.window_started < now - timedelta(days=1)))
    # Account bucket + a larger address bucket; no proxy header is trusted as IP.
    limits = [("user:" + username, 8), ("address:" + (request.client.host if request.client else "unknown"), 100)]
    buckets = []
    for key, limit in limits:
        bucket = session.get(db.LoginThrottle, token_hash(key))
        if bucket is None:
            bucket = db.LoginThrottle(key=token_hash(key), attempts=0, window_started=now)
            session.add(bucket)
        if now - aware(bucket.window_started) >= timedelta(minutes=15):
            bucket.attempts, bucket.window_started = 0, now
        if bucket.attempts >= limit:
            remaining = max(1, int((aware(bucket.window_started) + timedelta(minutes=15) - now).total_seconds()))
            raise HTTPException(429, "ログイン試行が多すぎます。しばらく待ってからお試しください", headers={"Retry-After": str(remaining)})
        buckets.append(bucket)
    account = session.scalar(select(db.Account).where(db.Account.username == username))
    valid = verify_password(payload.password, account.password_hash if account else _DUMMY_HASH)
    if not valid or account is None or not account.is_active or account.deleted_at is not None:
        for bucket in buckets:
            bucket.attempts += 1
        session.commit()
        raise HTTPException(401, "ログインIDまたはパスワードが正しくありません")
    buckets[0].attempts = 0
    buckets[1].attempts += 1
    old_token = request.cookies.get(COOKIE)
    if old_token:
        session.execute(delete(db.LoginSession).where(db.LoginSession.token_hash == token_hash(old_token)))
    token = issue_session(session, account, response)
    session.commit()
    return {"account": account_response(account), "csrf_token": csrf_token(token)}


@router.get("/auth/me")
def me(request: Request, response: Response, user: db.Account = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    return {"account": account_response(user), "csrf_token": csrf_token(request.cookies[COOKIE])}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, user: db.Account = Depends(require_user), session: Session = Depends(get_session)):
    session.execute(delete(db.LoginSession).where(db.LoginSession.token_hash == token_hash(request.cookies[COOKIE])))
    session.commit()
    response.delete_cookie(COOKIE, path="/api", httponly=True, samesite="strict",
                           secure=os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true")


@router.post("/auth/password")
def change_password(payload: ChangePassword, response: Response, user: db.Account = Depends(require_user), session: Session = Depends(get_session)):
    auth_lock(session)
    session.refresh(user)
    if not user.is_active or user.deleted_at is not None:
        raise HTTPException(401, "ログインしてください")
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(400, "現在のパスワードが正しくありません")
    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(422, "現在と異なるパスワードを指定してください")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    invalidate_sessions(session, user.id)
    token = issue_session(session, user, response)
    audit(session, user, user, "password_changed")
    session.commit()
    return {"account": account_response(user), "csrf_token": csrf_token(token)}


@router.get("/admin/accounts")
def list_accounts(q: str = Query("", max_length=100), limit: int = Query(25, ge=1, le=100),
                  offset: int = Query(0, ge=0), include_deleted: bool = False, active_only: bool = False,
                  user: db.Account = Depends(require_admin), session: Session = Depends(get_session)):
    filters = [] if include_deleted else [db.Account.deleted_at.is_(None)]
    if active_only:
        filters.extend([db.Account.is_active.is_(True), db.Account.deleted_at.is_(None)])
    if q.strip():
        filters.append(or_(db.Account.username.icontains(q.strip(), autoescape=True), db.Account.display_name.icontains(q.strip(), autoescape=True)))
    total = session.scalar(select(func.count()).select_from(db.Account).where(*filters))
    rows = session.scalars(select(db.Account).where(*filters).order_by(db.Account.username).offset(offset).limit(limit)).all()
    return {"accounts": [account_response(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def admin_mutation_guard(session: Session, actor: db.Account) -> None:
    auth_lock(session)
    session.refresh(actor)
    if not actor.is_active or actor.deleted_at is not None or not actor.is_admin:
        raise HTTPException(403, "管理者のみ操作できます")


def managed_account(session: Session, account_id: str) -> db.Account:
    target = session.get(db.Account, account_id)
    if target is None or target.deleted_at is not None:
        raise HTTPException(404, "アカウントが見つかりません")
    return target


@router.post("/admin/accounts", status_code=201)
def create_account(payload: CreateAccount, user: db.Account = Depends(require_admin), session: Session = Depends(get_session)):
    admin_mutation_guard(session, user)
    temporary = secrets.token_urlsafe(18)
    target = db.Account(username=payload.username, display_name=payload.display_name,
                        password_hash=hash_password(temporary), is_admin=payload.is_admin)
    session.add(target)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "このログインIDは登録済みです。削除済みのIDも再使用できません") from exc
    audit(session, user, target, "created")
    session.commit()
    return {"account": account_response(target), "temporary_password": temporary}


@router.put("/admin/accounts/{account_id}")
def update_account(account_id: str, payload: UpdateAccount, user: db.Account = Depends(require_admin), session: Session = Depends(get_session)):
    admin_mutation_guard(session, user)
    target = managed_account(session, account_id)
    if target.id == user.id and (not payload.is_admin or not payload.is_active):
        raise HTTPException(409, "自分自身の管理者権限や有効状態は解除できません")
    changed_access = target.is_admin != payload.is_admin or target.is_active != payload.is_active
    target.display_name, target.is_admin, target.is_active = payload.display_name, payload.is_admin, payload.is_active
    if changed_access:
        invalidate_sessions(session, target.id)
    audit(session, user, target, "updated")
    session.commit()
    return account_response(target)


@router.post("/admin/accounts/{account_id}/password")
def reset_password(account_id: str, user: db.Account = Depends(require_admin), session: Session = Depends(get_session)):
    admin_mutation_guard(session, user)
    target = managed_account(session, account_id)
    if target.id == user.id:
        raise HTTPException(409, "自分のパスワードはパスワード変更画面で変更してください")
    temporary = secrets.token_urlsafe(18)
    target.password_hash, target.must_change_password = hash_password(temporary), True
    invalidate_sessions(session, target.id)
    audit(session, user, target, "password_reset")
    session.commit()
    return {"account": account_response(target), "temporary_password": temporary}


@router.delete("/admin/accounts/{account_id}", status_code=204)
def delete_account(account_id: str, user: db.Account = Depends(require_admin), session: Session = Depends(get_session)):
    admin_mutation_guard(session, user)
    target = managed_account(session, account_id)
    if target.id == user.id:
        raise HTTPException(409, "自分自身のアカウントは削除できません")
    target.deleted_at, target.is_active = db.utcnow(), False
    invalidate_sessions(session, target.id)
    audit(session, user, target, "deleted")
    session.commit()
