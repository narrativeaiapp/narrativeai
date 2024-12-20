import json
import logging

from storygenv1.common.util import *
from storygenv1.common.llm.llm import *
from storygenv1.premise.premise import Premise
from storygenv1.plan.setting import Setting
from storygenv1.plan.entity import *
from storygenv1.plan.outline import *

class Plan:
    @staticmethod
    def load(path):
        with open(path, 'r') as f:
            data = json.load(f)
            premise = Premise(data['premise']['title'], data['premise']['premise'])
            setting = Setting(data['setting'])
            entity_list = EntityList([Entity(entity['name'], entity['description']) for entity in data['entities']])
            outline = OutlineNode.from_dict(data['outline'])
            return Plan(premise, setting, entity_list, outline)

    def __init__(self, premise, setting=None, entity_list=None, outline=None):
        self.premise = premise
        self.setting = setting
        self.entity_list = entity_list
        self.outline = outline
    
    def __str__(self):
        return f'{self.premise}\n\nSetting: {self.setting}\n\n\n\nCharacters and Entities:\n\n{self.entity_list}\n\n\n\nOutline:\n\n{self.outline}'
    
    def save(self, path):
        with open(path, 'w') as f:
            json.dump({
                'premise': {
                    'title': self.premise.title,
                    'premise': self.premise.premise
                },
                'setting': self.setting.setting,
                'entities': [{
                    'name': entity.name,
                    'description': entity.description
                } for entity in self.entity_list],
                'outline': self.outline.to_dict()
            }, f, indent=4)
