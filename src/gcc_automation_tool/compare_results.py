import pandas as pd
import yaml
import os
import pprint
import matplotlib.pyplot as plt

relpath = "./PF_results.csv"
simdata = pd.read_csv(relpath)
print(simdata)

#with open('./YAML_FRTs', 'r') as f:
#    ydata = yaml.load(f, Loader=yaml.SafeLoader)
#req = ydata['requirements']
req = yaml.load("""
                curve1:
                    0.0: 0.0
                    0.42: 0.0
                    0.420: 0.05
                    0.900: 0.20
                    1.550: 0.40
                    2.200: 0.60
                    3.000: 0.85
                    3.001: 1.00
                """, Loader=yaml.SafeLoader)

print(req)
plt.plot(req['curve1'].keys(), req['curve1'].values())
plt.show()