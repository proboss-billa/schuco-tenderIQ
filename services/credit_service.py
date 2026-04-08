"""Token credit tracking and enforcement."""

import logging
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models.user import User

logger = logging.getLogger("tenderiq.credits")

# Custom error code so the frontend can detect "out of credits"
CREDIT_EXHAUSTED_STATUS = 402
CREDIT_EXHAUSTED_DETAIL = "OUT_OF_CREDITS"


def check_credits(user: User) -> None:
    """Raise 402 if user has exhausted their token balance."""
    remaining = user.token_limit - user.tokens_used
    if remaining <= 0:
        raise HTTPException(
            status_code=CREDIT_EXHAUSTED_STATUS,
            detail=CREDIT_EXHAUSTED_DETAIL,
        )


def deduct_tokens(db: Session, user_id: uuid.UUID, tokens: int) -> None:
    """Add `tokens` to the user's usage counter."""
    if tokens <= 0:
        return
    db.query(User).filter(User.user_id == user_id).update(
        {User.tokens_used: User.tokens_used + tokens}
    )
    db.commit()
    logger.info("Deducted %d tokens for user %s", tokens, user_id)


def get_remaining(user: User) -> int:
    """Return how many tokens the user has left."""
    return max(0, user.token_limit - user.tokens_used)
