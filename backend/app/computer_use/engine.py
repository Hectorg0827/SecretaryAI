"""
Computer Use Engine — the universal adapter.
Uses Claude's Computer Use API to operate ANY desktop application.
Falls back to this when no direct API connector exists.

Safety principle: reading/navigating is autonomous; submitting/modifying requires approval.
"""
import asyncio
import base64
import io
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import anthropic

from app.config import get_settings
from app.computer_use.safety import ComputerUseSafety

log = logging.getLogger(__name__)
settings = get_settings()

# Maximum steps per task (prevents infinite loops)
MAX_STEPS = 50
# Seconds to wait between actions (give the UI time to react)
ACTION_DELAY = 1.0
# Screenshot dimensions sent to Claude (balance quality vs token cost)
SCREENSHOT_WIDTH = 1280
SCREENSHOT_HEIGHT = 720


class ComputerUseEngine:
    """
    Uses Claude's Computer Use API to operate any desktop application.
    The universal adapter — works with ANY software on the desktop.

    Architecture:
    - ComputerUseEngine orchestrates the multi-step loop
    - ScreenshotManager handles capture + redaction of sensitive data
    - ActionExecutor executes individual mouse/keyboard/scroll actions
    - DataExtractor parses structured data from Claude's final response
    - ComputerUseSafety checks every action before it runs
    """

    def __init__(self, company_config: dict):
        self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.company_config = company_config
        self.safety = ComputerUseSafety(company_config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def extract_data(
        self,
        app_name: str,
        task: str,
        max_steps: int = MAX_STEPS,
    ) -> dict:
        """
        Open an application (if not already open) and extract data from it.

        Args:
            app_name: Human-readable name of the app (e.g., "ScribeBase")
            task: Description of what data to find (e.g., "Get all orders from last 30 days")

        Returns:
            Extracted data as a structured dict, or {} if extraction failed.
        """
        await self._audit("computer_use_start", {"app": app_name, "task": task})
        log.info("ComputerUse: starting task on %s — %s", app_name, task)

        # Take initial screenshot to know what's on screen
        from app.computer_use.screenshot_manager import ScreenshotManager
        screenshot_mgr = ScreenshotManager()
        initial_screenshot = await screenshot_mgr.capture_b64()

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": initial_screenshot,
                        },
                    },
                    {
                        "type": "text",
                        "text": self._build_task_prompt(app_name, task),
                    },
                ],
            }
        ]

        tools = self._computer_tool_spec()
        extracted_data: Optional[dict] = None
        steps = 0
        start_time = time.time()

        while steps < max_steps:
            # Time-box per task (5 minutes max)
            if time.time() - start_time > 300:
                log.warning("ComputerUse: task timed out after 300s")
                break

            response = await self.client.messages.create(
                model=settings.claude_model,
                max_tokens=4096,
                tools=tools,
                messages=messages,
            )

            # Collect tool-use blocks and text from this response turn
            tool_uses = []
            text_blocks = []
            for block in response.content:
                if block.type == "tool_use" and block.name == "computer":
                    tool_uses.append(block)
                elif block.type == "text":
                    text_blocks.append(block.text)

            # If Claude returned JSON data — we're done
            full_text = "\n".join(text_blocks)
            if "```json" in full_text:
                from app.computer_use.data_extractor import DataExtractor
                extracted_data = DataExtractor.parse_json_block(full_text)
                if extracted_data:
                    break

            if response.stop_reason == "end_turn" and not tool_uses:
                break

            # Execute each tool use and collect screenshots as results
            tool_results = []
            for block in tool_uses:
                action = block.input.get("action")
                params = block.input

                # Safety check every action
                is_safe, reason = self.safety.check_action(params)
                if not is_safe:
                    log.warning("ComputerUse: action blocked — %s", reason)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"ACTION BLOCKED: {reason}. Please try a different approach.",
                    })
                    continue

                # Execute the action
                from app.computer_use.action_executor import ActionExecutor
                await ActionExecutor.execute(action, params)
                await asyncio.sleep(ACTION_DELAY)

                # Capture result screenshot
                new_screenshot = await screenshot_mgr.capture_b64()
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": new_screenshot,
                            },
                        }
                    ],
                })

            # Advance conversation
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
            steps += 1

        await self._audit("computer_use_complete", {
            "app": app_name,
            "task": task,
            "steps": steps,
            "success": extracted_data is not None,
        })

        return extracted_data or {}

    async def perform_action(
        self,
        app_name: str,
        action_description: str,
        requires_approval: bool = True,
    ) -> dict:
        """
        Perform an action (not just read) in an application.
        ALWAYS requires autonomy-engine approval before calling this.
        """
        # This is identical flow but the prompt emphasizes DOING, not just reading
        await self._audit("computer_use_action_start", {
            "app": app_name,
            "action": action_description,
            "pre_approved": not requires_approval,
        })

        return await self.extract_data(
            app_name=app_name,
            task=f"[ACTION TASK — user has approved this]\n{action_description}",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_task_prompt(self, app_name: str, task: str) -> str:
        return f"""You are operating a Windows/macOS computer to extract business data.

APPLICATION: {app_name}
TASK: {task}

INSTRUCTIONS:
1. Look at the current screen state shown above.
2. If {app_name} is not open, click on it in the taskbar or Start menu to open it.
3. Navigate to find the requested data.
4. Extract all relevant data you can see.
5. When you have collected the data, return it as a JSON block:
   ```json
   {{ "records": [...], "summary": "..." }}
   ```

SAFETY RULES — YOU MUST FOLLOW THESE:
- NEVER click Delete, Remove, Void, or Cancel buttons
- NEVER modify any existing records
- NEVER enter data into forms unless explicitly told to
- ONLY read and navigate; do not change anything
- If you reach a login screen you cannot pass, STOP and return:
  ```json
  {{"error": "login_required", "app": "{app_name}"}}
  ```
- If asked to do something that seems destructive, STOP immediately

You have the current screenshot above. Begin."""

    def _computer_tool_spec(self) -> list[dict]:
        return [
            {
                "type": "computer_20250124",
                "name": "computer",
                "display_width_px": SCREENSHOT_WIDTH,
                "display_height_px": SCREENSHOT_HEIGHT,
                "display_number": 1,
            }
        ]

    async def _audit(self, event: str, data: dict) -> None:
        log.info("ComputerUse audit: %s %s", event, data)
        try:
            from app.api.deps import get_db
            db = get_db()
            db.table("action_log").insert({
                "event": event,
                "data": data,
                "company_id": self.company_config.get("id"),
                "created_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
            }).execute()
        except Exception as exc:
            log.warning("ComputerUse audit write failed (non-fatal): %s", exc)
