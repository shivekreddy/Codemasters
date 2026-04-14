from typing import Literal

PlantType = Literal["type_1", "type_2"]
ScopeName = Literal["common", "type_1", "type_2"]
EvalMethod = Literal["rms", "emt", "loadflow", "short-circuit"] 
BackendName = Literal["powerfactory", "pscad", "psse"]