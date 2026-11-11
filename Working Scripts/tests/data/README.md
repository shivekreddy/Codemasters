# Test data for test_frt_check.py

Put the acceptance sample case here (not committed: `*.csv` is in .gitignore):

    tests/data/FRT 1PH 0.05 Q0/POC.csv
    tests/data/FRT 1PH 0.05 Q0/GU Terminal 1.csv
    tests/data/FRT 1PH 0.05 Q0/GU Terminal 2.csv

or point the `FRT_SAMPLE_DIR` environment variable at that folder. Without it
the acceptance tests are skipped; the synthetic tests always run.
