# Substrate sandboxes

Moyai supports Modal and Agent Substrate. Choose the default under **Settings → Runtime → Sandbox provider**. Connect either provider there; credentials are encrypted in Moyai's database. Changing the default affects new sessions, including Slack and automation runs. Existing sessions and child agents retain their original provider.

Substrate runs the sandboxes on your existing Kubernetes cluster. Moyai's web app can run on Render, Docker, or another always-on host with a persistent data directory. The sandbox must be able to reach Moyai's HTTPS `PUBLIC_URL` for model requests and app tools. Provider keys remain on the Moyai server.

## Prepare the cluster once

This integration targets Substrate's API at commit `92b74c0ee45aee6578a1a3d468f9f56b95074050`. Substrate is pre-1.0; use this tested API version. The automated integration test installs this exact revision on a disposable GitHub runner.

1. Build and push the workspace image to a registry your Substrate workers can pull. Use an immutable tag or digest:

   ```sh
   docker build -f Dockerfile.sandbox -t YOUR_REGISTRY/moyai-sandbox:YOUR_VERSION .
   docker push YOUR_REGISTRY/moyai-sandbox:YOUR_VERSION
   ```

2. In Moyai's Runtime settings, select **Substrate** and expand **Set up the Moyai actor template**. Copy the public key. The private signing key stays encrypted on the server; the sandbox only receives the public key.

3. Create an atespace and a template using a snapshot storage location already configured on your cluster:

   ```sh
   kubectl ate create atespace moyai
   python scripts/substrate_template.py \
     --image YOUR_REGISTRY/moyai-sandbox:YOUR_VERSION \
     --storage gs://YOUR_BUCKET/moyai \
     --public-key YOUR_MOYAI_PUBLIC_KEY > /tmp/moyai-template.json
   kubectl ate create actor-template -f /tmp/moyai-template.json
   ```

   The template selects a worker pool labelled `workload: moyai` with at least 2 CPUs and 4 GiB per worker. Reuse an existing pool with `--workload YOUR_LABEL`, or provision one using Substrate's worker-pool setup. For dependency-heavy environment builds, size the template and workers to 8 GiB with `--memory 8Gi`. This is cluster capacity, not a separate pool per session.

4. Enter the control API HTTPS URL, router HTTPS URL, API bearer token, atespace, and template name in Runtime settings. Supply the cluster CA certificate when the endpoints use a private CA. The certificate hostname must match the endpoint. Use credentials authorized to manage actors, their egress policies, and snapshot tags in the configured atespace.

5. Select **Connect and use**. Moyai creates a test actor, verifies signed command execution through the router, deletes the actor, and then saves the connection. A failed test preserves the previous connection. This check briefly uses cluster compute.

New cloud sessions now use Substrate. Model/harness selection, files, Computer, follow-up messages, cancellation, prepared environments, and parallel agents use the same Moyai controls. Prepared environments are rebuilt for the new provider when first needed; existing sessions keep their pinned build.

## Environment configuration

For installations configured entirely through environment variables:

```dotenv
SANDBOX_PROVIDER=substrate
SUBSTRATE_API_URL=https://substrate-api.example.com
SUBSTRATE_ROUTER_URL=https://substrate-router.example.com
SUBSTRATE_API_TOKEN=<bearer credential>
SUBSTRATE_ATESPACE=moyai
SUBSTRATE_TEMPLATE=moyai
SUBSTRATE_SIGNING_KEY=<base64 Ed25519 private key matching the template public key>
```

`SUBSTRATE_CA_CERT` accepts a PEM CA bundle. `SUBSTRATE_TOKEN_FILE` can name a projected, rotating bearer-token file instead of a static API token. HTTP endpoints are accepted only on loopback for local port-forward tests. Do not disable TLS verification.

Settings saved through Runtime take precedence over these environment defaults. Back up the database and `ENCRYPTION_KEY` (or the generated key in `DATA_DIR`) together. API tokens can be rotated in Runtime. To move to another Substrate cluster, use a separate Moyai installation: actor and snapshot references belong to their original cluster.

## Persistence and isolation

Moyai takes FULL Substrate snapshots so workspace files, installed packages, and root filesystem changes survive. It freezes other guest processes before taking a snapshot. Restoring the original actor resumes those processes; creating a child or replacement actor kills the frozen processes and removes their saved command credentials before starting new work. Each new agent gets its own run capability. The actor's UID is projected by Substrate through a read-only identity volume and binds every signed execution request to that actor.

Snapshots are retained as Substrate tags after their actor is deleted. Keep tags while sessions or environment builds reference them. Their storage is billed by your cluster's object-storage provider. Infrastructure billing remains provider-specific; Modal's billing API cannot report Kubernetes costs. LLM usage tracking works for either provider.

Moyai configures HTTP and TLS-passthrough egress for the allowed outbound host patterns (default `*`, equivalent to an internet-enabled workspace). Substrate's own networking and runtime restrictions still apply. Narrow this list when your cluster's policy requires it, including Moyai's broker and the package/repository hosts your tasks need.

## Validate an installation

With Substrate settings available in the environment:

```sh
uv run python scripts/substrate_smoke.py
```

This uses real Substrate actors to check commands, stdout/stderr, file transfers, root filesystem snapshots, clone process isolation, reconnection, Chromium screenshots, and deletion. It cleans up its actors and tags. It does not send model requests to external providers. The GitHub workflow uses the full agent image and additionally runs the actual agent SDK/MCP transport against a local inference fixture, builds a public GitHub project environment, and restores its snapshot into a new sandbox. Set `MOYAI_SMOKE_FULL_IMAGE=1` to include those checks on your installation.
