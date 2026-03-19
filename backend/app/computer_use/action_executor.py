"""
Action Executor — translates Claude Computer Use tool-use responses
into actual mouse, keyboard, and scroll actions on the desktop.

All actions are rate-limited (one at a time, with a delay) to give
the UI time to respond before the next screenshot is taken.
"""
import asyncio
import logging
from typing import Optional

log = logging.getLogger(__name__)


class ActionExecutor:
    """
    Executes individual computer actions produced by the Computer Use API.
    All methods are static — the executor is stateless.
    """

    @staticmethod
    async def execute(action: str, params: dict) -> str:
        """
        Dispatch to the correct action handler.
        Returns a string describing what happened (for logging).
        """
        loop = asyncio.get_event_loop()
        handler = ACTION_HANDLERS.get(action)
        if handler is None:
            log.warning("Unknown computer action: %s", action)
            return f"Unknown action: {action}"

        # Run blocking pyautogui calls in thread pool
        result = await loop.run_in_executor(None, handler, params)
        log.debug("Executed %s → %s", action, result)
        return result


# ─── Individual action handlers (blocking, run in thread pool) ────────────────

def _screenshot(params: dict) -> str:
    # The engine handles screenshots separately via ScreenshotManager
    return "screenshot requested (handled by engine)"


def _mouse_move(params: dict) -> str:
    try:
        import pyautogui
        x, y = params["coordinate"]
        pyautogui.moveTo(x, y, duration=0.2)
        return f"mouse moved to ({x}, {y})"
    except Exception as e:
        return f"mouse_move error: {e}"


def _left_click(params: dict) -> str:
    try:
        import pyautogui
        x, y = params["coordinate"]
        pyautogui.click(x, y)
        return f"left click at ({x}, {y})"
    except Exception as e:
        return f"left_click error: {e}"


def _double_click(params: dict) -> str:
    try:
        import pyautogui
        x, y = params["coordinate"]
        pyautogui.doubleClick(x, y)
        return f"double click at ({x}, {y})"
    except Exception as e:
        return f"double_click error: {e}"


def _type_text(params: dict) -> str:
    try:
        import pyautogui
        text = params.get("text", "")
        pyautogui.typewrite(text, interval=0.04)
        return f"typed {len(text)} chars"
    except Exception as e:
        return f"type error: {e}"


def _press_key(params: dict) -> str:
    try:
        import pyautogui
        keys = params.get("text", "").split("+")
        if len(keys) > 1:
            pyautogui.hotkey(*[k.lower().strip() for k in keys])
        else:
            pyautogui.press(keys[0].lower().strip())
        return f"pressed key: {params.get('text')}"
    except Exception as e:
        return f"key error: {e}"


def _scroll(params: dict) -> str:
    try:
        import pyautogui
        x, y = params.get("coordinate", (None, None))
        direction = params.get("direction", "down")
        amount = params.get("amount", 3)
        clicks = amount if direction == "down" else -amount
        if x is not None and y is not None:
            pyautogui.scroll(clicks, x, y)
        else:
            pyautogui.scroll(clicks)
        return f"scrolled {direction} {amount}"
    except Exception as e:
        return f"scroll error: {e}"


ACTION_HANDLERS: dict = {
    "screenshot": _screenshot,
    "mouse_move": _mouse_move,
    "left_click": _left_click,
    "double_click": _double_click,
    "type": _type_text,
    "key": _press_key,
    "scroll": _scroll,
}
