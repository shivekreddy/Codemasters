from src.gcc_automation_tool import logger, config, project
import logging


# Example of getting a PF instance
if False:
    pf = func_lib.get_PF(False, False)
    func_lib.select_PF_project(pf)


def main():
    # Initialize Logger
    # Load Project class via project config file

    logger.setup_logging(level=logging.INFO, log_file="logs/run.log")
    log = logging.getLogger(__name__)
    log.info("Initializing new Project.")

    proj_config = config.ConfigLoader("./config/project_test_config.yaml", plant_type='type_2').load()
    proj = project.Project(proj_config)
    

if __name__ == "__main__":
    main()