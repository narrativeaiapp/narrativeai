from datetime import datetime
from pathlib import Path
from upload2oss import up2oss
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict

from storygenv1.premise.premise import Premise
from storygenv1.premise.premise_writer import *
from storygenv1.common.llm.llm import *
from storygenv1.common.config import Config
from storygenv1.common.llm.prompt import load_prompts,load_premise_prompts

class premise_gen():
    def __init__(self, out_dir = '/data/git/storygen-v1/outputs', assets_dir = '/data/git/storygen-v1/assets/premise'):
        self.out_dir = Path(out_dir)
        self.assets_dir = Path(assets_dir)
        self.UP2Cloud = up2oss()
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.task_status = OrderedDict()
        self.config = Config.load(self.assets_dir, ["defaults"])
        init_logging(self.config.logging_level)
        self.llm_client = LLMClient()

    def premise_generation(self, job_id, user_prompt):
        prompts = load_premise_prompts(self.assets_dir, file_name ='prompts.json', user_prompt=user_prompt)

        premise = Premise()
        generate_title(premise, prompts['title'], self.config['model']['title'], self.llm_client, user_prompt)
        generate_premise(premise, prompts['premise'], self.config['model']['premise'], self.llm_client, user_prompt)
        
        premise.title = premise.title.replace('"', '')
        #premise.premise = re.sub(r'^[.\s]+', '', premise.premise)

        save_file = self.out_dir.absolute() / (str(job_id) + "_premise.json")
        premise.save(save_file)
        oss_file_url = self.UP2Cloud.upload_file(save_file)
        self.task_status[job_id] = {
        "job_id": job_id,
        "status": "success",
            "gen_type": "premise",
            "results": {
                "premise": oss_file_url
            }
        }
        logging.info(f'Generated, job_id: {job_id} {oss_file_url}')

    def generation_queue(self, job_id, prompt, gen_type):
        if gen_type == 'premise':
            self.executor.submit(self.premise_generation,job_id,prompt)
            #self.premise_generation(job_id,prompt)
