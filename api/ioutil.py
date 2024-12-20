import json
from pathlib import Path

class ioutil:
    def __init__(self):
        pass

    @staticmethod
    def check_dir(dir_path):
        dir_path = Path(dir_path)

        if not dir_path.exists():
            try:
                dir_path.mkdir(parents=True)
            except Exception as e:
                raise e

    @staticmethod
    def load_json(json_path):
        with open(json_path, 'r', encoding='UTF-8') as f:
            return json.load(f)
        
    @staticmethod
    def save_json(save_path, data):
        with open(save_path, 'w', encoding='UTF-8') as file:
            json.dump(data, file, ensure_ascii=False, indent=4)
