from httpx import AsyncClient


async def test_taxonomy_insights(client: AsyncClient):
    response = await client.get("/api/v1/insights/taxonomy")
    assert response.status_code == 200
    body = response.json()
    assert body["total_documents"] == 0
    assert body["total_concepts"] == 0
    assert body["singleton_rate"] == 0.0
    assert "confidence_buckets" in body
