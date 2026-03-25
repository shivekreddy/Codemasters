import winreg
import os
import sys
import winreg
import tkinter as Tk


def get_PF(headless:bool, latest:bool):
    """
    Instantiate and return newest PowerFactory installation with the currently used Python version. 
    Prints out errors, if necessary.
    Uses additional functions.
    \nParameters
    ----------
    headless : bool
        If true, PF will be run without graphic representation
    \nReturns
    -------
    pf : PowerFactory instance
    """

    pyversion = sys.version.split(".")[0] + "." + sys.version.split(".")[1]
    print("Running script with Python version " + pyversion)
    found_installations = find_powerfactory_installations()
    if not found_installations:
        print("No PowerFactory installations found.")
    else:
        for version, path in found_installations:
            print(f"{version}: {path}")

    # To get the newest version:
    if latest:
        newest = sorted(found_installations, key=lambda x: x[0], reverse=True)[0]
        print("\nNewest installation:")
        print(newest[1])
        syspath = newest[1]+"Python\\" + pyversion
        sys.path.append(r"{}".format(syspath))      # this appends the required path to the PowerFactory module
    else:
        root = Tk.Tk()
        pf_lb = Tk.Listbox(root, height=len(found_installations), width=200, selectmode='single')
        for version, path in found_installations:
            pf_lb.insert('end',f"{version}: {path}")
        pf_lb.pack()
        Tk.Button(root, text="Select", command=lambda: _button_select_installation(pf_lb, found_installations, pyversion, root)).pack()
        Tk.mainloop()

    import powerfactory # type: ignore
    try:
        pf = powerfactory.GetApplicationExt()
        if not headless:
            pf.Show()
        user = pf.GetCurrentUser()
        print("Retrieved PowerFactory instance with user: " + str(user))
        return pf
    except powerfactory.ExitError as error:
        print(error)
        print('error.code = %d' % error.code)





def find_powerfactory_installations():
    """
    Checks the registry (needs read-only access to local user) for PowerFactory installations and returns a list of installation paths.
    \nParameters:
    ---
    \nreturns
    ---
    installations : list of paths
    """
    installations = []

    # Registry roots to search (64-bit + 32-bit Wow6432Node)
    base_paths = [
        r"SOFTWARE\DIgSILENT",
        r"SOFTWARE\Wow6432Node\DIgSILENT",
        r"SOFTWARE\DIgSILENT GmbH",
        r"SOFTWARE\Wow6432Node\DIgSILENT GmbH"
    ]

    for base in base_paths:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
                # Enumerate all subkeys (e.g. PowerFactory, PowerFactory 2022, 2023...)
                i = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(root, i)
                        i += 1

                        if "PowerFactory" not in subkey_name:
                            continue

                        full_path = base + "\\" + subkey_name

                        try:
                            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, full_path) as pfkey:
                                install_dir, _ = winreg.QueryValueEx(pfkey, "InstallDir")

                                if os.path.exists(install_dir):
                                    installations.append((subkey_name, install_dir))

                        except FileNotFoundError:
                            pass

                    except OSError:
                        break

        except FileNotFoundError:
            pass

    return installations

def get_nested_projects(parent) -> list:
    """
    Return all objects of type 'IntPrj' from given parent directory. Run recursively for all found folders 'IntFolder' and return a list of Project objects.
    \nParameters
    ----------
    parent : Object of type 'IntFolder'
    \nreturns
    -------
    projects : list of Objects (type 'IntPrj')
    """
    projects = parent.GetContents('*.IntPrj')
    folders = parent.GetContents('*.IntFolder')
    if len(folders) > 0:
        for f in folders:
            projects.extend(get_nested_projects(f))
    return projects


def select_PF_project(pf):
    """
    Display list of available PF projects. Selected Project will be activated.
    \nParameters
    ----------
    pf : PowerFactory Instance
    \nreturns
    ------
    None
    """
    user = pf.GetCurrentUser()
    projects = get_nested_projects(user)
    root = Tk.Tk()
    project_lb = Tk.Listbox(root, height=len(projects), width=200, selectmode='single')
    for p in projects:
            project_lb.insert('end',str(p))
    project_lb.pack()
    Tk.Button(root, text="Select", command=lambda: _button_activate_Project(project_lb, projects, root),).pack()
    Tk.mainloop()


def _button_activate_Project(project_lb, projects, root:Tk):
    """
    Used by Command button, do not use.
    """
    print(project_lb.curselection())
    projects[project_lb.curselection()[0]].Activate()
    root.destroy()


def _button_select_installation(pf_lb:Tk.Listbox, installations:list, pyversion:str, root:Tk):
    """
    Used by Command button, do not use.
    """
    print(installations[pf_lb.curselection()[0]])
    sys.path.append(installations[pf_lb.curselection()[0]][1] + "Python\\" + pyversion)
    root.destroy()
#add a comment