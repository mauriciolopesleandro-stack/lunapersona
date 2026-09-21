"""Carrega workflows de /workflows/*.json e resolve placeholders com os
parametros de uma geracao especifica. Nenhum grafo fica hardcoded no backend:
adicionar um novo workflow e so adicionar um arquivo .json nessa pasta.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PLACEHOLDER_RE = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


class WorkflowNotFoundError(Exception):
    pass


class WorkflowParamError(Exception):
    pass


@dataclass
class WorkflowDefinition:
    id: str
    title: str
    description: str
    compatible_models: list[str]
    required_params: list[str]
    optional_params: dict[str, Any]
    graph_template: dict[str, Any]


class WorkflowManager:
    def __init__(self, workflows_dir: Path) -> None:
        self.workflows_dir = workflows_dir

    def _iter_files(self):
        if not self.workflows_dir.exists():
            return []
        return sorted(self.workflows_dir.glob("*.json"))

    def list_workflows(self) -> list[WorkflowDefinition]:
        return [self._load(path) for path in self._iter_files()]

    def get_workflow(self, workflow_id: str) -> WorkflowDefinition:
        for path in self._iter_files():
            wf = self._load(path)
            if wf.id == workflow_id:
                return wf
        raise WorkflowNotFoundError(f"Workflow '{workflow_id}' nao encontrado.")

    def _load(self, path: Path) -> WorkflowDefinition:
        data = json.loads(path.read_text(encoding="utf-8"))
        meta = data.get("meta", {})
        return WorkflowDefinition(
            id=meta.get("id", path.stem),
            title=meta.get("title", path.stem),
            description=meta.get("description", ""),
            compatible_models=meta.get("compatible_models", []),
            required_params=meta.get("required_params", []),
            optional_params=meta.get("optional_params", {}),
            graph_template=data.get("graph", {}),
        )

    def render(self, workflow_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Retorna o grafo pronto para envio ao ComfyUI, com placeholders substituidos."""
        wf = self.get_workflow(workflow_id)

        missing = [p for p in wf.required_params if p not in params]
        if missing:
            raise WorkflowParamError(
                f"Parametros obrigatorios ausentes para o workflow '{workflow_id}': {missing}"
            )

        merged = {**wf.optional_params, **params}
        graph = copy.deepcopy(wf.graph_template)
        self._substitute(graph, merged, workflow_id)
        return graph

    def _substitute(self, node: Any, params: dict[str, Any], workflow_id: str) -> Any:
        if isinstance(node, dict):
            return {k: self._substitute(v, params, workflow_id) for k, v in node.items()}
        if isinstance(node, list):
            return [self._substitute(v, params, workflow_id) for v in node]
        if isinstance(node, str):
            match = _PLACEHOLDER_RE.match(node)
            if match:
                key = match.group(1)
                if key not in params:
                    raise WorkflowParamError(
                        f"Placeholder '${{{key}}}' nao resolvido no workflow "
                        f"'{workflow_id}' (nenhum valor fornecido)."
                    )
                return params[key]
            return node
        return node
