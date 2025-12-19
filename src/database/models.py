"""Data models for Firestore documents."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class OverwatchRole(str, Enum):
    """Overwatch competitive roles."""
    TANK = "tank"
    DAMAGE = "damage"
    SUPPORT = "support"


class RankInfo(BaseModel):
    """Overwatch rank information for a role."""
    division: str = ""  # e.g., "Diamond", "Master", "Grandmaster"
    tier: int = 0  # 1-5, where 1 is highest within division
    skill_rating: int | None = None  # SR if available
    
    @property
    def display(self) -> str:
        """Get display string for rank."""
        if not self.division:
            return "Unranked"
        return f"{self.division} {self.tier}"


class CompetitiveStats(BaseModel):
    """Competitive stats for all roles."""
    tank: RankInfo = Field(default_factory=RankInfo)
    damage: RankInfo = Field(default_factory=RankInfo)
    support: RankInfo = Field(default_factory=RankInfo)
    season: int | None = None
    
    def get_highest_rank(self) -> RankInfo:
        """Get the highest rank among all roles."""
        rank_order = [
            "Bronze", "Silver", "Gold", "Platinum", 
            "Diamond", "Master", "Grandmaster", "Champion"
        ]
        
        ranks = [self.tank, self.damage, self.support]
        valid_ranks = [r for r in ranks if r.division]
        
        if not valid_ranks:
            return RankInfo()
        
        def rank_value(r: RankInfo) -> tuple[int, int]:
            div_index = rank_order.index(r.division) if r.division in rank_order else -1
            # Lower tier number is better (Tier 1 > Tier 5)
            return (div_index, -r.tier)
        
        return max(valid_ranks, key=rank_value)


class Account(BaseModel):
    """Overwatch account information."""
    id: str = ""  # Firestore document ID
    battle_tag: str  # e.g., "Player#1234"
    discord_user_id: str  # Discord user who owns this account
    display_name: str = ""  # Friendly name for the account
    is_main: bool = False  # Whether this is the user's main account
    platform: str = "pc"  # pc, console, etc.
    region: str = "us"  # us, eu, asia
    current_stats: CompetitiveStats = Field(default_factory=CompetitiveStats)
    last_updated: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    def to_firestore(self) -> dict[str, Any]:
        """Convert to Firestore-compatible dict."""
        data = self.model_dump(exclude={"id"})
        # Convert datetime to Firestore timestamp format
        if self.last_updated:
            data["last_updated"] = self.last_updated
        data["created_at"] = self.created_at
        return data
    
    @classmethod
    def from_firestore(cls, doc_id: str, data: dict[str, Any]) -> "Account":
        """Create instance from Firestore document."""
        data["id"] = doc_id
        return cls(**data)


class StatsHistory(BaseModel):
    """Historical stats snapshot for tracking progress."""
    id: str = ""
    account_id: str  # Reference to Account
    battle_tag: str
    stats: CompetitiveStats
    recorded_at: datetime = Field(default_factory=datetime.utcnow)
    season: int | None = None
    
    def to_firestore(self) -> dict[str, Any]:
        """Convert to Firestore-compatible dict."""
        data = self.model_dump(exclude={"id"})
        data["recorded_at"] = self.recorded_at
        return data
    
    @classmethod
    def from_firestore(cls, doc_id: str, data: dict[str, Any]) -> "StatsHistory":
        """Create instance from Firestore document."""
        data["id"] = doc_id
        return cls(**data)


class PlayerStats(BaseModel):
    """Aggregated player stats across all accounts."""
    discord_user_id: str
    accounts: list[Account] = Field(default_factory=list)
    total_games_played: int = 0
    
    def get_main_account(self) -> Account | None:
        """Get the user's main account."""
        for account in self.accounts:
            if account.is_main:
                return account
        return self.accounts[0] if self.accounts else None


class UserPreferences(BaseModel):
    """User preferences stored in Firestore."""
    id: str = ""  # Firestore document ID (same as discord_user_id)
    discord_user_id: str
    default_battle_tag: str | None = None
    notification_enabled: bool = True
    spotify_linked: bool = False
    spotify_refresh_token: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    def to_firestore(self) -> dict[str, Any]:
        """Convert to Firestore-compatible dict."""
        data = self.model_dump(exclude={"id", "spotify_refresh_token"})
        data["created_at"] = self.created_at
        data["updated_at"] = datetime.utcnow()
        # Store sensitive data separately or encrypt
        return data
    
    @classmethod
    def from_firestore(cls, doc_id: str, data: dict[str, Any]) -> "UserPreferences":
        """Create instance from Firestore document."""
        data["id"] = doc_id
        return cls(**data)


class MusicQueueItem(BaseModel):
    """Item in the music queue."""
    id: str = ""
    title: str
    url: str
    duration: int = 0  # Duration in seconds
    requested_by: str  # Discord user ID
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    
    def to_firestore(self) -> dict[str, Any]:
        """Convert to Firestore-compatible dict."""
        return self.model_dump(exclude={"id"})


class GuildMusicState(BaseModel):
    """Music state for a Discord guild."""
    guild_id: str
    current_track: MusicQueueItem | None = None
    queue: list[MusicQueueItem] = Field(default_factory=list)
    is_playing: bool = False
    volume: float = 0.5
    loop_mode: str = "off"  # off, single, queue
    updated_at: datetime = Field(default_factory=datetime.utcnow)

