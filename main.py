from src.gcc_automation_tool import logger, project
from src.gcc_automation_tool.config import config_loader
import logging


# Example of getting a PF instance
if False:
    pf = func_lib.get_PF(False, False)
    func_lib.select_PF_project(pf)


def main():
    # Initialize Logger

    logger.setup_logging(level=logging.INFO, log_file="logs/run.log")
    log = logging.getLogger(__name__)
    log.info("Initializing new Project.")

    # Load Project class via project config file
    proj_config = config_loader.ConfigLoader("./config/project_test_config.yaml", plant_type='type_2').load()
    proj = project.Project(proj_config)

    # iterate through applicable requirements and print name & scope
    for r in proj.requirements.iter_applicable():
        print(f"{r.name}: {r.scope}")
    

if __name__ == "__main__":
    main()