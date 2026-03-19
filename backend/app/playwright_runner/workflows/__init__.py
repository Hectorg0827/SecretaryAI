from app.playwright_runner.workflows.customs_portal import CustomsPortalWorkflow
from app.playwright_runner.workflows.distributor_portal import DistributorPortalWorkflow

WORKFLOWS: dict[str, type] = {
    CustomsPortalWorkflow.name: CustomsPortalWorkflow,
    DistributorPortalWorkflow.name: DistributorPortalWorkflow,
}
