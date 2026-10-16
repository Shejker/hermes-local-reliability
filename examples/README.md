# Reference configuration only

`config.example.yaml` records the known Ollama / Hermes settings from this local deployment. Do not overwrite a current configuration without validating it for your Hermes release; fields and defaults can change.

`guard-policy.example.json` records the v2.2 Guard defaults with automatic intervention disabled. Guard v2.2 is observe-only regardless of these two flags.

Both files are examples, **not a backup of the running user's config**. No real API keys are included. For any new installation, reassess the risk of `approvals.mode: off` before enabling local shell tools.
