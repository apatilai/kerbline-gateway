"""Independent MCP client interoperability check against the real front door."""
import asyncio
import json
from pathlib import Path
import subprocess
import os
import ssl
import certifi
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client, create_mcp_http_client


async def verify():
    state = Path(__file__).resolve().parents[2] / 'tmp' / 'system-design'
    manifest = json.loads((state / 'deployment.json').read_text())
    token = subprocess.run(['gcloud', 'auth', 'print-identity-token'], check=True, capture_output=True, text=True).stdout.strip()
    async def inspect_shape(request):
        if os.environ.get('MCP_DEBUG_KEYS') == '1' and request.method == 'POST':
            body = json.loads(request.content)
            print('MCP method and parameter shapes:', body.get('method'), {k: type(v).__name__ for k,v in body.get('params', {}).items()})
    # Explicit CA verification avoids an installed macOS truststore recursion bug.
    async with httpx2.AsyncClient(verify=ssl.create_default_context(cafile=certifi.where()),
                                 headers={'Authorization': 'Bearer ' + token, 'X-Kerbline-User-Token': token}, timeout=60,
                                 event_hooks={'request': [inspect_shape]}) as http:
        async with streamable_http_client(manifest['frontUrl'] + '/mcp', http_client=http) as streams:
            async with ClientSession(*streams) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool('invoice_summary', {})
                assert not result.is_error
                parsed = json.loads(result.content[0].text)
                assert parsed['recordCount'] == 134
                report = {'passed': True, 'negotiatedProtocol': initialized.protocol_version,
                          'tools': [t.name for t in tools.tools], 'recordCount': parsed['recordCount']}
    (state / 'mcp-sdk-results.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == '__main__':
    asyncio.run(verify())
