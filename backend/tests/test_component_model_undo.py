import unittest

from backend.app.components.service import apply_action, create_component_document


class ComponentModelUndoTests(unittest.TestCase):
    def test_restore_applies_optional_parameter_and_interface_type_changes(self):
        document = create_component_document()
        model = {key: value for key, value in document.model_dump(by_alias=True).items()
                 if key in ("assembly", "ports", "bindings", "patterns", "slots", "braceGenerationRules")}
        document = apply_action(document, {"type": "parameter.value", "id": "componentLength", "value": 4100})
        document = apply_action(document, {"type": "interface.types.merge", "types": [{"id": "custom:temp", "label": "temp"}]})
        legacy = apply_action(document, {"type": "model.restore", "model": model})
        self.assertEqual(legacy.parameters[0].value, 4100)
        self.assertEqual([item.id for item in legacy.interfaceTypes], ["custom:temp"])
        restored = apply_action(document, {"type": "model.restore", "model": {
            **model, "parameters": create_component_document().model_dump()["parameters"], "interfaceTypes": [],
        }})
        self.assertEqual(restored.parameters[0].value, 2400)
        self.assertEqual(restored.interfaceTypes, [])
        self.assertEqual(restored.revision, document.revision + 1)
