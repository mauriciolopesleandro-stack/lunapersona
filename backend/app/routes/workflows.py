from dataclasses import asdict

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/workflows")
async def list_workflows(request: Request):
    workflow_manager = request.app.state.workflow_manager
    workflows = []
    for wf in workflow_manager.list_workflows():
        data = asdict(wf)
        data.pop("graph_template", None)
        workflows.append(data)
    return {"workflows": workflows}
