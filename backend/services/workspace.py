"""HTTP-facing services adapt existing engines without duplicating their rules."""
from dataclasses import asdict,is_dataclass
from enum import Enum
import importlib
import inspect
import json
from pathlib import Path
from typing import get_type_hints

from pydantic import BaseModel,TypeAdapter

from backend.paths import storage_paths,relocated_path
from story_channel.config import Settings
from story_channel.projects import ProjectManager
from story_channel.files import input_hash


def plain(value):
    if isinstance(value,BaseModel):return value.model_dump(mode='json',by_alias=True)
    if is_dataclass(value):return plain(asdict(value))
    if isinstance(value,dict):return {str(k):plain(v) for k,v in value.items()}
    if isinstance(value,(list,tuple,set)):return [plain(v) for v in value]
    if isinstance(value,Path):return str(value)
    if isinstance(value,Enum):return value.value
    return json.loads(json.dumps(value,default=str))


CLASS_TARGETS={
    'world':('studio','WorldStudio'), 'episodes':('studio','Episodes'),
    'pipeline':('pipeline','Pipeline'),'assets':('assets','AssetPipeline'),
    'background':('background_library','BackgroundLibrary'),
    'library':('library_media','LibraryMedia'),'identity':('identity','IdentityManager'),
    'animation':('animation','AnimationDirector'),'scene':('scene_production','SceneProduction'),
    'render':('rendering','RenderPipeline'),'characters':('character_removal','CharacterRemoval'),
    'visual':('visual_generation','VisualGeneration'),'character_inputs':('character_visual_inputs','CharacterVisualInputs'),
    'style':('style_rules','StyleRules'),
}
FUNCTION_TARGETS={
    'storyboard_editor','storyboard_regeneration','storyboard_split','storyboard_scene_insertion','storyboard_scene_removal',
    'storyboard_scene_assets','storyboard_prop_removal','asset_sources','asset_revisions','scene_visual_revisions',
    'scene_keyframes','scene_keyframe_video','scene_video_extension','scene_video_region','voice_lines',
    'location_details','location_detail_zoom','location_detail_removal','floor_plan_revisions','floor_plan_guides',
    'location_layout','location_pairs','directional_masters','shared_room','room_canonical','prop_revisions','prop_removal',
    'audio_assets','bgm_library','storyboard_audio','storyboard_assets','check_asset_sync','storyboard_asset_check',
    'generation_preferences','voice_design','voice_validation','style_check','scene_continuity_review','dialogue_review',
    'episode_review','episode_reviews','review_changes','worldview','scene_direction_regeneration','studio','prop_drafts','pixabay_music','voice_samples',
}
DENY={'save_state','local_path','project_path','pipeline','save','create','canon','require_approved',
      'provider','enqueue','can_enqueue','max_tasks','configure_logging','read_text','atomic_write',
      'read','draft_file','folder','file_hash','write_bytes','main','configure','ffmpeg','preview','playback_preview'}
# Domain saves/configuration validate optimistic fingerprints themselves.
DOMAIN_SAVES={'identity.save','render.configure','finishing.configure','generation_preferences.save_preferences',
              'storyboard_editor.save','voice_lines.save','storyboard_editor.save_scene','episodes.create','style.save','prop_drafts.save'}
INJECT={'manager','project_id','world_id','pipeline','engine','service','provider','stop_requested','media','director','owner'}

def internal_signature(fn):
    return any(name in {'state','manifest','record','task','tasks','cls','self'} and param.default is inspect.Parameter.empty
        for name,param in inspect.signature(fn).parameters.items())


