# Grafana image rendering — server-side PNG/PDF capture (#877)

The obs box provisions Grafana's **image renderer** so dashboards and panels can be
rendered to **PNG/PDF server-side** — via the render API, in alert notifications, and
for scheduled reports. This turns "screenshot a live panel and paste it into a doc"
into a scriptable one-liner: drive a scenario, let the window settle, hit the render
API, commit the PNG. No human clicks.

## Deployment shape

A **standalone remote-rendering service**, not the Grafana plugin.

- **Service:** the `grafana-image-renderer` role installs the official standalone
  `grafana-image-renderer` binary (a GitHub release asset), a Chromium-based browser,
  and a `grafana-image-renderer.service` systemd unit listening on `:8081`. This
  mirrors the lab's download-a-binary-plus-unit idiom (loki / prometheus / logcli) and
  the bake/configure split: the binary + browser + unit are **baked** (install half,
  left inert), and the unit is **enabled + started per-run** (configure half, in the
  observe phase).
- **Grafana wiring:** the `grafana` role bakes a systemd env drop-in
  (`/etc/systemd/system/grafana-server.service.d/rendering.conf`) that sets
  `GF_RENDERING_SERVER_URL=http://localhost:8081/render` and
  `GF_RENDERING_CALLBACK_URL=http://localhost:3000/`. Renderer and Grafana are
  co-located on the obs box, so both are `localhost`.

**Why the standalone service and not the plugin.** The deprecated
`grafana-image-renderer` *plugin* ships an **x86_64 build only** — there is no
`linux-arm64` build in the Grafana plugin catalog. The lab's obs box is **dual-arch**:
it bakes natively as `aarch64` on an Apple-silicon host and `x86_64` on an x86 host
(see `docs/development/box-model.md`). The standalone binary ships **both** arches, so
it is the only shape that renders on both without introducing a container runtime
(which the lab's provisioning does not use). The renderer also defaults to
`--browser.sandbox=false`, i.e. it launches the browser with `--no-sandbox`, which is
required under Ubuntu 24.04's restricted unprivileged user namespaces.

### Browser

The renderer binary bundles **no** browser; one is installed separately and pointed at
via `--browser.path` (host-resolved):

- **x86_64** → `google-chrome-stable` from Google's apt repo (a self-contained `.deb`
  that pulls its own Chromium runtime libraries; a browser Grafana explicitly
  supports).
- **aarch64** → the distro `chromium` (no arm64 Chrome/Edge `.deb` exists).

## Render a panel to PNG from a script

The lab opens Grafana with anonymous Admin (`grafana_anonymous: true`), so no auth
header is needed inside the lab. Find a dashboard's UID in the Grafana UI (Dashboard
settings → JSON model, the `uid` field) or via the API
(`curl -s localhost:3000/api/search | jq`).

Single panel (`d-solo`, pick the `panelId` from the panel's share menu):

```bash
curl -fsS -o panel.png \
  "http://localhost:3000/render/d-solo/<dashboard-uid>/<slug>?orgId=1&panelId=<id>&width=1000&height=500&from=now-15m&to=now"
file panel.png          # => PNG image data, 1000 x 500
```

Whole dashboard (`d`):

```bash
curl -fsS -o dashboard.png \
  "http://localhost:3000/render/d/<dashboard-uid>/<slug>?orgId=1&width=1600&height=900&from=now-15m&to=now"
```

`<slug>` is cosmetic (any value resolves once the UID matches); `width`/`height` are in
pixels; `from`/`to` accept the usual Grafana time expressions. Outside the lab (login
required), add `-H "Authorization: Bearer <service-account-token>"`.

## Verify (live acceptance)

The render endpoint is a **live** check — run it on a cold-deployed, running lab:

```bash
systemctl is-active grafana-image-renderer.service   # => active
# Render any real panel and confirm PNG magic bytes:
curl -fsS -o /tmp/panel.png \
  "http://localhost:3000/render/d-solo/<dashboard-uid>/<slug>?orgId=1&panelId=1&width=800&height=400&from=now-15m&to=now"
head -c8 /tmp/panel.png | xxd | grep -q '8950 4e47' && echo "PNG OK"
```

A `400`/`500` from `/render` with an empty body usually means Grafana cannot reach the
renderer (`server_url`) or the renderer's browser failed to launch — check
`journalctl -u grafana-image-renderer` and `journalctl -u grafana-server`.
