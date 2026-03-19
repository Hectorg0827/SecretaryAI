"""Tests for Computer Use safety layer."""
import pytest
from app.computer_use.safety import ComputerUseSafety


@pytest.fixture
def safety():
    return ComputerUseSafety(company_config={})


def test_right_click_blocked(safety):
    is_safe, reason = safety.check_action({"action": "right_click", "coordinate": [100, 100]})
    assert not is_safe
    assert "right-click" in reason.lower()


def test_delete_key_blocked(safety):
    is_safe, reason = safety.check_action({"action": "key", "text": "delete"})
    assert not is_safe


def test_shift_delete_blocked(safety):
    is_safe, reason = safety.check_action({"action": "key", "text": "shift+delete"})
    assert not is_safe


def test_normal_click_allowed(safety):
    is_safe, reason = safety.check_action({"action": "left_click", "coordinate": [500, 400]})
    assert is_safe


def test_typing_text_allowed(safety):
    is_safe, reason = safety.check_action({"action": "type", "text": "search term"})
    assert is_safe


def test_typing_dangerous_text_blocked(safety):
    is_safe, reason = safety.check_action({"action": "type", "text": "delete all records"})
    assert not is_safe


def test_blocked_app_window(safety):
    is_safe, reason = safety.check_app_window("Windows PowerShell")
    assert not is_safe


def test_allowed_app_window(safety):
    is_safe, reason = safety.check_app_window("ScribeBase - Orders")
    assert is_safe


def test_system_tray_region_blocked(safety):
    # Windows system tray is in bottom-right corner
    is_safe, reason = safety.check_action({
        "action": "left_click",
        "coordinate": [1850, 1060]  # Inside blocked region
    })
    assert not is_safe


def test_customer_blocked_apps(safety_with_custom_blocks):
    is_safe, reason = safety_with_custom_blocks.check_app_window("MyBankApp - Login")
    assert not is_safe


@pytest.fixture
def safety_with_custom_blocks():
    return ComputerUseSafety(company_config={"blocked_apps": ["mybankapp"]})
