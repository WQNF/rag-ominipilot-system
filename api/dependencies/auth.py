# api/dependencies/auth.py
import os
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import psycopg2
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 10080

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")


DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "user": os.getenv("POSTGRES_USER", "omnipilot"),
    "password": os.getenv("POSTGRES_PASSWORD", "omnipilot123"),
    "database": os.getenv("POSTGRES_DB", "omnipilot"),
}


CREATE_USERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS app_users (
    username VARCHAR(64) PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL DEFAULT '',
    email VARCHAR(120) NOT NULL DEFAULT '',
    department VARCHAR(80) NOT NULL DEFAULT 'general',
    role VARCHAR(20) NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    hashed_password TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Token(BaseModel):
    access_token: str
    token_type: str
    username: str
    role: str
    department: str
    full_name: str = ""


class TokenData(BaseModel):
    username: Optional[str] = None
    role: Optional[str] = None
    department: Optional[str] = None


class User(BaseModel):
    username: str
    full_name: str = ""
    email: str = ""
    department: str = "general"
    role: str = "user"
    is_active: bool = True


class PublicUser(User):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class RegisterRequest(BaseModel):
    username: str
    password: str
    confirm_password: str
    full_name: str = ""
    email: str = ""
    department: str = "general"


class AdminUserCreate(BaseModel):
    username: str
    password: str
    confirm_password: str
    full_name: str = ""
    email: str = ""
    department: str = "general"
    role: str = "user"
    is_active: bool = True


class AdminUserUpdate(BaseModel):
    password: Optional[str] = None
    confirm_password: Optional[str] = None
    full_name: Optional[str] = None
    email: Optional[str] = None
    department: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


def _get_connection():
    return psycopg2.connect(**DB_CONFIG)


def _normalize_username(username: str) -> str:
    return username.strip()


def _validate_role(role: str) -> str:
    role = (role or "user").strip().lower()
    if role not in {"user", "admin"}:
        raise HTTPException(status_code=400, detail="角色只能是 user 或 admin")
    return role


def _validate_password_pair(password: str, confirm_password: str) -> None:
    if password != confirm_password:
        raise HTTPException(status_code=400, detail="两次输入的密码不一致")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密码长度不能少于 6 位")
    if password.isdigit():
        raise HTTPException(status_code=400, detail="密码不能是纯数字")


def ensure_user_store() -> None:
    """Create the auth table and seed the default accounts if they are missing."""
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_USERS_TABLE_SQL)
            seed_users = [
                {
                    "username": "admin",
                    "password": "admin123",
                    "full_name": "系统管理员",
                    "email": "admin@omnipilot.local",
                    "department": "系统管理",
                    "role": "admin",
                },
                {
                    "username": "zhangwei",
                    "password": "secret123",
                    "full_name": "张伟",
                    "email": "zhangwei@starring.com",
                    "department": "销售部",
                    "role": "user",
                },
            ]
            for item in seed_users:
                cur.execute(
                    """
                    INSERT INTO app_users (
                        username, full_name, email, department, role, hashed_password, is_active
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                    ON CONFLICT (username) DO NOTHING
                    """,
                    (
                        item["username"],
                        item["full_name"],
                        item["email"],
                        item["department"],
                        item["role"],
                        hash_password(item["password"]),
                    ),
                )


def _row_to_user(row: dict) -> User:
    return User(
        username=row["username"],
        full_name=row.get("full_name") or "",
        email=row.get("email") or "",
        department=row.get("department") or "general",
        role=row.get("role") or "user",
        is_active=bool(row.get("is_active", True)),
    )


def _row_to_public_user(row: dict) -> PublicUser:
    user = _row_to_user(row)
    return PublicUser(
        **user.dict(),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def get_user_record(username: str) -> Optional[dict]:
    ensure_user_store()
    username = _normalize_username(username)
    with _get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM app_users WHERE username = %s", (username,))
            return cur.fetchone()


def get_user(username: str) -> Optional[User]:
    row = get_user_record(username)
    if not row:
        return None
    return _row_to_user(row)


def authenticate_user(username: str, password: str) -> Optional[User]:
    row = get_user_record(username)
    if not row or not row.get("is_active", True):
        return None
    if not verify_password(password, row["hashed_password"]):
        return None
    return _row_to_user(row)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_user(
    *,
    username: str,
    password: str,
    confirm_password: str,
    full_name: str = "",
    email: str = "",
    department: str = "general",
    role: str = "user",
    is_active: bool = True,
) -> PublicUser:
    ensure_user_store()
    username = _normalize_username(username)
    role = _validate_role(role)
    _validate_password_pair(password, confirm_password)
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")

    with _get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT 1 FROM app_users WHERE username = %s", (username,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="用户名已存在")
            cur.execute(
                """
                INSERT INTO app_users (
                    username, full_name, email, department, role, hashed_password, is_active
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING username, full_name, email, department, role, is_active, created_at, updated_at
                """,
                (
                    username,
                    full_name.strip(),
                    email.strip(),
                    (department or "general").strip(),
                    role,
                    hash_password(password),
                    is_active,
                ),
            )
            return _row_to_public_user(cur.fetchone())


def list_users() -> list[PublicUser]:
    ensure_user_store()
    with _get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT username, full_name, email, department, role, is_active, created_at, updated_at
                FROM app_users
                ORDER BY created_at ASC, username ASC
                """
            )
            return [_row_to_public_user(row) for row in cur.fetchall()]


def update_user(username: str, data: AdminUserUpdate) -> PublicUser:
    ensure_user_store()
    username = _normalize_username(username)
    fields = []
    params = []

    if data.password or data.confirm_password:
        if not data.password or not data.confirm_password:
            raise HTTPException(status_code=400, detail="修改密码时需要填写两次密码")
        _validate_password_pair(data.password, data.confirm_password)
        fields.append("hashed_password = %s")
        params.append(hash_password(data.password))

    for field in ("full_name", "email", "department"):
        value = getattr(data, field)
        if value is not None:
            fields.append(f"{field} = %s")
            params.append(value.strip())

    if data.role is not None:
        fields.append("role = %s")
        params.append(_validate_role(data.role))

    if data.is_active is not None:
        fields.append("is_active = %s")
        params.append(data.is_active)

    if not fields:
        existing = get_user_record(username)
        if not existing:
            raise HTTPException(status_code=404, detail="用户不存在")
        return _row_to_public_user(existing)

    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(username)

    with _get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                UPDATE app_users
                SET {", ".join(fields)}
                WHERE username = %s
                RETURNING username, full_name, email, department, role, is_active, created_at, updated_at
                """,
                tuple(params),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="用户不存在")
            return _row_to_public_user(row)


def delete_user(username: str, current_username: str) -> None:
    ensure_user_store()
    username = _normalize_username(username)
    if username == current_username:
        raise HTTPException(status_code=400, detail="不能删除当前登录的管理员账户")

    try:
        from tools.user_knowledge import delete_all_user_knowledge
        delete_all_user_knowledge(username)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"清理用户个人知识库失败，已取消删除用户: {exc}")

    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM app_users WHERE username = %s", (username,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="用户不存在")


async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(
            username=username,
            role=payload.get("role"),
            department=payload.get("department"),
        )
    except JWTError:
        raise credentials_exception

    user = get_user(token_data.username)
    if user is None or not user.is_active:
        raise credentials_exception
    return user


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current_user
