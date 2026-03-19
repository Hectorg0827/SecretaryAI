import logging

from app.playwright_runner.workflows.base import BaseWorkflow

log = logging.getLogger(__name__)


class CustomsPortalWorkflow(BaseWorkflow):
    name = "customs_portal"
    requires_auth = True

    async def run(self, page, params: dict) -> dict:
        portal_url = params["portal_url"]
        username = params.get("username", "")
        password = params.get("password", "")

        await page.goto(portal_url, wait_until="networkidle")

        # Attempt login if credentials are provided and a login form is visible
        if username and password:
            login_form = await page.query_selector("form")
            if login_form:
                user_field = await page.query_selector(
                    "input[type='text'], input[type='email'], input[name*='user'], input[name*='login']"
                )
                pass_field = await page.query_selector("input[type='password']")
                if user_field and pass_field:
                    await user_field.fill(username)
                    await pass_field.fill(password)
                    await page.keyboard.press("Enter")
                    await page.wait_for_load_state("networkidle")
                    log.info("CustomsPortalWorkflow: logged in to %s", portal_url)

        # Extract shipment rows from any visible table
        shipments: list[dict] = []
        rows = await page.query_selector_all("table tr")
        headers: list[str] = []
        for i, row in enumerate(rows):
            cells = await row.query_selector_all("th, td")
            cell_texts = [await c.inner_text() for c in cells]
            if i == 0:
                headers = [t.strip() for t in cell_texts]
            elif cell_texts:
                shipments.append(dict(zip(headers, [t.strip() for t in cell_texts])))

        log.info("CustomsPortalWorkflow: extracted %d shipment rows", len(shipments))
        return {"shipments": shipments, "source": "playwright"}
