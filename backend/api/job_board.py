from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from backend.api.dependencies import get_services
from backend.services import ApplicationServices
from backend import job_board

router = APIRouter(prefix='/nodes', tags=['jobs'])


class JobAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['refresh', 'pause', 'resume', 'cancel']
    revision: int = Field(ge=1)


@router.get('/{node_id}/jobs')
async def jobs(node_id: str, services: ApplicationServices = Depends(get_services)):
    async with services._node_mutation(read_only=True):
        return job_board.listing(services, node_id)


@router.post('/{node_id}/jobs/{job_id}/actions')
async def control(node_id: str, job_id: str, request: JobAction, services: ApplicationServices = Depends(get_services)):
    return await job_board.control(services, node_id, job_id, request.action, request.revision)
