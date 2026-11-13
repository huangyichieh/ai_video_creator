"""UI batch commands delegate to the same production services as the CLI."""
class BatchService:
    def __init__(self,manager,project_id):
        self.manager,self.project_id=manager,project_id
        self.stop_requested=lambda:False
    def asset_provider(self,demo=False):
        from story_channel.assets import AssetPipeline
        return AssetPipeline(self.manager,self.project_id,demo=demo).inspect()
    def edit_outline(self,title,outline,duration,*,expected):
        from story_channel.studio import Episodes
        project=self.manager.load(self.project_id)
        if not project.world_project_id:raise ValueError('Select an episode first.')
        return Episodes(self.manager,project.world_project_id).save_outline(self.project_id,title,outline,duration,expected=expected)
    def remove_episode(self,confirmation):
        from story_channel.studio import Episodes
        project=self.manager.load(self.project_id)
        if not project.world_project_id:raise ValueError('Select an episode first.')
        return Episodes(self.manager,project.world_project_id).remove(self.project_id,confirmation=confirmation)
    def animated_scenes(self,force=False):
        from story_channel.scene_production import SceneProduction
        from story_channel.rendering import RenderPipeline
        engine=SceneProduction(self.manager,self.project_id)
        engine.stop_requested=self.stop_requested
        options=RenderPipeline(self.manager,self.project_id).load().options
        result=[]
        for scene in sorted(engine.assets.creative.artifact('storyboard').scenes,key=lambda item:item.order):
            if self.stop_requested():break
            result.append(engine.generate_visual(scene.scene_id,options,force=force))
        return result
    def next_animation_stage(self,pipeline=False):
        from story_channel.rendering import RenderPipeline
        from story_channel.animations_workflow import generate_animations
        engine=RenderPipeline(self.manager,self.project_id)
        engine.stop_requested=engine.assets.stop_requested=self.stop_requested
        return generate_animations(engine,pipeline=pipeline)

    def review_episode(self,kind='story',instructions=''):
        from story_channel.pipeline import Pipeline
        from story_channel.review_changes import prepare_review
        pipeline=Pipeline(self.manager,self.project_id)
        if kind in ('outline','script','prompts'):
            from story_channel.episode_reviews import review_episode
            return review_episode(pipeline,kind,instructions)
        if kind=='story':pipeline.generate('review',force=True)
        elif kind=='storyboard':
            from story_channel.dialogue_review import review_dialogue
            review_dialogue(pipeline,force=True)
        else:raise ValueError('Review kind must be story or storyboard')
        return prepare_review(pipeline,kind,force=True)

    def location_layout(self,lid,*,instructions='',update='both',expected_layout=None):
        from story_channel.assets import AssetPipeline
        from story_channel.location_layout import generate_location_layout,save_layout_result
        engine=AssetPipeline(self.manager,self.project_id)
        engine.stop_requested=self.stop_requested
        spec=engine.load().location_reference_sets.get(lid,{})
        result=generate_location_layout(engine,lid,'KEY_360',spec.get('floor_plan',''),
            spec.get('object_layout',{}),camera_plan=spec.get('camera_plan'),update=update,instructions=instructions)
        save_layout_result(engine,lid,result,expected_layout=expected_layout)
        return result
