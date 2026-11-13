from fastapi import APIRouter,Request
from backend.models.api import CommandRequest

router=APIRouter(prefix='/api/jobs',tags=['jobs'])

@router.get('')
def jobs(request:Request,project_id:str|None=None):
    rows=request.app.state.jobs.list(project_id)
    from story_channel.file_cache import read_json
    for row in rows:
        if row['operation']=='render.assemble_final' and row['status']=='running':
            path=request.app.state.service.manager.project_path(row['project_id'])/'assembly_progress.json'
            if path.is_file():row['progress']=read_json(path)
    return rows

@router.post('',status_code=202)
def submit(body:CommandRequest,request:Request):
    service=request.app.state.service
    if body.operation not in service.operations(body.project_id):raise ValueError('Unknown operation')
    return request.app.state.jobs.submit(body)

@router.get('/{job_id}')
def job(job_id:str,request:Request):return request.app.state.jobs.get(job_id)

@router.post('/{job_id}/stop')
def stop(job_id:str,request:Request):return request.app.state.jobs.stop(job_id)
