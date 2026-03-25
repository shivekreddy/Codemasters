import yaml
from src.gcc_automation_tool import requirement

class project:
    # basic info
    def __init__(self, path_to_config: str):
        # read yaml file and fill required fields
        with open(path_to_config) as stream:
            try:
                config = yaml.safe_load(stream)
            except yaml.YAMLError as exc:
                print(exc)
        
        self.pname = config['project']['name']
        self.grid_code = config['project']['grid_code']
        self.grid_code_version = config['project']['grid_code_version']
        self.ppm_type = config['project']['ppm_type']
        self.author = config['project']['author']
        self.date = config['project']['date']
        
        self.requirements = []
        for r in config['gcc_requirements']:
            req = requirement.requirement(r['path'])
            self.requirements.append(req)