class WorkspaceService:
    def __init__(self,settings=None):
        self.settings=settings or Settings.load()
        self.manager=ProjectManager(self.settings)
        self.document_migration=self.manager.document_migration

    def targets(self,pid,stop=None):
        from story_channel.pipeline import Pipeline
        from story_channel.assets import AssetPipeline
        from story_channel.rendering import RenderPipeline
        from story_channel.scene_production import SceneProduction
        manifest=self.manager.load(pid)
        assets=AssetPipeline(self.manager,pid)
        render=RenderPipeline(self.manager,pid)
        scene=SceneProduction(self.manager,pid)
        objects={'pipeline':assets.creative,'assets':assets,'render':render,'scene':scene,'finishing':render.finishing}
        from backend.services.actions import BatchService
        objects['batch']=BatchService(self.manager,pid)
        for key,(module,name) in CLASS_TARGETS.items():
            if key in objects:continue
            if manifest.world_project_id and key in ('world','episodes','characters'):continue
            cls=getattr(importlib.import_module('story_channel.'+module),name)
            try:
                objects[key]=cls(assets) if key in ('background','visual','character_inputs','style') else cls(self.manager,pid)
            except ValueError:
                continue # A World-only operation is not offered on an episode.
        manifest=self.manager.load(pid)
        if manifest.world_project_id:
            from story_channel.library_media import LibraryMedia
            objects['library']=LibraryMedia(self.manager,manifest.world_project_id)
        if stop:
            for obj in [*objects.values(),assets.creative,scene.director.assets,scene.assets]:
                if hasattr(obj,'stop_requested'):obj.stop_requested=stop.is_set
        return objects

    def operations(self,pid,*,stop=None):
        objects=self.targets(pid,stop)
        result={}
        for target,obj in objects.items():
            for method in dir(type(obj)):
                if method.startswith('_') or method in INJECT:continue
                key=target+'.'+method
                if method in DENY and key not in DOMAIN_SAVES:continue
                fn=getattr(obj,method,None)
                if callable(fn) and not isinstance(getattr(type(obj),method,None),property) and not internal_signature(fn):result[key]=(fn,objects)
        for module_name in FUNCTION_TARGETS:
            try:module=importlib.import_module('story_channel.'+module_name)
            except ModuleNotFoundError:continue
            for name,fn in vars(module).items():
                key=module_name+'.'+name
                if name.startswith('_') or name.endswith('_ui') or name in DENY and key not in DOMAIN_SAVES:continue
                if inspect.isfunction(fn) and fn.__module__==module.__name__:
                    sig=inspect.signature(fn)
                    if set(sig.parameters)&{'manager','pipeline','engine','service','media','director'} and not internal_signature(fn):result[key]=(fn,objects)
        return result

    def catalog(self,pid):
        rows=[]
        for key,(fn,_) in self.operations(pid).items():
            params=[]
            try:hints=get_type_hints(fn)
            except (NameError,TypeError):hints={}
            for name,param in inspect.signature(fn).parameters.items():
                if name in INJECT:continue
                if param.kind in (inspect.Parameter.VAR_KEYWORD,inspect.Parameter.VAR_POSITIONAL):continue
                schema={}
                try:
                    if name in hints:schema=TypeAdapter(hints[name]).json_schema()
                except Exception:pass
                default=None if param.default is inspect.Parameter.empty else plain(param.default)
                params.append({'name':name,'required':param.default is inspect.Parameter.empty,'defaultValue':default,'schema':schema})
            rows.append({'operation':key,'title':key.replace('_',' '),'description':inspect.getdoc(fn) or '', 'parameters':params})
            if key=='visual.update':params.extend([{'name':name,'required':False,'defaultValue':default,'schema':{'type':'string'}} for name,default in [('mode','text'),('prompt','')]])
        return rows

    def execute(self,pid,operation,arguments,stop=None):
        operations=self.operations(pid,stop=stop)
        if operation not in operations:raise ValueError('Unknown operation: '+operation)
        fn,objects=operations[operation]
        args=dict(arguments)
        if operation=='generation_preferences.save_preferences' and 'demo' not in args:
            manifest=self.manager.load(pid)
            owner=self.manager.load(manifest.world_project_id) if manifest.world_project_id else manifest
            args['demo']=getattr(owner.provider_config.get('llm'),'provider',self.settings.llm_provider)=='demo'
        sig=inspect.signature(fn)
        if set(args)&INJECT:raise ValueError('Service dependencies cannot be overridden by clients.')
        try:hints=get_type_hints(fn)
        except Exception:hints={}
        from story_channel.render_models import RenderOptions
        for name,param in sig.parameters.items():
            if name=='manager':args[name]=self.manager
            elif name in ('project_id','world_id'):args[name]=pid
            elif name=='owner':args[name]=self.manager.load(pid).world_project_id or pid
            elif name=='pipeline':args[name]=objects['pipeline']
            elif name=='engine':args[name]=objects['render'] if operation.startswith('scene_continuity_review.') else objects['assets']
            elif name=='service':args[name]=objects['scene']
            elif name=='media':args[name]=objects['library']
            elif name=='director':args[name]=objects['animation']
            elif name=='stop_requested':args[name]=stop.is_set if stop else lambda:False
            elif name=='options' and name not in args:args[name]=objects['render'].load().options
            elif name in args:
                args[name]=self.decode_argument(args[name],name)
                annotation=hints.get(name)
                if inspect.isclass(annotation) and issubclass(annotation,BaseModel):args[name]=annotation.model_validate(args[name])
                elif name=='options' and isinstance(args[name],dict):args[name]=RenderOptions.model_validate(args[name])
                elif name=='settings' and operation=='world.edit_settings':
                    from story_channel.worldview import WorldSettings
                    args[name]=WorldSettings.model_validate(args[name])
                elif name=='library' and operation=='identity.save':
                    from story_channel.identity import IdentityLibrary
                    args[name]=IdentityLibrary.model_validate(args[name])
        from story_channel.error_reports import record_failure,clear_reports
        if operation=='identity.save':
            from story_channel.voice_validation import validate_provider_voice
            previous=objects['identity'].load()
            for speaker,voice in args['library'].voices.items():
                old=previous.voices.get(speaker) if previous else None
                if voice.voice_id and (not old or voice.voice_id!=old.voice_id):
                    validate_provider_voice(self.manager,pid,voice.voice_id,speaker=speaker,demo=objects['assets'].demo)
        try:result=fn(**args)
        except Exception as exc:
            record_failure(self.manager.project_path(pid),'api:'+operation,exc)
            raise
        if not self.manager.project_path(pid).exists():return plain(result)
        clear_reports(self.manager.project_path(pid),'api:'+operation)
        if self.manager.load(pid).world_project_id and operation.startswith(('library.','prop_drafts.','prop_revisions.','prop_removal.')):
            from story_channel.check_asset_sync import synchronize
            synchronize(self.manager,pid)
        return plain(result)

    def prepare(self,pid,operation,arguments):
        """Capture current optimistic guards when opening a native action form."""
        from story_channel.modules import MODULES,STEPS
        from story_channel.background_library import locations
        from story_channel.asset_models import ASSET_STAGES
        operations=self.operations(pid)
        if operation not in operations:raise ValueError('Unknown operation')
        fn,objects=operations[operation]
        params=inspect.signature(fn).parameters
        values={name:plain(param.default) for name,param in params.items() if param.default is not inspect.Parameter.empty and name not in INJECT}
        values.update(arguments)
        for name in ('modes','images','references','subjects','decisions'):
            if name in params and values.get(name) is None and params[name].default is inspect.Parameter.empty:values[name]=[]
        if operation=='generation_preferences.save_preferences':
            manifest=self.manager.load(pid)
            owner=self.manager.load(manifest.world_project_id) if manifest.world_project_id else manifest
            values['demo']=getattr(owner.provider_config.get('llm'),'provider',self.settings.llm_provider)=='demo'
        for name in ('duration','seconds','start_time','end_time','position','fov'):
            if name in params and values.get(name) is None:
                values[name]=300 if name=='duration' and operation.startswith('episodes.') else 3 if name in ('duration','seconds','end_time') else 70 if name=='fov' else 0
        if 'instructions' in params and operation=='storyboard_split.split':values.setdefault('instructions',['',''])
        pipeline,assets=objects['pipeline'],objects['assets']
        if operation=='batch.edit_outline':
            from story_channel.studio import content_digest
            project=self.manager.load(pid)
            for key,value in {'title':project.title,'outline':self.manager.read_outline(pid),'duration':project.target_duration_sec}.items():
                if key not in arguments:values[key]=value
            values['expected']=content_digest(pipeline,'outline')
        if operation=='visual.update' and values.get('stage') and values.get('subject'):
            for key,value in objects['visual'].get(values['stage'],values['subject'],assets.load()).items():
                if key not in arguments:values[key]=value
        if operation=='batch.asset_provider' and 'demo' not in arguments:values['demo']=assets.demo
        try:cast=pipeline.artifact('characters').characters
        except (OSError,ValueError):cast=[]
        try:board=pipeline.artifact('storyboard').scenes
        except (OSError,ValueError):board=[]
        try:backgrounds=locations(pipeline)
        except (OSError,ValueError):backgrounds=[]
        choices={'character_id':[c.character_id for c in cast],'cid':[c.character_id for c in cast],
            'scene_id':[s.scene_id for s in board],'sid':[s.scene_id for s in board],
            'lid':[l.location_id for l in backgrounds],'location_id':[l.location_id for l in backgrounds],
            'stage':list(ASSET_STAGES),'step':list(STEPS),'decision':['apply','revert'],'mode':['text','image'],
            'gate':['characters','backgrounds','assets'] if operation.startswith('assets.') else ['world','outline','story','script','storyboard','prompts']}
        if operation.startswith(('prop_','library.')):
            props=objects['library'].load().get('props',{})
            choices['pid']=list(props)
            if operation.startswith('prop_') and 'pid' in params and not values.get('pid') and props:values['pid']=next(iter(props))
            prop=props.get(values.get('pid') or values.get('identity'),{})
            choices['mode']=prop.get('modes',[])
            if operation=='prop_drafts.save':
                values['expected']=input_hash(prop or None)
                for field in ('title','description','modes'):values.setdefault(field,prop.get(field))
        for name,options in choices.items():
            if name in params and name not in values and options:values[name]=options[0]
        if 'options' in params:values.setdefault('options',plain(objects['render'].load().options))
        if operation=='studio.approve_content':
            from story_channel.studio import content_digest
            values.setdefault('name','outline')
            choices['name']=['outline','script','prompts']
            values['expected']=content_digest(pipeline,values['name'])
        if 'subject' in params:
            stage=values.get('stage','')
            choices['subject']=[task.subject for task in assets.tasks(assets.load()) if task.stage==stage] if stage else []
            if choices['subject'] and not values.get('subject'):values['subject']=choices['subject'][0]
        sid=values.get('sid') or values.get('scene_id')
        if operation.startswith('scene.') and sid:
            if 'expected' in params:
                values['expected']=input_hash(objects['scene'].visuals().get(sid,{})) if operation.split('.')[1] in ('modify_region','extend_visual','apply_revision') else objects['scene'].direction_hash(sid)
            if 'expected_input_hash' in params:values['expected_input_hash']=objects['scene'].visual_hash(sid,objects['render'].load().options)
        if operation.startswith('world.') and 'expected' in params:values['expected']=input_hash(objects['world'].load().base_text)
        if operation=='world.edit_settings':values['settings']=plain(objects['world'].load().settings)
        if operation=='identity.save':
            library=objects['identity'].load();values.update(library=plain(library),expected=library.model_dump_json(by_alias=True))
        if operation=='finishing.configure':
            plan=objects['finishing'].load().plan;values.update(plan=plain(plan),expected=plan.model_dump_json(by_alias=True))
        if operation.startswith(('storyboard_editor.','storyboard_split.','storyboard_regeneration.','storyboard_scene_insertion.','storyboard_scene_removal.')):
            file=pipeline.path/MODULES['storyboard'].filename
            if file.is_file():
                if 'expected' in params:values['expected']=file.read_text(encoding='utf-8-sig')
                if 'data' in params:
                    data=json.loads(file.read_text(encoding='utf-8-sig'))
                    values.setdefault('data',data)
        if operation=='pipeline.edit_character' and values.get('character_id'):
            item=next(c for c in cast if c.character_id==values['character_id'])
            values['expected']=item.model_dump_json(by_alias=True);values.setdefault('text',values['expected'])
        if operation.startswith('animation.') and 'expected' in params:
            plan=objects['animation'].load(required=True).plan
            values['expected']=plan.model_dump_json(by_alias=True)
            choices['shot_id']=[shot.shot_id for shot in plan.shots]
        if operation.startswith('finishing.') and 'expected' in params:
            values['expected']=objects['finishing'].load().plan.model_dump_json(by_alias=True)
        if operation=='scene.save_direction' and sid:
            values.setdefault('text',objects['scene'].direction(sid).model_dump_json(by_alias=True,indent=2))
        if operation.startswith('prop_') and 'expected' in params and values.get('pid'):
            from story_channel.prop_removal import snapshot
            values['expected']=snapshot(objects['library'].load(),values['pid'],values.get('mode'))
        if operation=='background.edit_description' and values.get('subject'):
            location=next((item for item in backgrounds if item.location_id==values['subject']),None)
            if location:
                values['expected']=location.description
                values.setdefault('description',location.description)
        if operation.startswith('asset_sources.') and 'expected' in params and values.get('subject'):
            from story_channel.asset_sources import read_source
            source=read_source(pipeline,values['stage'],values['subject'])
            values['expected']=source.model_dump_json(by_alias=True)
            values.setdefault('text',values['expected'])
        if operation=='location_pairs.approve' and values.get('lid'):
            from story_channel.location_pairs import current
            values.setdefault('group','masters')
            records=current(assets,assets.load(),values['lid'],values['group'])
            values['expected']={name:record.asset.asset_id for name,record in (records or {}).items()}
        if operation=='characters.remove' and values.get('character_id'):
            values['expected']=next(c for c in cast if c.character_id==values['character_id']).model_dump_json(by_alias=True)
        editor={}
        if operation=='scene.modify_region' and sid:
            from story_channel.scene_video_region import playback_preview,preview
            record=objects['scene'].visuals()[sid]
            proxy,size,duration=playback_preview(objects['scene'],sid,record)
            _,poster,_=preview(objects['scene'],sid,record)
            editor={'video':proxy.relative_to(assets.path).as_posix(),'poster':poster.relative_to(assets.path).as_posix(),
                'duration':duration,'width':size[0],'height':size[1]}
            values.update(start_time=0,end_time=duration)
        if operation.startswith(('background.','location_layout.','location_details.','batch.location_layout')):
            lid=values.get('lid') or values.get('subject')
            spec=assets.load().location_reference_sets.get(lid,{})
            for name in ('floor_plan','object_layout','camera_plan','reference_type','generation_method'):
                if name in params and name not in arguments:values[name]=spec.get(name,values.get(name))
            if 'expected_layout' in params:
                from story_channel.floor_plan_revisions import signature
                values['expected_layout']=signature(spec)
            if operation.startswith('location_details.'):
                choices['identity']=list(spec.get('detail_references',{}))
                if operation!='location_details.add_draft' and choices['identity']:
                    values.setdefault('identity',choices['identity'][0])
                    reference=spec['detail_references'][values['identity']]
                    for name in ('area','description'):
                        if name in params and name not in arguments:values[name]=reference.get(name,'')
                    from story_channel.location_details import saved_camera
                    setup=saved_camera(assets,lid,values['identity'])
                    if operation=='location_details.generate' and setup:values['camera_setup']=setup
                    if operation=='location_details.save_camera':
                        from story_channel.detail_camera import geometry
                        from story_channel.floor_plan_guides import current_guides
                        plan,_,geometry_data=geometry(spec)
                        values['setup']=setup or {'position':{'x':sum(p.x for p in plan.boundary)/len(plan.boundary),'y':sum(p.y for p in plan.boundary)/len(plan.boundary)},'yaw':0,'heightMeters':plan.camera_height_m,'pitch':0}
                        editor={**geometry_data,'setup':values['setup'],'roomHeight':plan.room_height_m}
                        guides=current_guides(assets,lid,spec)
                        if guides:editor['image']=guides['floorPlan']
        definition=next(row for row in self.catalog(pid) if row['operation']==operation)
        return {'definition':definition,'values':values,'choices':choices,'editor':editor}

    def decode_argument(self,value,name=''):
        if isinstance(value,dict) and set(value)=={'upload'}:
            token=value['upload']
            if not isinstance(token,str) or Path(token).name!=token:raise ValueError('Invalid upload token')
            file=(storage_paths().uploads/token).resolve()
            if not file.is_relative_to(storage_paths().uploads.resolve()) or not file.is_file():raise ValueError('Upload not found')
            return file.read_bytes() if name in ('data','image_data','reference_data','mask','mask_data') else file
        if isinstance(value,list):return [self.decode_argument(item,name) for item in value]
        return value

    def projects(self):
        from story_channel.episode_status import episode_status
        from story_channel.animation_episode_status import animation_episode_status
        result=[]
        for item in self.manager.list_projects():
            row=plain(item)
            if item.world_project_id:
                row.update(storyStatus=episode_status(self.manager,item),animationStatus=animation_episode_status(self.manager,item))
            result.append(row)
        return result

    def create_world(self,request):
        from story_channel.studio import WorldStudio
        from story_channel.worldview import WorldSettings
        return plain(WorldStudio.create(self.manager,WorldSettings(name=request.name,premise=request.premise),default_language=request.language))

    def snapshot(self,pid,page):
        from story_channel.modules import MODULES
        from story_channel.file_cache import read_json
        from story_channel.assets import AssetPipeline
        manifest=self.manager.load(pid)
        path=self.manager.project_path(pid)
        mapping={
            'world':['world_setting.json'],
            'assets':['characters.json','background_library.json','library_media.json','production.json','identity_library.json','sfx_library.json','bgm_library.json','style_check.json'],
            'story':['episode_outline.json','outline.json',*[MODULES[name].filename for name in ('story','script','storyboard','prompts')]],
            'animations':['scenes.json','animation.json','visual_scenes.json','scene_image_keyframes.json','production.json','finishing.json','animation_continuity_review.json'],
            'final':['render.json','finishing.json','production.json'],
            'analytics':['manifest.json','usage.json'],
        }
        if page not in mapping:raise ValueError('Unknown workspace page')
        sections=[]
        for name in mapping[page]:
            file=path/name
            if file.is_file():
                data=read_json(file) if file.suffix=='.json' else file.read_text(encoding='utf-8-sig')
                sections.append({'id':name,'title':name.replace('_',' ').rsplit('.',1)[0].title(),'data':data,'version':input_hash(data)})
        engine=AssetPipeline(self.manager,pid)
        media=[]
        seen=set()
        def walk(value,label=''):
            if isinstance(value,dict):
                for key,item in value.items():
                    if key in ('file','filePath','image','audio','video','portrait','referenceImage','endFrame','previewFile') and isinstance(item,str) and item:
                        try:
                            file=engine.local_path(item)
                            if file.is_file() and file.suffix.lower() in ('.png','.jpg','.jpeg','.webp','.wav','.mp3','.mp4','.mov','.webm','.ogg') and str(file) not in seen:
                                seen.add(str(file))
                                media.append({'file':file.relative_to(path).as_posix(),'title':label or file.name,'kind':'image' if file.suffix.lower() in ('.png','.jpg','.jpeg','.webp') else 'audio' if file.suffix.lower() in ('.wav','.mp3','.ogg') else 'video'})
                        except (OSError,ValueError):pass
                    elif isinstance(item,(dict,list)):walk(item,(label+' · '+key).strip(' ·'))
            elif isinstance(value,list):
                for index,item in enumerate(value):walk(item,label+' '+str(index+1))
        for section in sections:walk(section['data'])
        characters=[]
        try:characters=[plain(c) for c in engine.creative.artifact('characters').characters]
        except (OSError,ValueError):pass
        computed={}
        if page=='assets':
            from story_channel.background_library import BackgroundLibrary,locations
            state=engine.load()
            tasks=engine.tasks(state)
            computed['characterStatus']={c['characterId']:{'approved':engine.character_approved(c['characterId'],state,tasks),
                'requirements':engine.character_requirements(c['characterId'],state,tasks)} for c in characters}
            library=BackgroundLibrary(engine)
            computed['backgrounds']=[{**plain(location),'approved':library.approved(location.location_id,state,tasks),
                'references':state.location_reference_sets.get(location.location_id,{})} for location in locations(engine.creative)]
        if page in ('assets','animations'):
            from story_channel.audio_assets import items
            from story_channel.library_media import LibraryMedia
            from story_channel.storyboard_asset_check import intact
            computed['audio']={kind:{key:{**plain(item),'ready':intact(path,item)} for key,item in items(self.manager,pid,kind).items()} for kind in ('SFX','BGM')}
            owner=manifest.world_project_id or pid
            computed['props']=plain(LibraryMedia(self.manager,owner).load())
            from story_channel.check_prop_assets import selected
            computed['selectedProps']={key+':'+mode:plain(selected(computed['props'],key,mode))
                for key,prop in computed['props'].get('props',{}).items() for mode in prop.get('modes',[])}
        if page=='animations':
            from story_channel.storyboard_asset_check import check
            from story_channel.rendering import RenderPipeline
            from backend.services.effects import catalog
            try:
                computed['assetCheck']=plain(check(self.manager,pid))
                from story_channel.voice_lines import signature as voice_signature
                state=engine.load()
                computed['voiceTracks']=[{'id':task.subject,'sceneId':task.scene_id,'characterId':task.speaker,'text':task.prompt,
                    'voiceId':state.voice_map.get(task.voice_id,''),'version':voice_signature(task),
                    'status':state.records[task.key].status if task.key in state.records else 'pending'}
                    for task in engine.tasks(state) if task.stage=='voices']
                renderer=RenderPipeline(self.manager,pid)
                computed['vfx']=plain(catalog(renderer,'VFX'))
                computed['sfx']=plain(catalog(renderer,'SFX'))
                from story_channel.animations_workflow import animation_snapshot
                action,rows=animation_snapshot(renderer,refresh=False)
                computed['nextAction']=plain(action);computed['stageStatus']=plain(rows)
            except (OSError,ValueError,RuntimeError) as exc:computed['message']=str(exc)
        if page=='analytics':
            from story_channel.cost_estimates import project_usage_records,estimate_usage
            records=project_usage_records(self.manager,pid)
            computed['costRecords']=[{'provider':r.provider,'model':r.model,'step':r.step,'cost':f'{value:.2f}' if (value:=estimate_usage(r,self.settings)[0]) is not None else 'Unavailable','outcome':r.outcome} for r in records]
        from story_channel.cost_estimates import project_cost_estimate
        total,_=project_cost_estimate(self.manager,pid)
        computed['projectCost']=f'{total:.2f}'
        return {'project':plain(manifest),'page':page,'sections':sections,'media':media,'characters':characters,'computed':computed,
            'operations':self.catalog(pid),'modelPreferences':self.model_preferences(pid)}

    def model_preferences(self,pid):
        from story_channel.generation_preferences import preferences,PROCESSES,MODELS
        manifest=self.manager.load(pid)
        owner=self.manager.load(manifest.world_project_id) if manifest.world_project_id else manifest
        return {'selected':preferences(self.manager,pid),'processes':PROCESSES,'models':MODELS,
            'demo':getattr(owner.provider_config.get('llm'),'provider',self.settings.llm_provider)=='demo'}

    def edit_artifact(self,pid,name,value,expected):
        from story_channel.modules import MODULES
        from story_channel.pipeline import Pipeline
        from story_channel.locking import project_lock
        path=self.manager.project_path(pid)
        file=path/name
        if name not in {module.filename for module in MODULES.values()}|{'world_setting.json','outline.json'}:
            raise ValueError('Use a domain action to edit this state; raw internal state writes are disabled.')
        baseline=file.read_text(encoding='utf-8-sig')
        current=json.loads(baseline) if file.suffix=='.json' else baseline
        if input_hash(current)!=expected:raise ValueError('This artifact changed. Refresh before saving.')
        if name=='world_setting.json':
            from story_channel.studio import WorldStudio
            from story_channel.worldview import WorldSettings
            return plain(WorldStudio(self.manager,pid).edit_settings(WorldSettings.model_validate(value.get('settings',value))))
        if name=='outline.json':
            if not isinstance(value,dict) or set(value)!={'text'} or not isinstance(value['text'],str):
                raise ValueError('Outline must contain a text field.')
            manifest=self.manager.load(pid)
            if manifest.world_project_id:
                from story_channel.studio import Episodes
                guard=input_hash({'title':manifest.title,'outline':current['text'],'duration':manifest.target_duration_sec})
                Episodes(self.manager,manifest.world_project_id).save_outline(
                    pid,manifest.title,value['text'],manifest.target_duration_sec,expected=guard)
            else:
                Pipeline(self.manager,pid).edit_bundle({'outline':value['text']},expected={'outline':current['text']})
            return {'saved':True}
        step=next((key for key,module in MODULES.items() if module.filename==name),'outline')
        Pipeline(self.manager,pid).edit_bundle({step:json.dumps(value,ensure_ascii=False) if not isinstance(value,str) else value},
            expected={step:baseline})
        return {'saved':True}
