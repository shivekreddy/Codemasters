# Test data for test_frt_5_compliance_check.py

Put the acceptance sample case here (not committed: `*.csv` is in .gitignore):

    tests/data/FRT 1PH 0.05 Q0/POC.csv
    tests/data/FRT 1PH 0.05 Q0/GU Terminal 1.csv
    tests/data/FRT 1PH 0.05 Q0/GU Terminal 2.csv

or point the `FRT_SAMPLE_DIR` environment variable at that folder. Without it
the acceptance tests are skipped; the synthetic tests always run.

## test_freq_4_compliance_check.py

Put the frequency samples here (not committed):

    tests/data/LFSMO_Step.csv
    tests/data/LFSMU_Step.csv

or point the `FREQ_SAMPLE_DIR` environment variable at their folder.
