"""Run from project root: python -m uvicorn backend.main:app."""
from contextlib import asynccontextmanager
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI,Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse,FileResponse

from backend.paths import PROJECT_ROOT,storage_paths
from backend.services.workspace import WorkspaceService
from backend.workers.jobs import Jobs


def create_app(settings=None,*,database=None):
    @asynccontextmanager
    async def lifespan(app):
        paths=storage_paths().ensure()
        app.state.service=WorkspaceService(settings)
        app.state.jobs=Jobs(database or paths.database/'jobs.sqlite3',app.state.service.execute)
        from backend.services.frontend_host import FrontendHost
        app.state.frontend=FrontendHost(PROJECT_ROOT/'frontend',paths.temp/'frontend-hosting')
        if os.getenv('STORY_FRONTEND_WATCH','0').lower() in ('1','true','yes'):
            app.state.frontend.start()
        previous_temp=tempfile.tempdir
        tempfile.tempdir=str(paths.temp)
        try:yield
        finally:
            app.state.frontend.stop()
            app.state.jobs.shutdown()
            tempfile.tempdir=previous_temp
    app=FastAPI(title='Story Studio',version='0.4.0',lifespan=lifespan)
    origins=os.getenv('STORY_ALLOWED_ORIGINS','http://localhost:7357,http://127.0.0.1:7357').split(',')
    app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=['GET','POST','PUT','DELETE'],allow_headers=['Content-Type'])
    from backend.routers import projects,jobs,media,settings as settings_router
    for router in (projects.router,jobs.router,media.router,settings_router.router):app.include_router(router)
    @app.exception_handler(ValueError)
    async def invalid(request,exc):return JSONResponse(status_code=409 if 'changed' in str(exc).lower() else 400,content={'detail':str(exc)})
    @app.exception_handler(FileNotFoundError)
    async def missing(request,exc):return JSONResponse(status_code=404,content={'detail':'Resource not found'})
    @app.exception_handler(KeyError)
    async def unknown(request,exc):return JSONResponse(status_code=404,content={'detail':'Resource not found'})
    @app.get('/api/health')
    def health():return {'status':'ok','application':'Story Studio','frontend':'Flutter Web','storage':'local'}
    @app.get('/api/frontend/status')
    def frontend_status(request:Request):return JSONResponse(request.app.state.frontend.status(),headers={'Cache-Control':'no-store'})
    @app.get('/{relative:path}',include_in_schema=False)
    def frontend(relative:str,request:Request):
        build=request.app.state.frontend.current.resolve()
        target=(build/relative).resolve()
        if not target.is_relative_to(build):raise ValueError('Invalid frontend path')
        headers={'Cache-Control':'no-store'}
        if target.is_file():return FileResponse(target,headers=headers)
        if relative.startswith('api/'):return JSONResponse(status_code=404,content={'detail':'Unknown API endpoint'})
        if (build/'index.html').is_file():return FileResponse(build/'index.html',headers=headers)
        return JSONResponse(status_code=404,content={'detail':'Build frontend with flutter build web, or use its development server.'})
    return app


app=create_app()
