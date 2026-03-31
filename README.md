# GCC-Automation-Tool
Dev platform for the GCC automation tool. 

**Focus on these modules first:**
- [ ] PF Model sim setup - define config files in parallel
- [ ] PF run sims
- [X] create a basic config loader
	- [ ] add additional base_config subclass for each config type
- [X] prepare requirements config files
	- [ ] Update evaluation_method for finished requirement configs (loadflow, short-circuit, rms, emt)
- [ ] Compare results with requirements
- [ ] plot results with requirements

Model preparation can be done after all requirements for the model are known (should result from the previous tasks)
Main module has low priority and can be implemented when everything else is working as intended.

> See [Documentation](https://github.com/kaikamp/GCC-Automation-Tool/tree/main/docs) for currently available modules, classes and functions.

**Some additional requests**
Please
- check if functions you need are already available
- use comments / add documentation to you functions
- if your module requires some specific information/data/xyz, please note this in [module_requirements.md](module_requirements.md). This helps when defining the config files and model preparation module.
- test your module with different use cases and configurations


![GCC Automation Tool Overview](https://github.com/kaikamp/GCC-Automation-Tool/blob/main/GCC%20Automation%20Tool%20Overview.png?raw=true)


# Modules Description

## Main
*Start the following modules, entry point for users and GUI*

Inputs:
- PowerFactory Project Name ... 
- folder for results
- config file for simulations
- ...
Outputs:

Functions:

## PF Model preparation
*Prepare and verify functionality of model (basic setup)*
*Check if all required elements are there, connected, working and named correctly.*
*If required, check signal/parameter names --> refer to required simulations: Which parameters need to be changed with Events*

Inputs: 
- config file: define elements, signals etc. to check --> can start as a blank, add things when preparing specific simulations 

Outputs:

Functions:

## PF Model sim setup
*Create the required Study Cases, Operational Scenarios, Variants and Events
Define export variables*

Inputs: 
- 

Outputs:
- message: done

Functions:
- check if cases already exist (maybe delete all existing cases)
- read config file
- set up cases (including simulations)
- prepare task automation
## PF Model run sims
*Run prepared simulations with task automation (parallel processing enabled)
Export variables to file (csv)*
Inputs:

Outputs:
- csv file of simulation results
- simulation log?
- PF output window
Functions:
- check for convergence errors/problems (if possible)
- 

## Compare results with requirements

Inputs:
- more config files (requirements)
- results csv
Outputs:
- list of yes/no (maybe including what went wrong --> comment)
Functions:


## Plot results (with requirements)
Inputs:
- results csv
- requirement configs
Outputs:
- plots
Functions:



## Common function library
*Collection of commonly used functions within the tool. All functions which are used by more than one module, can be moved here.*

Functions:
- get_PowerFactory(silent: *bool*): pf --> PowerFactory Instance
	  return PF instance with graphical interface (silent = false) or without (silent=true)
- map_elements(PathToMapper): el_lib --> library 
	  return a library of mapped element and signal names 



# Config files description

## preparation config
Example approach using YAML formatting (text file):
key:value pairs
Relevant details contained in lists, e.g. "elements" with sublists if required (e.g. "signals")

```YAML
version: 1.0
elements:
	- elm1:
		name: ...
		type: ...
		signals:
			- sig1: ...
			- sig2: ...
			- ...
	- elm2:
		name: ...
	- ...
otherdata: ...
```


## element mapper
Map model specific element names to tool specific names. E.g. fault switch (tool name) --> XQbkdl.elmxyz
This can either be a YAML file or an excel work sheet.


## sim setup config
Define list of
- Study Cases:
	- Operational Scenarios
	- Events
	- export variables


## Requirements config
List of requirement curves
```YAML
version: 1.0
requirements:
	- req1:
		title: ...
		desc: ...
		x-axis: s
		y-axis: kV
		curve:
			- [0.0]: [1.1]
			- [0.2]: [1.08]
			- ...
	- req2:
		...
```
