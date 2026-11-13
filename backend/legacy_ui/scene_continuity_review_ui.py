"""AI-only scene review; composition and manual finished-scene review live on Final."""
from threading import Event
import streamlit as st

from . import scene_continuity_review as service
from .file_cache import read_json
from .generation_ui import review_button
from .ui_tasks import busy,launch


def render(manager,project_id):
    from .rendering import RenderPipeline
    from .generation_preferences import preferences,MODELS
    engine=RenderPipeline(manager,project_id)
    model=preferences(manager,project_id)['scene_continuity']
    st.subheader('Scene Review & Continuity')
    st.caption('OpenAI / '+MODELS.get(model,model)+' · Visual review of every animated scene in storyboard order.')
    st.caption('Reviews approximately one frame per second, scene boundaries and saved keyframe times. Checks visual identity, layout, props, style, action and transitions. Audio and brief glitches between samples are not checked. Composition and finished-scene approvals are on Final.')
    problem=None
    try:
        source=service.snapshot(engine)
    except (OSError,ValueError,RuntimeError) as exc:
        source=None
        problem=str(exc)
    if problem:
        st.info(problem)
    if source:
        duration=sum(float(item['duration']) for item in source['videos'])
        extra=sum(len(source['keyframes'].get(video['sceneId'],{}).get('frames',[])) for video in source['videos'])
        estimate=service.estimate(duration,len(source['videos']),model=model,extra_frames=extra)
        st.caption(f"Rough budget: ${estimate['low']:.2f}–${estimate['high']:.2f} per review · {len(source['videos'])} scene versions · ~{duration:g}s. Longer clips, more scenes and both delivery formats cost more. Actual token usage is recorded; cached unchanged reviews/batches are reused.")
    def start():
        stop=Event()
        engine.stop_requested=stop.is_set
        launch(lambda:service.review(engine),'Reviewing scene continuity',inline=True,rerun_on_start=False,
            stop_event=stop,project_path=engine.path,resource='scene-continuity-review',service='openai')
    review_button('Review',key='animation-ai-continuity-review-'+project_id,disabled=busy() or source is None,on_click=start)
    progress(engine)
    report=service.load(engine)
    if report:
        if not service.current(engine):
            st.warning('Previous review: animated scenes or context changed. Review the current versions.')
        else:
            st.success('AI review completed for all current animated scenes. Findings are suggestions; no video was changed.')
        st.caption(str(report.get('model'))+' · '+str(report.get('checkedAt'))+f" · {report.get('frameCount',0)} sampled frames")
        st.write(report['summary'])
        issues=report.get('issues',[])
        if issues:
            st.dataframe(issues,hide_index=True,width='stretch')
        else:
            st.info('No issues reported in the sampled frames. This does not guarantee frame-perfect continuity.')
        with st.expander('Review coverage and all batch findings',expanded=False):
            for row in report.get('coverage',[]):
                st.caption(f"{row['sceneId']} · {row['format']} · {row['duration']:g}s · {len(row['sampleTimes'])} frames")
            st.json({'coverage':report.get('coverage',[]),'batchReports':report.get('batchReports',[])})


@st.fragment(run_every=1)
def progress(engine):
    path=engine.path/service.PROGRESS
    if not path.is_file():
        return
    value=read_json(path)
    st.progress(value.get('percent',0)/100,text=value.get('message',''))
    if value.get('status')=='failed':
        from .error_report_ui import render_error
        render_error(value['message'],project_path=engine.path,task_key='scene_continuity',
            key='animation-continuity-error-'+engine.project_id)
