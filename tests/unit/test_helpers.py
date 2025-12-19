"""Unit tests for utility helpers."""

import pytest

from src.utils.helpers import format_duration, pretty_format, sanitize_filename, truncate_text


class TestFormatDuration:
    """Tests for format_duration function."""
    
    def test_seconds_only(self):
        """Test formatting with only seconds."""
        assert format_duration(45) == "0:45"
    
    def test_minutes_and_seconds(self):
        """Test formatting with minutes and seconds."""
        assert format_duration(185) == "3:05"
    
    def test_hours(self):
        """Test formatting with hours."""
        assert format_duration(3661) == "1:01:01"
    
    def test_zero(self):
        """Test formatting zero duration."""
        assert format_duration(0) == "0:00"
    
    def test_negative(self):
        """Test formatting negative duration."""
        assert format_duration(-10) == "0:00"


class TestTruncateText:
    """Tests for truncate_text function."""
    
    def test_no_truncation_needed(self):
        """Test text shorter than max length."""
        result = truncate_text("Hello", max_length=10)
        assert result == "Hello"
    
    def test_truncation_with_default_suffix(self):
        """Test truncation with default suffix."""
        result = truncate_text("Hello, World!", max_length=10)
        assert result == "Hello, ..."
        assert len(result) == 10
    
    def test_truncation_with_custom_suffix(self):
        """Test truncation with custom suffix."""
        result = truncate_text("Hello, World!", max_length=10, suffix="…")
        assert result == "Hello, Wo…"
    
    def test_exact_length(self):
        """Test text exactly at max length."""
        result = truncate_text("Hello", max_length=5)
        assert result == "Hello"


class TestSanitizeFilename:
    """Tests for sanitize_filename function."""
    
    def test_removes_slashes(self):
        """Test removing forward and back slashes."""
        assert sanitize_filename("path/to/file") == "path_to_file"
        assert sanitize_filename("path\\to\\file") == "path_to_file"
    
    def test_removes_special_chars(self):
        """Test removing special characters."""
        assert sanitize_filename('file:name*test?"<>|') == "file_name_test_____"
    
    def test_preserves_valid_chars(self):
        """Test preserving valid characters."""
        assert sanitize_filename("valid-file_name.txt") == "valid-file_name.txt"


class TestPrettyFormat:
    """Tests for pretty_format function."""
    
    def test_format_string(self):
        """Test formatting a string."""
        assert pretty_format("Hello") == "Hello"
    
    def test_format_list(self):
        """Test formatting a list."""
        result = pretty_format(["a", "b", "c"])
        assert "- a" in result
        assert "- b" in result
        assert "- c" in result
    
    def test_format_dict(self):
        """Test formatting a dictionary."""
        result = pretty_format({"key": "value"})
        assert "key: value" in result
    
    def test_format_nested_dict(self):
        """Test formatting nested dictionary."""
        result = pretty_format({"outer": {"inner": "value"}})
        assert "outer:" in result
        assert "inner: value" in result

