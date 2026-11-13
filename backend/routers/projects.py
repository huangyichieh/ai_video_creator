from fastapi import APIRouter,Request
from backend.models.api import CreateWorldRequest,DeleteRequest,ArtifactEdit
from pydantic import BaseModel,Field

class PrepareRequest(BaseModel):
    operation: str
    arguments: dict=Field(default_factory=dict)

router=APIRouter(prefix='/api/projects',tags=['projects'])

@router.get('/{project_id}/exports/{name}')
def export_markdown(project_id:str,name:str,request:Request):
    from fastapi.responses import Response
    from story_channel.modules import MODULES
    from story_channel.approval_exports import readable_export
    manager=request.app.state.service.manager
    manager.load(project_id)
    allowed={module.filename for module in MODULES.values()}|{'outline.json','world_setting.json','episode_outline.json'}
    if name not in allowed:raise ValueError('Unknown export document')
    file=manager.project_path(project_id)/name
    if not file.is_file():raise FileNotFoundError(file)
    return Response(readable_export(file),media_type='text/markdown',
        headers={'Content-Disposition':f'attachment; filename="{file.stem}.md"'})

@router.get('')
def projects(request:Request):return request.app.state.service.projects()

@router.post('',status_code=201)
def create(body:CreateWorldRequest,request:Request):return request.app.state.service.create_world(body)

@router.delete('/{project_id}')
def delete(project_id:str,body:DeleteRequest,request:Request):
    manager=request.app.state.service.manager
    affected={project_id}|{p.project_id for p in manager.list_projects() if p.world_project_id==project_id}
    if any(job['project_id'] in affected and job['status'] in ('queued','running','stopping') for job in request.app.state.jobs.list()):
        raise ValueError('Stop active tasks and wait for them to finish before deleting this project.')
    return {'deleted':manager.delete(project_id,confirmation=body.confirmation)}

@router.get('/{project_id}/workspace/{page}')
def workspace(project_id:str,page:str,request:Request):return request.app.state.service.snapshot(project_id,page)

@router.get('/{project_id}/operations')
def operations(project_id:str,request:Request):return request.app.state.service.catalog(project_id)

@router.get('/{project_id}/errors')
def errors(project_id:str,request:Request):
    from story_channel.error_reports import reports
    manager=request.app.state.service.manager
    manager.load(project_id)
    return reports(manager.project_path(project_id))

@router.get('/{project_id}/reviews/{kind}')
def review(project_id:str,kind:str,request:Request):
    from story_channel.pipeline import Pipeline
    from story_channel.review_changes import current_session,storyboard_approval_snapshot
    from story_channel.files import input_hash
    pipeline=Pipeline(request.app.state.service.manager,project_id)
    if kind in ('outline','script','prompts'):
        from story_channel.studio import TextReview,content_digest,review_differences
        from story_channel.files import read_text
        file=pipeline.path/f'episode_{kind}_review.json'
        if not file.is_file():return {'session':None}
        report=TextReview.model_validate_json(read_text(file))
        text=pipeline.manager.read_outline(project_id) if kind=='outline' else pipeline.artifact(kind).model_dump_json(by_alias=True,indent=2)
        if report.source_hash!=input_hash(text):raise ValueError('Content changed. Run AI Review again for the current version.')
        original,changes=review_differences(kind,text,report.advice.proposed_text or text)
        return {'session':{'original':original,'changes':[{**item,'reason':'AI review suggestion'} for item in changes]},
            'expected':content_digest(pipeline,kind),'reportDigest':input_hash(report.model_dump()),'sourceText':text}
    session=current_session(pipeline,kind)
    if session is None:return {'session':None}
    return {'session':session.model_dump(mode='json',by_alias=True),
            'expected':input_hash(session.model_dump(mode='json')),
            'approvalExpected':storyboard_approval_snapshot(pipeline) if kind=='storyboard' else None}

@router.get('/{project_id}/revisions')
def revisions(project_id:str,request:Request,subject:str,stage:str='scene_images',video:bool=False):
    from story_channel.assets import AssetPipeline
    from story_channel.scene_production import SceneProduction
    from story_channel.files import input_hash
    from backend.services.workspace import plain
    manager=request.app.state.service.manager
    if video:
        service=SceneProduction(manager,project_id)
        return {'records':plain(service.revisions(subject)),'expected':input_hash(service.visuals().get(subject,{}))}
    return {'records':plain(AssetPipeline(manager,project_id).revisions(stage,subject))}

@router.get('/{project_id}/keyframes/{token}')
def keyframes(project_id:str,token:str,request:Request):
    from story_channel.assets import AssetPipeline
    from story_channel.scene_keyframes import load
    return load(AssetPipeline(request.app.state.service.manager,project_id),token)

@router.post('/{project_id}/prepare')
def prepare(project_id:str,body:PrepareRequest,request:Request):return request.app.state.service.prepare(project_id,body.operation,body.arguments)

@router.put('/{project_id}/artifacts/{name}')
def edit(project_id:str,name:str,body:ArtifactEdit,request:Request):
    return request.app.state.service.edit_artifact(project_id,name,body.value,body.expected)
