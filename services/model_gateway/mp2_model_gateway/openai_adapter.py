"""Optional external adapter contract; this module does not import a provider SDK."""

from mp2_schemas.gateway import ModelGatewayRequest, ModelGatewayResponse


class OpenAICompatibleAdapter:
    def __init__(self, *, enabled: bool = False):
        self.enabled = enabled

    async def execute(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
        if not self.enabled:
            raise PermissionError(
                "OpenAI adapter requires explicit policy approval and credentials"
            )
        raise NotImplementedError(
            "wire an approved HTTP transport without leaking provider objects"
        )
