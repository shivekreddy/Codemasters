"""
Tests for the folder logic in gcc_common.py (section 0) and the checks'
default_folders(): one source, the project config 'paths' section.

Run:  python -m pytest "Working Scripts/tests" -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import gcc_common as gcc                                  # noqa: E402
import freq_check as fq                                   # noqa: E402
import frt_check as fc                                    # noqa: E402


def test_results_dir_layout(tmp_path):
    cfg = {"paths": {"root": str(tmp_path / "Results Export")}}
    assert gcc.results_dir(cfg, "frt_raw", "FRT_x") == str(
        tmp_path / "Results Export" / "FRT Raw Data" / "FRT_x")
    assert gcc.results_dir(cfg, "freq_plots") == str(
        tmp_path / "Results Export" / "PowerFactory Plots" / "Frequency")
    assert os.path.isdir(tmp_path / "Results Export" / "FRT Raw Data" / "FRT_x")


def test_missing_root_is_a_clear_error():
    with pytest.raises(RuntimeError, match="paths"):
        gcc.results_root({})


def test_run_tag_with_text_after_it():
    name = "FRT_2026-11-11_13-56-23 (PPC Enabled)"
    assert gcc._parse_run_tag(name, "FRT") is not None
    assert gcc.run_tag_of(name, "FRT") == "FRT_2026-11-11_13-56-23"
    assert gcc._parse_run_tag("FRT_old-copy", "FRT") is None


def test_latest_run_dir_by_timestamp(tmp_path):
    for n in ("FRT_2026-11-10_09-12-44", "FRT_2026-11-11_13-56-23 (PPC Enabled)",
              "FRT_09-12-2026_10-00-00", "FRT_notes", "FREQ_2027-01-01_00-00-00"):
        (tmp_path / n).mkdir()
    assert os.path.basename(gcc.latest_run_dir(str(tmp_path), "FRT")) == "FRT_09-12-2026_10-00-00"


def _config(tmp_path, **paths):
    root = tmp_path / "Results"
    p = {"root": str(root), "frt_run": None, "freq_run": None}
    p.update(paths)
    import yaml
    (tmp_path / "project_config_update.yaml").write_text(yaml.safe_dump({"paths": p}))
    return root


@pytest.mark.parametrize("mod,raw,res,prefix,xlsx", [
    (fq, "Freq Raw Data", "Freq Results", "FREQ", "freq_results.xlsx"),
    (fc, "FRT Raw Data", "FRT Results", "FRT", "frt_results.xlsx"),
])
def test_checks_take_latest_run_from_config(tmp_path, mod, raw, res, prefix, xlsx):
    root = _config(tmp_path)
    for n in (f"{prefix}_2026-11-10_09-12-44", f"{prefix}_2026-11-12_16-50-24"):
        (root / raw / n).mkdir(parents=True)
    script = tmp_path / "x.py"          # the config is looked up next to the script
    data, out = mod.default_folders(str(script))
    assert data == str(root / raw / f"{prefix}_2026-11-12_16-50-24")
    assert out == str(root / res / f"{prefix}_2026-11-12_16-50-24" / xlsx)


def test_checks_use_configured_run(tmp_path):
    root = _config(tmp_path, freq_run="FREQ_2026-11-10_09-12-44")
    for n in ("FREQ_2026-11-10_09-12-44", "FREQ_2026-11-12_16-50-24"):
        (root / "Freq Raw Data" / n).mkdir(parents=True)
    data, _ = fq.default_folders(str(tmp_path / "x.py"))
    assert data.endswith("FREQ_2026-11-10_09-12-44")


def test_checks_without_raw_data_say_so(tmp_path):
    _config(tmp_path)
    with pytest.raises(RuntimeError, match="gcc_export_raw_results.py"):
        fc.default_folders(str(tmp_path / "x.py"))
