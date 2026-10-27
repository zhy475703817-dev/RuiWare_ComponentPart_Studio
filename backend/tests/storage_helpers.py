from io import BytesIO
from types import SimpleNamespace


class ObjectResponse(BytesIO):
    def release_conn(self):
        pass


class MemoryMinio:
    """Only the external S3 boundary is replaced; publication logic stays real."""
    def __init__(self):
        self.objects = {}
        self.fail_metadata = False
        self.fail_get = set()
        self.fail_list = False
        self.writes = []
        self.reads = []

    def put_object(self, bucket_name, object_name, data, length, **kwargs):
        if self.fail_metadata and object_name.endswith('/metadata.json'):
            raise ConnectionError('private connection details must not reach the user')
        content = data.read()
        assert len(content) == length
        self.objects[bucket_name, object_name] = content
        self.writes.append(object_name)

    def get_object(self, bucket_name, object_name):
        self.reads.append(object_name)
        if object_name in self.fail_get:
            raise ConnectionError('private connection details must not reach the user')
        return ObjectResponse(self.objects[bucket_name, object_name])

    def list_objects(self, bucket_name, prefix, recursive):
        if self.fail_list:
            raise ConnectionError('private connection details must not reach the user')
        return [SimpleNamespace(object_name=key) for bucket, key in self.objects if bucket == bucket_name and key.startswith(prefix)]


STORAGE_ENV = {
    'RUIWARE_MINIO_ENDPOINT': '127.0.0.1:19000',
    'RUIWARE_MINIO_ACCESS_KEY': 'test-access', 'RUIWARE_MINIO_SECRET_KEY': 'test-secret',
    'RUIWARE_MINIO_BUCKET': 'shared-templates', 'RUIWARE_MINIO_PREFIX': 'publishers/test-user',
    'RUIWARE_MINIO_READ_PREFIX': 'publishers', 'RUIWARE_MINIO_SECURE': 'false',
}
