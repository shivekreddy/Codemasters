import winreg
import os
import sys
import winreg


def get_PF(headless:bool):
    found_installations = find_powerfactory_installations()
    if not found_installations:
        print("No PowerFactory installations found.")
    else:
        for version, path in found_installations:
            print(f"{version}: {path}")

    # To get the newest version:
    newest = sorted(found_installations, key=lambda x: x[0], reverse=True)[0]
    print("\nNewest installation:")
    print(newest[1])
    syspath = newest[1]+r"Python\3.10"
    print(syspath)
    sys.path.append(r"{}".format(syspath))
    import powerfactory
    try:
        pf = powerfactory.GetApplicationExt()
        if not headless:
            pf.Show()
        user = pf.GetCurrentUser()
        print(user)
        project=user.GetContents('*.IntPrj')[1]
        project.Activate()
        # ... some calculations ...
        return pf
    except powerfactory.ExitError as error:
        print(error)
        print('error.code = %d' % error.code)





def find_powerfactory_installations():
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



# need to add Python Version control

get_PF(False)