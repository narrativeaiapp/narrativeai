from datetime import datetime
from pathlib import Path
from upload2oss import up2oss
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict

from storygenv1.premise.premise import Premise
from storygenv1.premise.premise_writer import *
from storygenv1.plan.plan import Plan
from storygenv1.plan.plan_writer import generate_entities, generate_outline, generate_setting, plan_list_extend_plots,plan_extend_by_plot
from storygenv1.common.llm.llm import *
from storygenv1.common.config import Config
from storygenv1.common.llm.prompt import load_prompts

class plan_gen():
    def __init__(self, out_dir = '/data/git/storygen-v1/outputs', assets_dir = '/data/git/storygen-v1/assets/plan'):
        self.out_dir = Path(out_dir)
        self.assets_dir = Path(assets_dir)
        self.UP2Cloud = up2oss()
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.task_status = OrderedDict()
        self.config = Config.load(self.assets_dir, ["defaults"])
        init_logging(self.config.logging_level)
        self.llm_client = LLMClient()

    def plan_generation(self, job_id):
        premise_file = self.out_dir.absolute() / (str(job_id) + "_premise.json")
        premise = Premise.load(premise_file)
        prompts = load_prompts(self.assets_dir, file_name ='prompts.json')
        plan = Plan(premise)
        generate_setting(plan, self.llm_client, prompts['setting'], self.config['model']['setting'])

        success = False
        for i in range(self.config['model']['entity']['max_attempts']):
            try:
                generate_entities(plan, self.llm_client, prompts['entity'], self.config['model']['entity'])
                success = True
                break
            except:
                logging.warning(f'Failed to generate entities, retrying ({i+1}/{self.config["model"]["entity"]["max_attempts"]})')
        if not success:
            raise Exception('Failed to generate entities')

        success = False
        for i in range(self.config['model']['outline']['max_attempts']):
            # TODO retry mechanism could be more sophisticated if needed, e.g. beam search or MCTS, similar to how we do it in generate_story
            try:
                generate_outline(plan, self.llm_client, prompts['outline'], self.config['model']['outline'])
                success = True
                break
            except Exception as e:
                logging.warning(f'Failed to generate outline, retrying ({i+1}/{self.config["model"]["outline"]["max_attempts"]}, error: {e})')
        if not success:
            raise Exception('Failed to generate outline')
        
        if len(plan.outline.children) >= 3:
            last_node = plan.outline.children[-1]
            plan.outline.children.remove(last_node)

        save_file = self.out_dir.absolute() / (str(job_id) + "_plan.json")
        plan.save(save_file)
        oss_file_url = self.UP2Cloud.upload_file(save_file)
        self.task_status[job_id] = {
        "job_id": job_id,
        "status": "success",
            "gen_type": "plan",
            "results": {
                "plan": oss_file_url
            }
        }

        logging.info(f'Generated plan, job_id: {job_id} {oss_file_url}')

    def list_extend_plots(self, job_id):
        plan_file = self.out_dir.absolute() / (str(job_id) + "_plan.json")
        plan = Plan.load(plan_file)
        prompts = load_prompts(self.assets_dir, file_name ='prompts.json')
        plots = plan_list_extend_plots(plan, self.llm_client, prompts['outline'], self.config['model']['outline'])

        return plots
 
    def plan_extend(self, job_id, plot):
        plan_file = self.out_dir.absolute() / (str(job_id) + "_plan.json")
        plan = Plan.load(plan_file)
        prompts = load_prompts(self.assets_dir, file_name ='prompts.json')
        plan = plan_extend_by_plot(plan, self.llm_client, prompts['outline'], self.config['model']['outline'], plot)
        if not plan:
            raise Exception('Failed to extend plan')

        save_file = self.out_dir.absolute() / (str(job_id) + "_plan.json")
        plan.save(save_file)
        oss_file_url = self.UP2Cloud.upload_file(save_file)
        print(f"plan_extend by plot, job_id: {job_id}, oss_file_url: {oss_file_url}")

        return plan
       
    def generation_queue(self, job_id, gen_type):
        current_time = datetime.now()
        time_stamp = current_time.strftime('%Y-%m-%d %H:%M:%S')
        print(f"plan generation queue: {time_stamp} - {job_id} - {gen_type}")
        if gen_type == 'plan':
            self.executor.submit(self.plan_generation,job_id)
            #self.plan_generation(job_id)