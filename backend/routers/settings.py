from fastapi import APIRouter,Request
from backend.models.api import KeysUpdate
from backend.services.settings import saved_keys,save_keys,quotas

router=APIRouter(prefix='/api/settings',tags=['settings'])

@router.get('/keys')
def keys():return saved_keys()

@router.put('/keys')
def update(body:KeysUpdate,request:Request):
    if any(job['status'] in ('queued','running','stopping') for job in request.app.state.jobs.list()):
        raise ValueError('Wait for active generation to finish before changing API keys.')
    return save_keys(body.values)

@router.get('/quotas')
def balances():return quotas()
