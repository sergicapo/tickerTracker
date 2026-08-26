"""
HTTP Basic Auth dependency for FastAPI.
Credentials are loaded from environment variables API_USER and API_PASSWORD.
Uses secrets.compare_digest to prevent timing attacks.
"""
import logging
import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

logger = logging.getLogger(__name__)

security = HTTPBasic()


def verify_credentials(
    credentials: HTTPBasicCredentials = Depends(security),
) -> str:
    """
    FastAPI dependency that validates HTTP Basic Auth credentials.

    Returns the authenticated username on success.
    Raises HTTP 401 on failure.
    """
    correct_user = os.getenv("API_USER", "admin")
    correct_pass = os.getenv("API_PASSWORD", "changeme")

    # Use secrets.compare_digest to guard against timing attacks
    user_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        correct_user.encode("utf-8"),
    )
    pass_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        correct_pass.encode("utf-8"),
    )

    if not (user_ok and pass_ok):
        logger.warning(
            "Failed authentication attempt for username=%r", credentials.username
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials.username
