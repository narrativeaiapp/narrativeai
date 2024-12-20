from datetime import datetime
from pathlib import Path
from upload2oss import up2oss
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict

from storygenv1.story.story_writer import generate_story,extend_by_last_node
from storygenv1.plan.plan_writer import plan_extend_by_plot
from storygenv1.plan.plan import Plan
from storygenv1.common.llm.llm import *
from storygenv1.common.config import Config
from storygenv1.common.llm.prompt import load_prompts

class story_gen():
    def __init__(self, out_dir = '/data/git/storygen-v1/outputs', assets_dir = '/data/git/storygen-v1/assets/story'):
        self.out_dir = Path(out_dir)
        self.assets_dir = Path(assets_dir)
        self.UP2Cloud = up2oss()
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.task_status = OrderedDict()
        self.config = Config.load(self.assets_dir, ["defaults"])
        init_logging(self.config.logging_level)
        self.llm_client = LLMClient()
    
    def story_generation(self, job_id):
        plan_file = self.out_dir.absolute() / (str(job_id) + "_plan.json")
        plan = Plan.load(plan_file)
        prompts = load_prompts(self.assets_dir, file_name ='prompts.json')

        story = generate_story(
            plan, 
            self.config['model']['story'], 
            prompts['story'], 
            self.llm_client
        )[0]

        story_file = self.out_dir.absolute() / (str(job_id) + "_story.txt")
        story.save(story_file)
        oss_file_url = self.UP2Cloud.upload_file(story_file)
        self.task_status[job_id] = {
        "job_id": job_id,
        "status": "success",
            "gen_type": "story",
            "results": {
                "story": oss_file_url
            }
        }
        logging.info(f'Generated story, job_id: {job_id} {oss_file_url}')


    def story_extend_by_plot(self, job_id, extend_id):
        print(f"start story_extend_by_plot, job_id: {job_id}, extend_id: {extend_id}")
        prompts = load_prompts(self.assets_dir, file_name ='prompts.json')

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        plan_file = self.out_dir.absolute() / (str(job_id) + "_plan.json")
        plan = Plan.load(plan_file)
        story_extended = extend_by_last_node(plan, self.config['model']['story'], prompts['story'], self.llm_client)[0]
        file_path = self.out_dir.absolute() / f"{job_id}_story_extended_{timestamp}.txt"
        story_extended.save(file_path)
        oss_file_url = self.UP2Cloud.upload_file(file_path)
        self.task_status[job_id] = {
        "job_id": job_id,
        "extend_id": extend_id,
        "status": "success",
        "gen_type": "extend_story",
        "results": {
                "story": oss_file_url
            }
        }
        logging.info(f'Generated story extended, job_id: {job_id}, {oss_file_url}')

    def generation_queue(self, job_id, gen_type, extend_id=None):
        current_time = datetime.now()
        time_stamp = current_time.strftime('%Y-%m-%d %H:%M:%S')
        print(f"story generation queue: {time_stamp} - {job_id} - {gen_type}")
        if gen_type == 'story':
            self.executor.submit(self.story_generation,job_id)
            #self.story_generation(job_id)
        elif gen_type == 'extend_story':
            self.executor.submit(self.story_extend_by_plot, job_id, extend_id)
            #self.story_extend_by_plot(job_id)