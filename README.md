# GCC-Automation-Tool
Dev platform for the GCC automation tool. 


![GCC Automation Tool Overview](https://github.com/kaikamp/GCC-Automation-Tool/blob/main/GCC%20Automation%20Tool%20Overview.png?raw=true)


# Modules Description

## Main
*Start the following modules, entry point for users and GUI*

Inputs:
- PowerFactory Project Name ... 
- folder for results
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
- config file for simulations

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
