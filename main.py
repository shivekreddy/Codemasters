from src.gcc_automation_tool import func_lib, requirement, project

# Example of getting a PF instance
if False:
    pf = func_lib.get_PF(False, False)
    func_lib.select_PF_project(pf)


# Example of creating a new requirement  --> should be added to a project
if False:
    req = requirement.requirement('config/lvrt_template.yaml')
    print(f'The requirement is of type {req.req_type}.', )


if True:
    proj = project.project('config/plant_template.yaml')
    print(proj.pname)
    for r in proj.requirements:
        print(r.req_type)
    