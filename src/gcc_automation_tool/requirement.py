import yaml

class requirement:

    def __init__(self, path_to_config:str):
        # read yaml file and fill required fields
        with open(path_to_config) as stream:
            try:
                config = yaml.safe_load(stream)
            except yaml.YAMLError as exc:
                print(exc)
        self.req_type = ""

        match config['requirement_type']:
            case 'lvrt':
                self.req_type = 'lvrt'
                self.lvrt_curve = config['requirements']['lvrt_curve']
        
            case 'hvrt':
                self.req_type = 'hvrt'
                self.hvrt_curve = config['requirements']['hvrt_curve']

            case _:
                print('Requirements config file could not be read. ')
        


