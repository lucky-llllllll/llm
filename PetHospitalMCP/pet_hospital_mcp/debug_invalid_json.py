import asyncio
import httpx
from pet_hospital_mcp.server import list_pets_tool

async def main():
    def fake_request(self, method, url, **kwargs):
        return httpx.Response(200, text='not-json')
    httpx.Client.request = fake_request
    result = await list_pets_tool(page=1, pageSize=10)
    print(type(result))
    print(result)
    print(getattr(result, 'structured_content', None))

asyncio.run(main())
