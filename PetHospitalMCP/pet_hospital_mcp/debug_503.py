import asyncio
import httpx
from pet_hospital_mcp.server import list_pets_tool

async def main():
    def fake_request(self, method, url, **kwargs):
        return httpx.Response(503, json={'code': 503, 'message': 'backend down'})
    httpx.Client.request = fake_request
    result = await list_pets_tool(page=1, pageSize=10)
    print(result)
    print(result.structured_content)

asyncio.run(main())
