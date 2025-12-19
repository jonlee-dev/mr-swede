"""Firestore client for database operations."""

from datetime import datetime
from functools import lru_cache
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1 import AsyncClient

from src.config.logging import get_logger
from src.config.settings import get_settings
from src.database.models import Account, CompetitiveStats, StatsHistory, UserPreferences

logger = get_logger(__name__)


class FirestoreClient:
    """Async Firestore client for database operations."""
    
    def __init__(self, project_id: str | None = None, collection_prefix: str = "mr_swede_"):
        """Initialize Firestore client.
        
        Args:
            project_id: GCP project ID (uses default if not provided)
            collection_prefix: Prefix for all collection names
        """
        self._project_id = project_id
        self._collection_prefix = collection_prefix
        self._client: AsyncClient | None = None
    
    @property
    def client(self) -> AsyncClient:
        """Get or create the Firestore async client."""
        if self._client is None:
            self._client = AsyncClient(project=self._project_id)
        return self._client
    
    def _collection(self, name: str) -> str:
        """Get prefixed collection name."""
        return f"{self._collection_prefix}{name}"
    
    # ==================== Account Operations ====================
    
    async def get_account(self, account_id: str) -> Account | None:
        """Get an account by ID.
        
        Args:
            account_id: Firestore document ID
            
        Returns:
            Account if found, None otherwise
        """
        doc_ref = self.client.collection(self._collection("accounts")).document(account_id)
        doc = await doc_ref.get()
        
        if doc.exists:
            return Account.from_firestore(doc.id, doc.to_dict() or {})
        return None
    
    async def get_account_by_battle_tag(self, battle_tag: str) -> Account | None:
        """Get an account by BattleTag.
        
        Args:
            battle_tag: Blizzard BattleTag (e.g., "Player#1234")
            
        Returns:
            Account if found, None otherwise
        """
        query = (
            self.client.collection(self._collection("accounts"))
            .where("battle_tag", "==", battle_tag)
            .limit(1)
        )
        
        docs = query.stream()
        async for doc in docs:
            return Account.from_firestore(doc.id, doc.to_dict() or {})
        return None
    
    async def get_accounts_by_discord_user(self, discord_user_id: str) -> list[Account]:
        """Get all accounts for a Discord user.
        
        Args:
            discord_user_id: Discord user ID
            
        Returns:
            List of accounts
        """
        query = (
            self.client.collection(self._collection("accounts"))
            .where("discord_user_id", "==", discord_user_id)
        )
        
        accounts = []
        async for doc in query.stream():
            accounts.append(Account.from_firestore(doc.id, doc.to_dict() or {}))
        return accounts
    
    async def create_account(self, account: Account) -> str:
        """Create a new account.
        
        Args:
            account: Account to create
            
        Returns:
            Document ID of created account
        """
        doc_ref = self.client.collection(self._collection("accounts")).document()
        await doc_ref.set(account.to_firestore())
        logger.info("Created account", battle_tag=account.battle_tag, doc_id=doc_ref.id)
        return doc_ref.id
    
    async def update_account(self, account_id: str, data: dict[str, Any]) -> None:
        """Update an account.
        
        Args:
            account_id: Document ID
            data: Fields to update
        """
        doc_ref = self.client.collection(self._collection("accounts")).document(account_id)
        data["last_updated"] = datetime.utcnow()
        await doc_ref.update(data)
        logger.info("Updated account", doc_id=account_id)
    
    async def update_account_stats(
        self, account_id: str, stats: CompetitiveStats
    ) -> None:
        """Update an account's competitive stats.
        
        Args:
            account_id: Document ID
            stats: New competitive stats
        """
        await self.update_account(account_id, {
            "current_stats": stats.model_dump(),
            "last_updated": datetime.utcnow(),
        })
    
    async def delete_account(self, account_id: str) -> None:
        """Delete an account.
        
        Args:
            account_id: Document ID
        """
        doc_ref = self.client.collection(self._collection("accounts")).document(account_id)
        await doc_ref.delete()
        logger.info("Deleted account", doc_id=account_id)
    
    # ==================== Stats History Operations ====================
    
    async def add_stats_history(self, history: StatsHistory) -> str:
        """Add a stats history record.
        
        Args:
            history: Stats history to add
            
        Returns:
            Document ID
        """
        doc_ref = self.client.collection(self._collection("stats_history")).document()
        await doc_ref.set(history.to_firestore())
        return doc_ref.id
    
    async def get_stats_history(
        self, 
        account_id: str, 
        limit: int = 30,
        start_date: datetime | None = None,
    ) -> list[StatsHistory]:
        """Get stats history for an account.
        
        Args:
            account_id: Account document ID
            limit: Maximum records to return
            start_date: Only return records after this date
            
        Returns:
            List of stats history records
        """
        query = (
            self.client.collection(self._collection("stats_history"))
            .where("account_id", "==", account_id)
            .order_by("recorded_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        
        if start_date:
            query = query.where("recorded_at", ">=", start_date)
        
        history = []
        async for doc in query.stream():
            history.append(StatsHistory.from_firestore(doc.id, doc.to_dict() or {}))
        return history
    
    # ==================== User Preferences Operations ====================
    
    async def get_user_preferences(self, discord_user_id: str) -> UserPreferences | None:
        """Get user preferences.
        
        Args:
            discord_user_id: Discord user ID
            
        Returns:
            UserPreferences if found, None otherwise
        """
        doc_ref = self.client.collection(self._collection("user_preferences")).document(
            discord_user_id
        )
        doc = await doc_ref.get()
        
        if doc.exists:
            return UserPreferences.from_firestore(doc.id, doc.to_dict() or {})
        return None
    
    async def upsert_user_preferences(self, preferences: UserPreferences) -> None:
        """Create or update user preferences.
        
        Args:
            preferences: User preferences to save
        """
        doc_ref = self.client.collection(self._collection("user_preferences")).document(
            preferences.discord_user_id
        )
        await doc_ref.set(preferences.to_firestore(), merge=True)
    
    # ==================== Bulk Operations ====================
    
    async def get_all_accounts(self) -> list[Account]:
        """Get all accounts (for batch updates).
        
        Returns:
            List of all accounts
        """
        accounts = []
        async for doc in self.client.collection(self._collection("accounts")).stream():
            accounts.append(Account.from_firestore(doc.id, doc.to_dict() or {}))
        return accounts
    
    async def batch_update_stats(
        self, updates: list[tuple[str, CompetitiveStats]]
    ) -> None:
        """Batch update stats for multiple accounts.
        
        Args:
            updates: List of (account_id, stats) tuples
        """
        batch = self.client.batch()
        collection = self.client.collection(self._collection("accounts"))
        
        for account_id, stats in updates:
            doc_ref = collection.document(account_id)
            batch.update(doc_ref, {
                "current_stats": stats.model_dump(),
                "last_updated": datetime.utcnow(),
            })
        
        await batch.commit()
        logger.info("Batch updated stats", count=len(updates))


@lru_cache
def get_firestore_client() -> FirestoreClient:
    """Get cached Firestore client instance."""
    settings = get_settings()
    return FirestoreClient(
        project_id=settings.gcp_project_id or None,
        collection_prefix=settings.firestore_collection_prefix,
    )

