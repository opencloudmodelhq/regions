# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

from rdflib import Graph

def validate(data_graph: Graph, *, shacl_graph: Graph | None = ..., inference: str | None = ...) -> tuple[bool, Graph, str]: ...
