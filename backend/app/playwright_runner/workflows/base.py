from abc import ABC, abstractmethod


class BaseWorkflow(ABC):
    name: str = ""
    requires_auth: bool = False

    @abstractmethod
    async def run(self, page, params: dict) -> dict:
        """Execute the workflow against an already-navigated Playwright page."""
