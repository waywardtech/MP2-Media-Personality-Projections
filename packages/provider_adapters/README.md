# packages/provider_adapters

Adapters translating external provider responses into MP2-owned contracts.

Currently the only adapters live with the Model Gateway
(`services/model_gateway`): the llama.cpp adapter, the deterministic baseline adapter and
the disabled external adapter. This package exists for provider adapters that are not model
gateways - metadata providers, subtitle sources, rights registries.

Adapters here may import provider SDKs. **`packages/domain` and `packages/schemas` may
not**, and nothing here may leak a provider object into them.
