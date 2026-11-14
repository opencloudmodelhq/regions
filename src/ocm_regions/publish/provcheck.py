# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Check the PROV-O export with the W3C PROV library and with SHACL shapes."""

from pathlib import Path
from typing import Final

from prov.model import ProvDocument
from pyshacl import validate
from rdflib import Graph, Namespace

SH: Final = Namespace("http://www.w3.org/ns/shacl#")
SHAPES: Final = Path(__file__).with_name("provenance_shapes.ttl")


def prov_document(text: str) -> ProvDocument:
    """Parse provenance JSON-LD as a PROV document.

    Args:
        text: Provenance as JSON-LD text.

    Returns:
        The parsed document.
    """
    return ProvDocument.deserialize(content=text, format="rdf", rdf_format="json-ld")


def shape_problems(text: str) -> list[str]:
    """The SHACL violations in provenance JSON-LD; empty when it conforms.

    Args:
        text: Provenance as JSON-LD text.

    Returns:
        One message per violation.
    """
    data = Graph().parse(data=text, format="json-ld")
    conforms, report, _ = validate(data, shacl_graph=Graph().parse(SHAPES, format="turtle"), inference="none")
    if conforms:
        return []
    return sorted(
        f"{report.value(r, SH.focusNode)}: {report.value(r, SH.resultPath)}: {report.value(r, SH.resultMessage)}"
        for r in report.subjects(SH.resultSeverity, SH.Violation)
    )
