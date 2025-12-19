"""Utility helper functions."""

from typing import Any


def pretty_format(message: Any, indent: int = 0) -> str:
    """Format a message for display.
    
    Args:
        message: Message to format (dict, list, or str)
        indent: Current indentation level
        
    Returns:
        Formatted string
    """
    prefix = "  " * indent
    
    if isinstance(message, dict):
        lines = []
        for k, v in message.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{prefix}{k}:")
                lines.append(pretty_format(v, indent + 1))
            else:
                lines.append(f"{prefix}{k}: {v}")
        return "\n".join(lines)
    
    if isinstance(message, list):
        lines = [f"{prefix}- {item}" for item in message]
        return "\n".join(lines)
    
    return f"{prefix}{message}"


def format_duration(seconds: int) -> str:
    """Format seconds into a human-readable duration.
    
    Args:
        seconds: Duration in seconds
        
    Returns:
        Formatted duration string (e.g., "3:45" or "1:23:45")
    """
    if seconds < 0:
        return "0:00"
    
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """Truncate text to a maximum length.
    
    Args:
        text: Text to truncate
        max_length: Maximum length including suffix
        suffix: Suffix to append when truncated
        
    Returns:
        Truncated text
    """
    if len(text) <= max_length:
        return text
    
    return text[:max_length - len(suffix)] + suffix


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename by removing invalid characters.
    
    Args:
        filename: Original filename
        
    Returns:
        Sanitized filename
    """
    import re
    # Replace path separators and other invalid chars with underscore
    return re.sub(r'[\\/:*?"<>|]', '_', filename)

