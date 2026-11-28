import json
from pathlib import Path
import subprocess
import unittest

from backend.app.components.models import ComponentDocument
from backend.app.components.service import _infer_envelope_dimensions, apply_action


class ComponentActionContractTests(unittest.TestCase):
    def test_python_and_typescript_match_after_each_action(self):
        root = Path(__file__).resolve().parents[2]
        fixture_path = root / "tests" / "fixtures" / "component-actions-v1.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        script = """
import {readFileSync} from 'node:fs';
import {componentReducer} from './src/domain/componentReducer.ts';
import {effectiveParameterBounds} from './src/domain/parameterMetrics.ts';
const fixture = JSON.parse(readFileSync('./tests/fixtures/component-actions-v1.json', 'utf-8'));
let {document} = fixture;
const results = fixture.steps.map(({action, bounds}) => {
  document = componentReducer(document, action);
  if (!bounds) return {document};
  const parameter = document.parameters.find(item => item.id === 'componentLength');
  const {min, max} = effectiveParameterBounds(parameter, document.bindings, document.assembly.members);
  return {document, bounds: [min, max]};
});
console.log(JSON.stringify(results));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=root, capture_output=True, encoding="utf-8", check=True,
        )
        document = ComponentDocument.model_validate(fixture["document"])
        for step, expected in zip(fixture["steps"], json.loads(result.stdout), strict=True):
            action = step["action"]
            with self.subTest(action=action):
                revision = document.revision + step.get("revisionDelta", 1)
                document = apply_action(document, action)
                data = document.model_dump(by_alias=True)
                self.assertEqual(document.revision, revision)
                self.assertEqual({member.id: member.parameterValues for member in document.assembly.members}, step["values"])
                self.assertEqual(data, expected["document"])
                if "bounds" in step:
                    bounds = []
                    for value in (-100, 100):
                        probe = apply_action(document, {"type": "parameter.value", "id": "componentLength", "value": value})
                        bounds.append(next(item.value for item in probe.parameters if item.id == "componentLength"))
                    self.assertEqual(bounds, expected["bounds"])
                    self.assertEqual(bounds, step["bounds"])
                if "inferredLength" in step:
                    dimensions = _infer_envelope_dimensions(data["parameters"], data["assembly"]["members"], data["patterns"])
                    self.assertEqual(dimensions["length"], step["inferredLength"])
                if step.get("removedReferences"):
                    self.assertEqual(document.ports, [])
                    self.assertEqual(document.patterns, [])
                    self.assertEqual([binding.id for binding in document.bindings], ["offset.binding", "travel.binding"])
