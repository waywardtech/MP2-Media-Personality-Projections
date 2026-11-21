import pytest

from mp2_model_gateway.adapters import DisabledExternalAdapter
from mp2_schemas.gateway import ModelGatewayRequest


@pytest.mark.asyncio
async def test_external_adapter_disabled() -> None:
    request = ModelGatewayRequest(capability="scene-evidence", capability_schema_version="1",
        bounded_input={"scene": "synthetic"}, privacy_class="private", quality_tier="local",
        maximum_cost=0, determinism_requirement="preferred", allowed_provider_classes=["external"],
        output_schema_id="evidence/1", trace_id="trace")
    with pytest.raises(PermissionError):
        await DisabledExternalAdapter().execute(request)
