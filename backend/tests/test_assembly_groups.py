import unittest
import json
import subprocess
from pathlib import Path
from pydantic import ValidationError

from backend.app.components.models import ComponentDocument, ComponentAction
from backend.app.components.service import apply_action, create_component_document, serialize_component


def member(identifier):
    return {"id": identifier, "name": identifier, "code": identifier, "role": identifier,
            "template": {"publicationId": "pub", "templateId": "part", "version": 1, "sha256": "a" * 64},
            "position": [0, 0, 0], "rotation": [0, 0, 0], "parameterValues": {}, "interfaces": []}


class AssemblyGroupTests(unittest.TestCase):
    def document(self):
        data = create_component_document().model_dump(by_alias=True)
        data["assembly"] = {"members": [member("a"), member("b")], "mates": [], "groups": [
            {"id": "inner", "name": "内部组件", "memberIds": ["a"], "parentId": "outer"},
            {"id": "outer", "name": "外部组件", "memberIds": ["a", "b"]},
        ]}
        return ComponentDocument.model_validate(data)

    def test_groups_round_trip_and_model_restore_are_atomic(self):
        original = self.document()
        model = {key: original.model_dump(by_alias=True)[key] for key in ("assembly", "ports", "bindings", "patterns", "slots")}
        removed = apply_action(original, ComponentAction.validate_python({"type": "member.remove", "id": "a"}))
        self.assertEqual(removed.model_dump(by_alias=True)["assembly"]["groups"], [{"id": "outer", "name": "外部组件", "memberIds": ["b"]}])
        restored = apply_action(removed, ComponentAction.validate_python({"type": "model.restore", "model": model}))
        self.assertEqual(restored.assembly, original.assembly)
        self.assertEqual(serialize_component(restored)["stages"]["assembly"]["groups"], model["assembly"]["groups"])
        self.assertEqual(restored.revision, original.revision + 2)

    def test_invalid_group_reference_or_cycle_is_rejected(self):
        data = self.document().model_dump(by_alias=True)
        for group in (
            {"id": "bad", "name": "坏组件", "memberIds": ["missing"]},
            {"id": "bad", "name": "坏组件", "memberIds": ["a", "a"]},
            {"id": "bad", "name": "坏组件", "memberIds": ["a"], "parentId": "bad"},
            {"id": "bad", "name": "坏组件", "memberIds": ["a"], "parentId": "missing"},
        ):
            with self.subTest(group=group), self.assertRaises(ValidationError):
                ComponentDocument.model_validate({**data, "assembly": {**data["assembly"], "groups": [group]}})

    def test_legacy_documents_keep_original_shape(self):
        document = create_component_document()
        self.assertNotIn("groups", document.model_dump(by_alias=True)["assembly"])
        self.assertNotIn("groups", serialize_component(document)["stages"]["assembly"])

    def test_frontend_materialized_publication_can_be_saved_by_backend(self):
        script = """
import { createComponentDocument } from './src/domain/component.ts';
import { materializeComponentModel } from './src/domain/assemblyGroups.ts';
const document = createComponentDocument(), publication = createComponentDocument();
const member = id => ({id,name:id,code:id,role:id,template:{publicationId:id,templateId:id,version:1,sha256:'a'.repeat(64)},position:[0,0,0],rotation:[0,0,0],parameterValues:{},interfaces:[]});
const root = member('component'); root.template.templateKind='component'; document.assembly.members=[root];
publication.assembly.members=[member('part')];
const expanded=await materializeComponentModel(document,'component',async()=>publication);
console.log(JSON.stringify({...document,...expanded.model}));
"""
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, encoding="utf-8", check=True)
        document = ComponentDocument.model_validate(json.loads(result.stdout))
        self.assertEqual(document.assembly.groups[0].id, "component")
        self.assertEqual(document.assembly.groups[0].memberIds, [document.assembly.members[0].id])
