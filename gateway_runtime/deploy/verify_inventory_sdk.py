"""Independent MCP SDK check for the signed internal inventory client."""
import asyncio
import json
from pathlib import Path
import ssl

import certifi
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from inventory_client import tokens


async def verify():
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / 'tmp/system-design/deployment.json').read_text())
    user, client = await asyncio.to_thread(tokens)
    headers = {'Authorization': 'Bearer ' + user, 'X-Kerbline-User-Token': user, 'X-Kerbline-Client-Token': client}
    async with httpx2.AsyncClient(verify=ssl.create_default_context(cafile=certifi.where()), headers=headers, timeout=60) as http:
        async with streamable_http_client(manifest['frontUrl'] + '/mcp', http_client=http) as streams:
            async with ClientSession(*streams) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                assert 'cloud_run_inventory' in [tool.name for tool in tools.tools]
                result = await session.call_tool('cloud_run_inventory', {'services': ['gateway']})
                assert not result.is_error
                content = json.loads(result.content[0].text)
                assert len(content['services']) == 1 and content['services'][0]['alias'] == 'gateway'
                assert content['modelAttempted'] is False
                report = {'passed': True, 'protocol': initialized.protocol_version, 'tool': 'cloud_run_inventory',
                          'approvedResource': content['services'][0]['resourceId'], 'modelRequestsMade': 0}
    (root / 'tmp/inventory/sdk-tests.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == '__main__':
    asyncio.run(verify())
