"""Database module for Firestore integration."""

from src.database.firestore import FirestoreClient, get_firestore_client
from src.database.models import (
    Account,
    CompetitiveStats,
    PlayerStats,
    RankInfo,
    StatsHistory,
    UserPreferences,
)

__all__ = [
    "FirestoreClient",
    "get_firestore_client",
    "Account",
    "CompetitiveStats",
    "PlayerStats",
    "RankInfo",
    "StatsHistory",
    "UserPreferences",
]
