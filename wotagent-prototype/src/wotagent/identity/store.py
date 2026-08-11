"""SQLite-backed persistent Principal directory and demo credential sessions."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from .directory import PrincipalDirectory, PrincipalNotAssigned
from .models import (
    DomainRoleBinding,
    PrincipalProfile,
    PrincipalRelationship,
    TaskPrincipal,
)
from .templates import (
    require_registration_target,
    require_role_template,
)


class PrincipalAuthenticationError(PermissionError):
    pass


class PrincipalRegistrationError(ValueError):
    pass


class PrincipalStore(PrincipalDirectory):
    def __init__(
        self,
        path: str | Path,
        *,
        seed_profiles: list[PrincipalProfile] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        if seed_profiles:
            self.seed(seed_profiles)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS principals (
                    principal_id TEXT PRIMARY KEY,
                    principal_type TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    login_enabled INTEGER NOT NULL,
                    login_mode TEXT NOT NULL,
                    login_name TEXT,
                    pin_salt TEXT,
                    pin_hash TEXT,
                    password_salt TEXT,
                    password_hash TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS principal_bindings (
                    principal_id TEXT NOT NULL,
                    domain_id TEXT NOT NULL,
                    binding_json TEXT NOT NULL,
                    PRIMARY KEY (principal_id, domain_id),
                    FOREIGN KEY (principal_id)
                        REFERENCES principals(principal_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS principal_sessions (
                    token_hash TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (principal_id)
                        REFERENCES principals(principal_id)
                        ON DELETE CASCADE
                );
                """
            )
            self._ensure_column(
                connection,
                "principals",
                "login_name",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "principals",
                "password_salt",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "principals",
                "password_hash",
                "TEXT",
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_principals_login_name
                ON principals(login_name)
                WHERE login_name IS NOT NULL
                """
            )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        columns = {
            row[1]
            for row in connection.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        }
        if column not in columns:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
            )

    def seed(self, profiles: list[PrincipalProfile]) -> None:
        now = _utc_now()
        with self._connect() as connection:
            for profile in profiles:
                pin_salt = None
                pin_hash = None
                password_salt = None
                password_hash = None
                if profile.login_mode == "pin" and profile.bootstrap_secret:
                    salt = secrets.token_bytes(16)
                    pin_salt = salt.hex()
                    pin_hash = _pin_hash(profile.bootstrap_secret, salt)
                if profile.login_mode == "password" and profile.bootstrap_secret:
                    salt = secrets.token_bytes(16)
                    password_salt = salt.hex()
                    password_hash = _password_hash(
                        profile.bootstrap_secret,
                        salt,
                    )
                connection.execute(
                    """
                    INSERT INTO principals (
                        principal_id, principal_type, display_name,
                        login_enabled, login_mode, login_name,
                        pin_salt, pin_hash, password_salt, password_hash,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(principal_id) DO UPDATE SET
                        principal_type=excluded.principal_type,
                        display_name=excluded.display_name,
                        login_enabled=excluded.login_enabled,
                        login_mode=excluded.login_mode,
                        login_name=COALESCE(excluded.login_name, principals.login_name),
                        pin_salt=COALESCE(excluded.pin_salt, principals.pin_salt),
                        pin_hash=COALESCE(excluded.pin_hash, principals.pin_hash),
                        password_salt=COALESCE(excluded.password_salt, principals.password_salt),
                        password_hash=COALESCE(excluded.password_hash, principals.password_hash)
                    """,
                    (
                        profile.principal_id,
                        profile.principal_type,
                        profile.display_name,
                        int(profile.login_enabled),
                        profile.login_mode,
                        profile.login_name,
                        pin_salt,
                        pin_hash,
                        password_salt,
                        password_hash,
                        now,
                    ),
                )
                for binding in profile.bindings:
                    connection.execute(
                        """
                        INSERT INTO principal_bindings (
                            principal_id, domain_id, binding_json
                        ) VALUES (?, ?, ?)
                        ON CONFLICT(principal_id, domain_id) DO UPDATE SET
                            binding_json=excluded.binding_json
                        """,
                        (
                            profile.principal_id,
                            binding.domain_id,
                            binding.model_dump_json(),
                        ),
                    )

    def get_profile(self, principal_id: str) -> PrincipalProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM principals WHERE principal_id = ?",
                (principal_id,),
            ).fetchone()
            if row is None:
                return None
            bindings = connection.execute(
                """
                SELECT binding_json FROM principal_bindings
                WHERE principal_id = ? ORDER BY domain_id
                """,
                (principal_id,),
            ).fetchall()
        return PrincipalProfile(
            principal_id=row["principal_id"],
            principal_type=row["principal_type"],
            display_name=row["display_name"],
            login_enabled=bool(row["login_enabled"]),
            login_mode=row["login_mode"],
            login_name=row["login_name"],
            bindings=[
                DomainRoleBinding.model_validate_json(item["binding_json"])
                for item in bindings
            ],
        )

    def login_mode(self, principal_id: str) -> str:
        profile = self.get_profile(principal_id)
        return profile.login_mode if profile else "disabled"

    def resolve(
        self,
        principal_id: str,
        domain_id: str,
        *,
        fallback_role: str | None = None,
    ) -> TaskPrincipal:
        profile = self.get_profile(principal_id)
        if profile is None:
            if fallback_role is None:
                raise PrincipalNotAssigned(
                    f"principal '{principal_id}' is not registered"
                )
            return TaskPrincipal(
                principal_id=principal_id,
                display_name=principal_id,
                domain_id=domain_id,
                roles=[fallback_role],
            )
        binding = next(
            (item for item in profile.bindings if item.domain_id == domain_id),
            None,
        )
        if binding is None:
            raise PrincipalNotAssigned(
                f"principal '{principal_id}' is not assigned to "
                f"domain '{domain_id}'"
            )
        return TaskPrincipal(
            principal_id=profile.principal_id,
            principal_type=profile.principal_type,
            display_name=profile.display_name,
            domain_id=domain_id,
            login_name=profile.login_name,
            roles=[binding.platform_role],
            domain_roles=binding.roles,
            role_labels=binding.role_labels,
            granted_capabilities=binding.capabilities,
            resource_ids=binding.resource_ids,
            relationships=binding.relationships,
        )

    def list_contexts(
        self,
        domain_id: str,
        *,
        login_only: bool = False,
    ) -> list[TaskPrincipal]:
        query = "SELECT principal_id FROM principals"
        parameters: tuple[object, ...] = ()
        if login_only:
            query += " WHERE login_enabled = 1"
        query += " ORDER BY created_at, principal_id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        contexts: list[TaskPrincipal] = []
        for row in rows:
            try:
                contexts.append(self.resolve(row["principal_id"], domain_id))
            except PrincipalNotAssigned:
                continue
        return contexts

    def register(
        self,
        *,
        display_name: str,
        pin: str,
        domain_id: str,
        role_template_id: str,
        target_id: str,
    ) -> tuple[TaskPrincipal, str]:
        clean_name = display_name.strip()
        if len(clean_name) < 2 or len(clean_name) > 30:
            raise PrincipalRegistrationError("显示名称长度必须为 2-30 个字符")
        if not pin.isdigit() or not 4 <= len(pin) <= 12:
            raise PrincipalRegistrationError("PIN 必须为 4-12 位数字")
        try:
            template = require_role_template(role_template_id, domain_id)
            target = require_registration_target(
                domain_id,
                template.target_type,
                target_id,
            )
        except LookupError as exc:
            raise PrincipalRegistrationError(str(exc)) from exc
        principal_id = f"USR-{uuid4().hex[:8].upper()}"
        salt = secrets.token_bytes(16)
        binding = DomainRoleBinding(
            domain_id=domain_id,
            platform_role=template.platform_role,
            roles=template.roles,
            role_labels=[template.label],
            capabilities=template.capabilities,
            resource_ids=[f"{target.target_type}:{target.target_id}"],
            relationships=[PrincipalRelationship(
                relation=template.relation,
                target_type=target.target_type,
                target_id=target.target_id,
                target_label=target.label,
            )],
        )
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO principals (
                    principal_id, principal_type, display_name,
                    login_enabled, login_mode, pin_salt, pin_hash, created_at
                ) VALUES (?, 'human', ?, 1, 'pin', ?, ?, ?)
                """,
                (
                    principal_id,
                    clean_name,
                    salt.hex(),
                    _pin_hash(pin, salt),
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO principal_bindings (
                    principal_id, domain_id, binding_json
                ) VALUES (?, ?, ?)
                """,
                (
                    principal_id,
                    domain_id,
                    binding.model_dump_json(),
                ),
            )
        token = self._create_session(principal_id)
        return self.resolve(principal_id, domain_id), token

    def create_password_user(
        self,
        *,
        display_name: str,
        login_name: str,
        password: str,
        domain_id: str,
        platform_role: Literal["admin", "viewer"],
        target_id: str | None = None,
    ) -> tuple[TaskPrincipal, str]:
        clean_name = display_name.strip()
        if len(clean_name) < 2 or len(clean_name) > 30:
            raise PrincipalRegistrationError("显示名称长度必须为 2-30 个字符")
        normalized_login_name = _normalize_login_name(login_name)
        _validate_password(password)
        principal_id = f"USR-{uuid4().hex[:8].upper()}"
        binding = self._managed_binding(
            domain_id=domain_id,
            platform_role=platform_role,
            target_id=target_id,
        )
        salt = secrets.token_bytes(16)
        now = _utc_now()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO principals (
                        principal_id, principal_type, display_name,
                        login_enabled, login_mode, login_name,
                        password_salt, password_hash, created_at
                    ) VALUES (?, 'human', ?, 1, 'password', ?, ?, ?, ?)
                    """,
                    (
                        principal_id,
                        clean_name,
                        normalized_login_name,
                        salt.hex(),
                        _password_hash(password, salt),
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO principal_bindings (
                        principal_id, domain_id, binding_json
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        principal_id,
                        domain_id,
                        binding.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise PrincipalRegistrationError("账号已存在，请更换用户名") from exc
        token = self._create_session(principal_id)
        return self.resolve(principal_id, domain_id), token

    def update_password_user_role(
        self,
        *,
        principal_id: str,
        domain_id: str,
        platform_role: Literal["admin", "viewer"],
        target_id: str | None = None,
    ) -> TaskPrincipal:
        profile = self.get_profile(principal_id)
        if profile is None:
            raise PrincipalRegistrationError("账号不存在")
        if profile.login_mode != "password":
            raise PrincipalRegistrationError("仅账号密码用户支持后台角色管理")
        binding = self._managed_binding(
            domain_id=domain_id,
            platform_role=platform_role,
            target_id=target_id,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO principal_bindings (
                    principal_id, domain_id, binding_json
                ) VALUES (?, ?, ?)
                ON CONFLICT(principal_id, domain_id) DO UPDATE SET
                    binding_json=excluded.binding_json
                """,
                (
                    principal_id,
                    domain_id,
                    binding.model_dump_json(),
                ),
            )
        return self.resolve(principal_id, domain_id)

    def list_user_accounts(
        self,
        domain_id: str,
        *,
        login_only: bool = True,
    ) -> list[dict[str, object]]:
        query = """
            SELECT principal_id, display_name, login_enabled, login_mode, login_name
            FROM principals
        """
        if login_only:
            query += " WHERE login_enabled = 1"
        query += " ORDER BY created_at, principal_id"
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        results: list[dict[str, object]] = []
        for row in rows:
            try:
                context = self.resolve(row["principal_id"], domain_id)
            except PrincipalNotAssigned:
                continue
            results.append({
                **context.model_dump(mode="json"),
                "login_mode": row["login_mode"],
                "login_name": row["login_name"],
                "login_enabled": bool(row["login_enabled"]),
            })
        return results

    def login(
        self,
        *,
        principal_id: str | None = None,
        login_name: str | None = None,
        domain_id: str,
        pin: str | None = None,
        password: str | None = None,
    ) -> tuple[TaskPrincipal, str]:
        resolved_principal_id = principal_id
        if resolved_principal_id is None and login_name:
            resolved_principal_id = self._principal_id_for_login_name(login_name)
        if resolved_principal_id is None:
            raise PrincipalAuthenticationError("请输入账号或主体标识")
        profile = self.get_profile(resolved_principal_id)
        if profile is None or not profile.login_enabled:
            raise PrincipalAuthenticationError("账号不存在或不可登录")
        if profile.login_mode == "pin":
            self._verify_pin(resolved_principal_id, pin or "")
        elif profile.login_mode == "password":
            self._verify_password(resolved_principal_id, password or "")
        elif profile.login_mode != "one_click":
            raise PrincipalAuthenticationError("该主体不允许交互式登录")
        try:
            principal = self.resolve(resolved_principal_id, domain_id)
        except PrincipalNotAssigned as exc:
            raise PrincipalAuthenticationError(str(exc)) from exc
        return principal, self._create_session(resolved_principal_id)

    def resolve_session(
        self,
        token: str,
        domain_id: str,
    ) -> TaskPrincipal:
        token_hash = _token_hash(token)
        now = _utc_now()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT principal_id FROM principal_sessions
                WHERE token_hash = ? AND expires_at > ?
                """,
                (token_hash, now),
            ).fetchone()
        if row is None:
            raise PrincipalAuthenticationError("登录会话无效或已过期")
        try:
            return self.resolve(row["principal_id"], domain_id)
        except PrincipalNotAssigned as exc:
            raise PrincipalAuthenticationError(str(exc)) from exc

    def resolve_for_request(
        self,
        *,
        principal_id: str,
        domain_id: str,
        token: str | None,
        fallback_role: str | None = None,
    ) -> TaskPrincipal:
        profile = self.get_profile(principal_id)
        if profile is not None and profile.login_mode == "pin":
            if not token:
                raise PrincipalAuthenticationError("该账号需要登录会话")
            principal = self.resolve_session(token, domain_id)
            if principal.principal_id != principal_id:
                raise PrincipalAuthenticationError("登录主体与请求主体不一致")
            return principal
        if token:
            principal = self.resolve_session(token, domain_id)
            if principal.principal_id != principal_id:
                raise PrincipalAuthenticationError("登录主体与请求主体不一致")
            return principal
        return self.resolve(
            principal_id,
            domain_id,
            fallback_role=fallback_role,
        )

    def _verify_pin(self, principal_id: str, pin: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT pin_salt, pin_hash FROM principals
                WHERE principal_id = ?
                """,
                (principal_id,),
            ).fetchone()
        if (
            row is None
            or not row["pin_salt"]
            or not row["pin_hash"]
            or not hmac.compare_digest(
                _pin_hash(pin, bytes.fromhex(row["pin_salt"])),
                row["pin_hash"],
            )
        ):
            raise PrincipalAuthenticationError("PIN 不正确")

    def _verify_password(self, principal_id: str, password: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT password_salt, password_hash FROM principals
                WHERE principal_id = ?
                """,
                (principal_id,),
            ).fetchone()
        if (
            row is None
            or not row["password_salt"]
            or not row["password_hash"]
            or not hmac.compare_digest(
                _password_hash(
                    password,
                    bytes.fromhex(row["password_salt"]),
                ),
                row["password_hash"],
            )
        ):
            raise PrincipalAuthenticationError("账号或密码不正确")

    def _principal_id_for_login_name(self, login_name: str) -> str:
        normalized = _normalize_login_name(login_name)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT principal_id FROM principals
                WHERE login_name = ?
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            raise PrincipalAuthenticationError("账号不存在或不可登录")
        return str(row["principal_id"])

    def _managed_binding(
        self,
        *,
        domain_id: str,
        platform_role: Literal["admin", "viewer"],
        target_id: str | None,
    ) -> DomainRoleBinding:
        if platform_role == "admin":
            return DomainRoleBinding(
                domain_id=domain_id,
                platform_role="admin",
                roles=["system_admin"],
                role_labels=["系统管理员"],
                capabilities=["*"],
                resource_ids=["*"],
                relationships=[],
            )
        if domain_id == "eldercare":
            if not target_id:
                raise PrincipalRegistrationError("普通用户必须绑定一个居民对象")
            target = require_registration_target(
                domain_id,
                "resident",
                target_id,
            )
            return DomainRoleBinding(
                domain_id=domain_id,
                platform_role="viewer",
                roles=["resident_user"],
                role_labels=["普通用户"],
                capabilities=[
                    "domain.read",
                    "resident.context.read",
                    "health.status.read",
                    "health.summary.read",
                    "health.vitals.read",
                ],
                resource_ids=[f"resident:{target.target_id}"],
                relationships=[PrincipalRelationship(
                    relation="self",
                    target_type="resident",
                    target_id=target.target_id,
                    target_label=target.label,
                )],
            )
        if domain_id == "smart_home":
            target = require_registration_target(
                domain_id,
                "site",
                target_id or "villa-demo",
            )
            return DomainRoleBinding(
                domain_id=domain_id,
                platform_role="viewer",
                roles=["standard_user"],
                role_labels=["普通用户"],
                capabilities=[
                    "domain.read",
                    "device.read",
                ],
                resource_ids=[f"site:{target.target_id}"],
                relationships=[PrincipalRelationship(
                    relation="member_of",
                    target_type="site",
                    target_id=target.target_id,
                    target_label=target.label,
                )],
            )
        raise PrincipalRegistrationError(
            f"domain '{domain_id}' 暂不支持用户管理角色模板"
        )

    def _create_session(self, principal_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=7)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO principal_sessions (
                    token_hash, principal_id, expires_at, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    _token_hash(token),
                    principal_id,
                    expires_at.isoformat(),
                    now.isoformat(),
                ),
            )
            connection.execute(
                "DELETE FROM principal_sessions WHERE expires_at <= ?",
                (now.isoformat(),),
            )
        return token


def _pin_hash(pin: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("utf-8"),
        salt,
        120_000,
    ).hex()


def _password_hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        180_000,
    ).hex()


def _normalize_login_name(login_name: str) -> str:
    normalized = login_name.strip().lower()
    if (
        len(normalized) < 4
        or len(normalized) > 32
        or any(not (ch.isalnum() or ch in {"_", ".", "-"}) for ch in normalized)
    ):
        raise PrincipalRegistrationError(
            "账号必须为 4-32 位字母、数字或 ._- 组合"
        )
    return normalized


def _validate_password(password: str) -> None:
    if len(password) < 8 or len(password) > 64:
        raise PrincipalRegistrationError("密码长度必须为 8-64 位")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "PrincipalAuthenticationError",
    "PrincipalRegistrationError",
    "PrincipalStore",
]
