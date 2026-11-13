from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter,File,Request,UploadFile,Query
from fastapi.responses import FileResponse
from backend.paths import storage_paths

router=APIRouter(prefix='/api',tags=['media'])
ALLOWED={'.png','.jpg','.jpeg','.webp','.gif','.wav','.mp3','.m4a','.flac','.mp4','.mov','.webm','.ogg','.srt','.vtt','.svg','.pdf','.docx'}

@router.post('/uploads',status_code=201)
async def upload(file:UploadFile=File(...)):
    suffix=Path(file.filename or '').suffix.lower()
    if suffix not in ALLOWED:raise ValueError('Unsupported media file type')
    name=uuid4().hex+suffix
    target=storage_paths().ensure().uploads/name
    size=0
    try:
        with target.open('wb') as stream:
            while block:=await file.read(1024*1024):
                size+=len(block)
                if size>200*1024*1024:raise ValueError('Upload exceeds 200 MB')
                stream.write(block)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {'upload':name,'filename':file.filename,'size':size}

@router.get('/projects/{project_id}/media/{relative:path}')
def media(project_id:str,relative:str,request:Request,download:bool=False,width:int|None=Query(default=None,ge=64,le=1200)):
    from story_channel.assets import AssetPipeline
    service=request.app.state.service
    service.manager.load(project_id)
    path=AssetPipeline(service.manager,project_id).local_path(relative)
    if path.suffix.lower() not in ALLOWED:raise ValueError('This file type is not served as media')
    if not path.is_file():raise FileNotFoundError(path)
    if width and not download and path.suffix.lower() in ('.png','.jpg','.jpeg','.webp'):
        from backend.services.media_preview import thumbnail
        return FileResponse(thumbnail(path,width),media_type='image/webp')
    return FileResponse(path,filename=path.name if download else None)
