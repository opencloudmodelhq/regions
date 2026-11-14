# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0


def test_un_locode_is_recorded_under_the_un_cefact_licence_statement():
    from ocm_regions.fetch.sources import SOURCES

    note = next(s.license_note for s in SOURCES if s.name == "un-locode")
    assert "CC BY 4.0" in note and "UN/CEFACT" in note


def test_a_clean_checkout_gets_its_output_folders_before_any_command_runs(tmp_path):
    from ocm_regions.cli import ensure_folders

    ensure_folders(tmp_path)
    assert (tmp_path / "output").is_dir() and (tmp_path / "paper" / "generated").is_dir()


def test_the_build_reads_its_inputs_from_the_grouped_folders(tmp_path):
    from ocm_regions.common.layout import datasets, documents, operators, overturned, reference, register

    assert (
        datasets(tmp_path) == tmp_path / "sources" / "datasets.csv"
        and documents(tmp_path) == tmp_path / "sources" / "documents.csv"
    )
    assert operators(tmp_path) == tmp_path / "config" / "operators.csv"
    assert (
        reference(tmp_path) == tmp_path / "evaluation" / "reference_placements.csv"
        and overturned(tmp_path) == tmp_path / "evaluation" / "overturned_placements.csv"
    )
    assert register(tmp_path, "nearby_hub") == tmp_path / "registers" / "codes_nearby_hub.csv"
