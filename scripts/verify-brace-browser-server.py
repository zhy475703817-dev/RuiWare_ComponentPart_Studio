"""Isolated real-CAD backend for the column-brace browser acceptance script.

Only publication loading is replaced with checked-in, fixed fixtures. Drafts,
MCP, qualification, CAD workers, static UI and SQLite use production code.
"""
import argparse
import copy
import json
from pathlib import Path
import sys

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import reconstruction
from backend.app.config import Settings
from backend.app.library import LibraryError, PublishedTemplate
from backend.app.main import create_app


def fixture_source():
    fixture = json.loads((ROOT / 'backend/tests/fixtures/geometry-resize-v1.json').read_text(encoding='utf-8'))
    source = copy.deepcopy(fixture['components']['upright'])
    source['definition'].update(name='柱片撑杆浏览器验收', code='BRACEQA')
    source['assembly']['members'] = [m for m in source['assembly']['members']
                                      if m['id'] in {'upright-left', 'upright-right', 'cross-1', 'diagonal-1'}]
    member_ids = {m['id'] for m in source['assembly']['members']}
    source['bindings'] = [binding for binding in source['bindings'] if binding['memberId'] in member_ids]
    # Keep genuine static exports. High, fixed main-hole ports in the original
    # assembly deliberately cannot survive the shortened test height.
    source['ports'] = [port for port in source['ports']
                       if port['source']['memberId'] in member_ids
                       and port['source']['interfaceId'] == 'upright.basePlane']
    source['revision'] = 1
    source['parameters'].extend([
        {'id': name, 'label': name, 'value': value, 'unit': 'mm', 'min': value, 'max': value}
        for name, value in [('bracePitch', 75), ('diagonalPitch', 75), ('braceBottom', 100), ('braceTop', 200)]
    ])
    rule = {
        'id': 'cad-upright-braces', 'label': '真实孔位固定规格撑杆',
        'heightParameterId': 'frameElevationZ', 'pitchParameterId': 'bracePitch',
        'bottomOffsetParameterId': 'braceBottom', 'topClearanceParameterId': 'braceTop',
        'leftColumnMemberId': 'upright-left', 'rightColumnMemberId': 'upright-right',
        'horizontalMemberId': 'cross-1', 'diagonalMemberId': 'diagonal-1',
        'horizontalLeftInterfaceId': 'crossBrace.leftNode.part_face_front.001',
        'horizontalRightInterfaceId': 'crossBrace.rightNode.part_face_front.001',
        'diagonalLeftInterfaceId': 'diagonalBrace.startNode.part_face_front.001',
        'diagonalRightInterfaceId': 'diagonalBrace.endNode.part_face_front.001',
        'leftColumnInterfacePattern': 'upright.sideAttachmentConnections.part_face_left.{index:03d}',
        'rightColumnInterfacePattern': 'upright.sideAttachmentConnections.part_face_right.{index:03d}',
        'diagonalLeftColumnInterfacePattern': 'upright.sideAttachmentConnections.part_face_left.{index:03d}',
        'diagonalRightColumnInterfacePattern': 'upright.sideAttachmentConnections.part_face_right.{index:03d}',
        'diagonalPitchParameterId': 'diagonalPitch', 'columnInterfaceIndexStart': 1,
        'columnInterfaceIndexStep': 2, 'diagonalColumnInterfaceIndexStep': 7,
        'remainderPolicy': 'reject', 'connectionMode': 'geometry',
    }
    return fixture, source, rule


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8049)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixture, source, rule = fixture_source()
    templates = {}
    for name, item in fixture['templates'].items():
        ref = item['reference']
        publication = PublishedTemplate(**ref, code=item['draft']['code'], name=name,
            createdAt='2026-11-19T00:00:00Z', size=0, downloadUrl='/fixture-unused')
        templates[(ref['publicationId'], ref['sha256'])] = publication, item['draft'], item['material']

    async def read_template(request, publication_id, expected_hash, **_kwargs):
        item = templates.get((publication_id, expected_hash))
        if item is None:
            raise LibraryError(404, 'REMOTE_TEMPLATE_NOT_FOUND')
        return item

    # The catalog is empty because this test exercises existing member references;
    # reconstruction below still uses actual fixture CAD for those references.
    def upstream(request):
        if request.url.path == '/api/public/v1/templates':
            return httpx.Response(200, json={'items': [], 'count': 0})
        return httpx.Response(404, json={'error': {'code': 'REMOTE_TEMPLATE_NOT_FOUND'}})

    reconstruction._read_template = read_template
    base = f'http://127.0.0.1:{args.port}'
    app = create_app(Settings(draft_db=output/'drafts.sqlite3', api_key='local-fixture', public_base_url=base),
                     transport=httpx.MockTransport(upstream))
    (output/'fixture-input.json').write_text(json.dumps({'base': base, 'document': source, 'rule': rule}, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'base': base, 'output': str(output)}), flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
