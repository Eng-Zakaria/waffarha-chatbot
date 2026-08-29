"""
Session management for tracking offers shown during a conversation.

Provides centralized storage for offers retrieved during a user session,
allowing the compare feature to scope to previously shown offers instead
of retrieving fresh results.
"""
import redis
import json
import uuid
from typing import List, Dict, Optional
import config

class SessionManager:
    def __init__(self, redis_url: str = None):
        """Initialize session manager with Redis connection.

        Args:
            redis_url: Redis connection URL. Defaults to config.REDIS_URL.
        """
        self.redis = redis.Redis.from_url(redis_url or config.REDIS_URL)

    def create_session(self) -> str:
        """Create a new session and return its ID."""
        session_id = str(uuid.uuid4())
        self.redis.setex(f"session:{session_id}", 3600, json.dumps({"offers": []}))
        return session_id

    def add_offers_to_session(self, session_id: str, offers: List[Dict]) -> None:
        """Store offers in the session.

        Args:
            session_id: Session identifier
            offers: List of offer dictionaries to store
        """
        if not offers:
            return

        session_data = self.get_session(session_id)
        # Deduplicate offers by offer_id
        existing_ids = {offer.get("offer_id") for offer in session_data.get("offers", [])}
        for offer in offers:
            if offer.get("offer_id") not in existing_ids:
                session_data["offers"].append(offer)

        self.redis.setex(f"session:{session_id}", 3600, json.dumps(session_data))

    def get_session_offers(self, session_id: str) -> List[Dict]:
        """Retrieve all offers stored in the session.

        Args:
            session_id: Session identifier

        Returns:
            List of offer dictionaries
        """
        session_data = self.get_session(session_id)
        return session_data.get("offers", [])

    def get_session(self, session_id: str) -> Dict:
        """Retrieve full session data.

        Args:
            session_id: Session identifier

        Returns:
            Session data dictionary
        """
        data = self.redis.get(f"session:{session_id}")
        return json.loads(data) if data else {"offers": []}

    def clear_session(self, session_id: str) -> None:
        """Clear session data.

        Args:
            session_id: Session identifier
        """
        self.redis.delete(f"session:{session_id}")

    def get_offers_for_merchants(self, session_id: str, merchants: List[str]) -> Dict[str, List[Dict]]:
        """Get offers from session for specific merchants.

        Args:
            session_id: Session identifier
            merchants: List of merchant names to filter by

        Returns:
            Dictionary mapping merchant names to their offers
        """
        session_offers = self.get_session_offers(session_id)
        merchant_offers = {}

        for merchant in merchants:
            merchant_offers[merchant] = []
            for offer in session_offers:
                # Check both English and Arabic merchant names
                offer_merchant_en = offer.get("part_name_en", "").lower()
                offer_merchant_ar = offer.get("part_name_ar", "").lower()
                merchant_lower = merchant.lower()

                if (offer_merchant_en == merchant_lower or
                    offer_merchant_ar == merchant_lower or
                    merchant_lower in offer_merchant_en or
                    merchant_lower in offer_merchant_ar):
                    merchant_offers[merchant].append(offer)

        return merchant_offers