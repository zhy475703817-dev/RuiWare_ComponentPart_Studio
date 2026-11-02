import hashlib
import json
from types import SimpleNamespace
import unittest

from backend.app.config import MinioSettings
from backend.app.library import LibraryError, PublishedTemplate
from backend.app.published_storage import PublishedStorage, StoredPublication


class VersionedMemoryMinio:
    def __init__(self, objects):
        self.objects = {("templates", key): value for key, value in objects.items()}
        self.deleted = []
        self.fail_delete = False
        self.extra_versions = {}

    def get_object(self, bucket, key):
        from io import BytesIO
        response = BytesIO(self.objects[bucket, key])
        response.release_conn = lambda: None
        return response

    def list_objects(self, bucket, prefix, recursive, include_version=False):
        result = []
        for object_bucket, key in self.objects:
            if object_bucket != bucket or not key.startswith(prefix):
                continue
            result.extend(SimpleNamespace(object_name=key, version_id=version, is_delete_marker=False)
                         for version in self.extra_versions.get((bucket, key), [None]))
        return result

    def remove_object(self, bucket, key, version_id=None):
        if self.fail_delete:
            error = PermissionError("private minio endpoint")
            error.code = "AccessDenied"
            raise error
        self.deleted.append((key, version_id))
        versions = self.extra_versions.get((bucket, key))
        if version_id is not None and versions:
            versions.remove(version_id)
            if not versions:
                self.extra_versions.pop((bucket, key), None)
                self.objects.pop((bucket, key), None)
        else:
            self.objects.pop((bucket, key), None)


def seed(template_kind="part", package=b"package-bytes", object_key="publishers/account/pub-1/template.rwpart"):
    metadata = {
        "templateKind": template_kind,
        "publicationId": "pub-1",
        "templateId": "template-1",
        "version": 1,
        "code": "BEAM-1",
        "name": "横梁",
        "createdAt": "2026-01-01T00:00:00Z",
        "sha256": hashlib.sha256(package).hexdigest(),
        "size": len(package),
        "objectKey": object_key,
    }
    return package, metadata


class PublishedStorageDeletionTests(unittest.TestCase):
    def storage(self, kind="part", package=b"package-bytes", object_key="publishers/account/pub-1/template.rwpart"):
        package, metadata = seed(kind, package, object_key)
        metadata_key = object_key.removesuffix("template.rwpart") + "metadata.json"
        client = VersionedMemoryMinio({object_key: package, metadata_key: json.dumps(metadata).encode()})
        settings = MinioSettings(endpoint="minio.test", access_key="a", secret_key="b", bucket="templates",
                                 prefix="publishers/account", read_prefix="publishers")
        return PublishedStorage(settings, client), client

    def test_delete_part_removes_only_verified_package_and_metadata_pair(self):
        storage, client = self.storage()
        record = storage.record("part", "pub-1")

        storage.delete_part(record, expected_sha256=record.publication.sha256)

        self.assertEqual({key for key, _ in client.deleted}, {
            "publishers/account/pub-1/template.rwpart", "publishers/account/pub-1/metadata.json"})
        self.assertFalse(client.objects)

    def test_delete_refuses_component_package(self):
        storage, client = self.storage("component")
        record = storage.record("component", "pub-1")

        with self.assertRaises(LibraryError) as raised:
            storage.delete_part(record, expected_sha256=record.publication.sha256)

        self.assertEqual(raised.exception.code, "CLEANUP_KIND_NOT_ALLOWED")
        self.assertFalse(client.deleted)

    def test_delete_refuses_package_changed_after_scan(self):
        storage, client = self.storage()
        record = storage.record("part", "pub-1")
        client.objects["templates", record.object_key] = b"changed"

        with self.assertRaises(LibraryError):
            storage.delete_part(record, expected_sha256=record.publication.sha256)

        self.assertFalse(client.deleted)

    def test_delete_refuses_key_outside_configured_publish_prefix(self):
        storage, client = self.storage(object_key="reference-registry/pub-1/template.rwpart")
        package, metadata = seed(object_key="reference-registry/pub-1/template.rwpart")
        publication = PublishedTemplate.model_validate({**metadata, "downloadUrl": "/download"})
        record = StoredPublication(publication, "reference-registry/pub-1/template.rwpart")

        with self.assertRaises(LibraryError):
            storage.delete_part(record, expected_sha256=record.publication.sha256)

        self.assertFalse(client.deleted)

    def test_delete_removes_every_object_version(self):
        storage, client = self.storage()
        record = storage.record("part", "pub-1")
        for key in list(client.objects):
            client.extra_versions[key] = ["latest", "older", "delete-marker"]

        storage.delete_part(record, expected_sha256=record.publication.sha256)

        self.assertEqual(len(client.deleted), 6)
        self.assertFalse(client.objects)

    def test_access_denied_is_reported_without_leaking_minio_error(self):
        storage, client = self.storage()
        record = storage.record("part", "pub-1")
        client.fail_delete = True

        with self.assertRaises(LibraryError) as raised:
            storage.delete_part(record, expected_sha256=record.publication.sha256)

        self.assertEqual(raised.exception.code, "DELETE_PERMISSION_DENIED")
        self.assertNotIn("private", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
