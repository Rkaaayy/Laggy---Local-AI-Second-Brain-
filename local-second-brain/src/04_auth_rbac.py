import time
import jwt
import pyotp

# Strictly local cryptographic secret (in production, load from a local .env file)
JWT_SECRET = "laggy-local-zero-trust-secret-key-2026"
JWT_ALGORITHM = "HS256"

# Base32 TOTP secret (compatible with Google Authenticator / Authy / Bitwarden)
TOTP_SECRET = "JBSWY3DPEHPK3PXP"

# Role-Based Access Control (RBAC) Policy Matrix
RBAC_POLICIES = {
    "standard": {
        "allowed_clearances": ["standard"],
        "allowed_tools": ["search_knowledge_base", "manage_todo_tasks", "search_the_web"],
    },
    "vault_admin": {
        "allowed_clearances": ["standard", "vault"],
        "allowed_tools": [
            "search_knowledge_base",
            "manage_todo_tasks",
            "search_the_web",
            "save_markdown_note",
            "read_vault_record",
        ],
    },
}


def get_current_totp_code() -> str:
    """Helper to generate the current 6-digit 2FA code locally for testing."""
    totp = pyotp.TOTP(TOTP_SECRET)
    return totp.now()


def create_session_token(username: str, totp_code: str = None, ttl_seconds: int = 600) -> str:
    """
    Issues a signed JWT token.
    - Default login gets 'standard' role.
    - Providing a valid 6-digit TOTP code elevates the token to 'vault_admin' for 10 minutes.
    """
    role = "standard"
    if totp_code:
        totp = pyotp.TOTP(TOTP_SECRET)
        if totp.verify(totp_code, valid_window=1):
            role = "vault_admin"
        else:
            raise PermissionError("Invalid 6-digit TOTP code. Vault elevation denied.")

    payload = {
        "sub": username,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl_seconds,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token_and_permissions(token: str, required_tool: str = None) -> dict:
    """
    Decodes the JWT token, verifies it hasn't expired, and checks if the user's
    role is allowed to execute the requested MCP tool.
    """
    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise PermissionError("Session token expired. Please re-authenticate.")
    except jwt.InvalidTokenError:
        raise PermissionError("Invalid authentication token.")

    role = decoded.get("role", "standard")
    policy = RBAC_POLICIES.get(role, RBAC_POLICIES["standard"])

    if required_tool and required_tool not in policy["allowed_tools"]:
        raise PermissionError(
            f"RBAC BLOCKED: Role '{role}' is not authorized to call tool '{required_tool}'. "
            f"Requires 'vault_admin' elevation."
        )

    return {
        "username": decoded["sub"],
        "role": role,
        "allowed_clearances": policy["allowed_clearances"],
        "allowed_tools": policy["allowed_tools"],
    }