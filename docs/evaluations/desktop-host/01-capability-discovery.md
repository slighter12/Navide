# Ticket 01 — Desktop Host capability discovery

## Result and evidence boundary

**Disposition: source assessment complete; no Host selected and no candidate is performance-eligible.** Retaining Electron remains a valid outcome. Tauri and Wails v3 justify only targeted next-evidence work on embedding and authenticated routing, not a migration commitment. Their smaller-framework architecture does not establish faster Navide startup, smaller complete installation, or cheaper packaging.

This assessment uses the fixed issue checkout at `0f66f5f34150c8e73db4b7f32d3176fcaa5a2020`, branch `feat/issue-4-desktop-host-evaluation`, not original/main. The supplied input manifest has SHA-256 `e9e04e89a328d3f9eb29ec48445b711ba354a7d155c982d65e01ba9f3c9e6c2a`. The initial status contained pre-existing untracked `docs/adr/`; this work does not adopt or alter it. See the [evidence directory][evidence] for fetched primary bytes, URL/status/hash logs, source-input hashes, search corrections and source-only checks.

**No runtime verification exists from this assignment.** Tests, typechecks, builds, installation, application launches, benchmarks, UI interaction, signing and release operations were **not run**, not passed. Source branches and historical prose claiming prior verification are not fresh platform results. Actual implementation effort is zero; effort below is a qualitative estimate of remaining work, not measured cost.

Labels used throughout:

- **D**: first-party documented support/constraint (rolling documents are explicitly identified).
- **S**: implementation observed in the identified source, not executed.
- **K**: a concrete source/documented gap in the named approach, not proof that the entire framework is incapable.
- **U**: unverified requirement, compatibility, or platform behavior.
- **V**: runtime-verified; **no V entries** in this report.
- Target drafts/plans are requirements/design evidence, not D/S proof of shipped behavior.

Public first-party GET reads were authorized. GitHub release API requests returned 403 rate-limit errors; actual release HTML and tag-addressed raw sources were read instead. Several Google Search narratives misstated versions, command defaults, updater behavior and URLs. Those were rejected in favor of directly fetched owner records; failed reads remain in the log. No exhaustive newest-release claim is made.

## Exact release/source records

| Alternative | Source identifier actually checked | Meaning and maturity |
| --- | --- | --- |
| Electron reference | Navide `0.2.14`; dependency range `^44.4.5`, lockfile resolves **44.4.5**. Electron [v44.4.5 release][er], published 2026-09-23, links commit `694f45852a0f1726cd23bfd379854de489cccb65`. | Stable-shaped reference release; lock resolution is not an installed/artifact version measurement. Tagged security, webview and performance docs inspected. |
| Bounded Electron opportunity | Same reference inputs; **no optimized artifact/version exists**. | No framework change is assumed. Opportunities are hypotheses within the present architecture. |
| Tauri | [tauri-v2.12.1][tr], published 2026-09-30, links commit `30da1fd6e17de6107ecc850c95dfb16b5729f2dd`; tagged Cargo file says 2.12.1. | Stable-shaped Tauri 2 source line examined. The fetched release listing leads with `tauri-v3.0.0-alpha.4`; that is not substituted for v2 evidence. Child-webview APIs in this v2 source are still `unstable`-gated. |
| Wails v3 | [v3.0.0-beta.27][wr], published 2026-10-01, links commit `90486a938587f0e6c1b31fee940302c1bfcf1b8b`; release explicitly says **Pre-release**. Tagged module is `github.com/wailsapp/wails/v3`, Go **1.25.0**. | Beta maintenance/API churn is a separate risk from performance. Wails v2 is excluded; historical proposals to evaluate v2 have no force here. |

Release dates and commit identifiers come from the actual release pages. Tauri plugin versions, WRY/TAO dependency resolution, Wails transitive build inputs and actual webview-runtime versions are **not experiment pins**. Core tags do not pin every separately versioned plugin. Pin the complete toolchain and artifacts after G01/G02, not by copying these research records. Rolling Tauri v2 and Wails v3 docs are archived by exact content hashes; release-tagged implementation takes precedence where they differ. For example, Wails installation prose says Go >=1.24 while beta.27's module requires 1.25.0.

## Fixed-checkout responsibilities and actual seams

### Desktop lifecycle, native integration and workspace ownership

Electron main owns windows, preload/IPC, Python supervision, native menus/dialogs, dock behavior, media permission handling, file/protocol opens, updates, and shutdown. The Vue renderer owns layouts, xterm/input and reactive workspace presentation; Python owns PTYs, CLI adaptation/observations, long-running services and durable application data [N1].

Source audit, not inferred from old diagrams:

- `src/main/index.ts:289–364` stores primary/adopted workspaces separately, canonicalizes folder paths and redirects duplicate opens to the existing main window. Detached run-group windows have separate bookkeeping. `window-registry.ts:1–120` records workspace/adopted-workspace/auxiliary restore state and clean-exit snapshots [N2]. Restore semantics cannot be replaced with merely “multiple windows work.”
- `index.ts:532–589` creates the main renderer with context isolation, no Node integration, **sandbox false**, background throttling disabled and the guest tag enabled. This is not a claim that every renderer is sandboxed. `index.ts:5035–5107` installs navigation, context-menu, zoom, media and native-open handling [N2]. Preserve actual behavior and safety rules; do not invent universal frontend network confinement. The generic window-open handler and the public SDK's gesture-gated operation are different paths.
- `index.ts:5293–5321` already starts Python **in parallel** with window creation and defers Plans-storage warming until that start settles. Parallel startup is not a new optimization delivered by this assessment.
- Workspace switching must leave background panes running; leaving a workspace is different. Documentation owns this workflow requirement [N1]. Source registries and PTY ownership support the intended separation, but switching/focus behavior is U on every platform here.

### Python startup, supervision and communication

`backend.ts` allocates loopback addressing, creates a per-backend Host-session token, binds an exact-digest activation catalog, recovers login-shell PATH on applicable desktop launch paths, and starts either the packaged PyInstaller executable or the normal uv development entrypoint. A separate trust-confirmation key is handed over stdin, not treated as renderer/package data. Health timeout failure tree-kills the child; POSIX shutdown uses SIGTERM with bounded tree cleanup, Windows uses stdin shutdown then bounded cleanup. The backend also receives a parent PID; no cross-platform Job Object guarantee is assumed [N3].

`index.ts:1951–2035` wires bounded auto-restart and lifecycle epochs. `useBackend.ts` gets backend info through the Host bridge, then uses shared correlated WS requests/events, reconnect, reset of old requests on backend changes, and sleep recovery. `wsClient.ts` parses binary terminal output separately from JSON messages [N4]. A candidate's Rust/Go bindings may replace **Host transport**, not Python's domain implementation or external CLI semantics. A generic shell/sidecar API does not provide Navide's health, identity, restart or cleanup policy automatically.

### Packages, grants, views and receiver children

The source is more advanced than some current/target document status banners. The v1 guide's statement that contributions are not rendered, the v2 opening text about unopened third-party paths, and old all-WebContentsView diagrams cannot establish the checkout's current implementation [N5]. Conversely, source wiring does not prove those features passed release or human acceptance.

- Installed manifests/canonical package identity and contributions are handled by the installed-package, manifest and catalog modules. Installer preparation verifies digest/signature/trust/archive shape before commitment. `pluginIpc.ts:1459–1556` re-verifies a staged candidate, exact artifact identity and grant before restart, serializing package transactions; rollback and removal have separate handlers. Candidate/active/previous and matching storage selections are Host lifecycle concerns, not framework features [N6]. No released marketplace archive was acquired or exercised here.
- **Outer in-window contributions now use `<webview>` guests**: `PluginRegionHost.vue` requests `prepareContribution`, receives only a Host-issued URL, mounts a guest and keeps hidden tabs mounted. Main attach hooks strip tag-sourced preferences and force the plugin preload, sandbox, no Node, web security and authoritative arguments. `frontendPluginManager.ts:10701–10789` binds the guest's own WebContents to the reservation, checking its embedder when available. Native window contributions retain a native WebContentsView carrier [N7]. CSS stacking/modal coverage is a real compatibility requirement, not satisfied by unrelated external windows.
- **Receiver composition is different from the outer guest carrier.** `plugin-preload.ts:434–481` creates an about:blank iframe in the exact registered mount host, obtains its Electron frame token, asks main for blank-frame admission, then navigates to the Host locator. `PluginFrameBindingRegistry` reserves package/artifact/version/workspace/instance/contribution/receiver identity and binds an exact frame and document generation to private MessagePorts [N8].
- `frontendPluginManager.ts:2518–2662` refuses ordinary IPC from subframes and revalidates active child ports against frame tree, parent, URL/origin, package/version, workspace, receiver/document generation and live instance. `pluginFrameAssetProtocol.ts` uses a random revocable per-artifact origin, canonical contained regular-file reads and restrictive CSP (`connect-src 'none'`). Close guards/detail targets/receiver registration are part of the composition contract; declarations without live registration must fail closed [N8], [N5]. A page-provided instance id or a shared window label cannot substitute for this producer/consumer chain.

### Terminal routing and effective security

The audited Host terminal chain is Python `_active_emit` / `_PTY_OWNERS` (`app.py:838–979`) → owning authenticated WS connection → binary frame decoding/shared client → `createHostTerminalDockPort` (`hostSurfacePorts.ts:260–369`) → session-filtered `terminalPort.onOutput` (`useTerminal.ts:2399–2415`) → queued **xterm `term.write`** (`useTerminal.ts:1230–1314`). Output for detached owners is dropped while the PTY remains live; the ownerless janitor has a grace period. `terminal.reattach` (`ws_handlers.py:8089–8183`) checks Host-only attestations and claims surviving PTYs [N9]. This identifies a final consumer, not a successful readiness trial.

Plugin AI CLI sessions use Host-owned tuple bindings, not a public raw PTY grant. The broker checks catalog address/schema, Manifest Permissions, publisher eligibility, exact Package-version Grant, workspace, session ownership, Host-authenticated Initiator and agent Execution Policy; `full` does not bypass the other boundaries. Public gesture-sensitive calls require a gesture observed by isolated Host preload, not a supplied trusted-event bit. Re-evaluation/drain/revocation and Backend Wire identity must survive adaptation [N10].

Third-party native-backend admission/sandbox code is actually present despite older deferred prose: global switch, exact-content/permission consent, per-plugin disable, trust/restart checks, method/event allowlists, workspace filesystem-only bridge, watchdog and no MCP agent-method grant. macOS builds Seatbelt rules; Linux depends on usable system bubblewrap/unprivileged namespaces; Windows **always returns unavailable** in this source. No unsandboxed fallback is allowed. CPU/memory enforcement is the watchdog, not the sandbox; macOS metadata/system-library and Linux system-binary allowances are not complete filesystem secrecy [N11]. Code comments claiming past macOS verification are historical assertions, not V evidence in this ticket. Retain these effective limits rather than “fixing” baseline policy or enabling Windows native backends as part of Host evaluation.

ADRs 0001–0003 preserve vendor-native CLI semantics and keep proposed Agent CLI Runtime migration separate. Vue, Python, external CLIs, application persistence formats and public Plugin contracts stay; no Rust PTY migration, new consent regime or frontend rewrite is selected [N12].

## Requirement/platform evidence matrix

Each cell lists **macOS / Linux / Windows**, in that order. `S+U` means a source path exists but that platform's actual Navide behavior is unverified. `D+S+U` means framework documentation/tagged APIs exist but Navide adaptation is unverified. Bounded optimization inherits reference obligations only; **all changed optimized behavior is U**. R identifiers are the coverage/check keys.

| Requirement | Electron reference | Bounded Electron opportunity | Tauri 2.12.1 | Wails v3 beta.27 |
| --- | --- | --- | --- | --- |
| R01 lifecycle/native menus, dialogs, dock, media, protocols, guarded quit | S+U / S+U / S+U [N2] | Reference / Reference / Reference; change U | D+S+U / D+S+U / D+S+U [T1], [T9], [T10] | D+S+U / D+S+U / D+S+U [W1], [W2] |
| R02 Python supervise, health, auth, restart, WS/HTTP | S+U / S+U / S+U [N3], [N4] | Reference ×3; change U | D+U / D+U / D+U; sidecar primitive only [T3] | S+U / S+U / S+U; services/hooks, Host supervisor still needed [W1] |
| R03 multiple windows, workspace dedup, restore | S+U / S+U / S+U [N2] | Reference ×3; change U | D+S+U / D+S+U / D+S+U; Navide registry U [T1] | D+S+U / D+S+U / D+S+U; Navide registry U [W1], [W2] |
| R04 switching/background continuity, session recovery | S+U / S+U / S+U [N1], [N9] | Reference ×3; change U | U / U / U | U / U / U |
| R05 verified install/update/restart/rollback/uninstall and storage | S+U / S+U / S+U [N6] | Reference ×3; change U | U / U / U; framework app updater is not Plugin lifecycle | U / U / U; framework app updater is not Plugin lifecycle |
| R06 embedded outer Plugin views, hidden tabs, CSS stacking/focus | S+U / S+U / S+U [N7] | Reference ×3; change U | S+K+U / S+K+U / S+K+U; unstable child API, layout parity U [T1] | U / U / U; top-level multi-window is not embedded parity [W1], [W2] |
| R07 receiver/child mount, exact frame/doc/port identity, detail/close guards | S+U / S+U / S+U [N8] | Reference ×3; change U | U / D+K+U / U; Linux iframe IPC distinction constraint [T2] | U / U / U; exact child admission not established [W1], [W3] |
| R08 deterministic local terminal at xterm, AI CLI session tuple/vendor semantics | S+U / S+U / S+U [N9], [N10], [N12] | Reference ×3; change U | U / U / U; retain Python+xterm, web engine parity U | U / U / U; retain Python+xterm, web engine parity U |
| R09 grants/Initiator/policy denial, stale/wrong-instance denial, trusted input | S+U / S+U / S+U [N10] | Reference ×3; change U | D+S+U / D+K+U / D+S+U; Tauri ACL not Navide grants [T2] | S+U / S+U / S+U; routing and OriginInfo callback not sufficient [W1], [W3] |
| R10 third-party native-backend confinement, fail-closed availability | S+U / S+U / S+K [N11] | Reference ×3; change U | U / U / U; must preserve Mac rules, Linux conditionality and Windows refusal | U / U / U; same mandatory baseline limits |
| R11 unchanged released archives **and separately** public contract fidelity | U / U / U; no archive trial | U / U / U | U / U / U; Host private transport incompatibility K, public adapter possible U [N5], [N8], [T1], [T2] | U / U / U; same private-transport gap K, public adapter possible U [N5], [N8], [W1] |
| R12 distribution/update with complete runtimes/components | S+U / S+K+U / S+U; Linux deb has no in-app update [N13] | Reference ×3; change U | D+U / D+U / D+U; signed updater/platform formats [T3], [T4] | D+S+U / D+S+U / D+S+U; update payload/resource parity U [W4], [W5] |

No framework D or S entry claims Navide compatibility. Platform-neutral source bodies do not establish native behavior. Electron's Windows backend refusal and Linux deb update restriction are **baseline limitations**, not replacement failures or authority to remove their fail-closed behavior. Tauri's Windows child creation has an additional documented synchronous-command/event-handler deadlock constraint: use the documented async/separate-thread path, not a framework patch [T1].

## Candidate findings and minimum adaptation

### Electron reference and bounded opportunities

Electron already provides the exact outer-guest, native-view and receiver-frame APIs in the checkout; maintain them as the reference while rechecking source after merges. Upstream **discourages the webview tag** because rendering/navigation/event routing can change [E1]. This is a maintenance risk, not permission to replace its current CSS-stacking behavior with native views or remove embedding. Package compatibility still requires a released unchanged archive trial.

Source-backed opportunities, not implementation or predicted gains:

1. **Incremental packaging dependency reuse:** `build.sh` always repackages Python; production `build:official-plugins` rebuilds public/official bundles, and Plans packaging invokes clean PyInstaller. A common Vue-only edit may therefore repeat unchanged backend/plugin work. The existing Plans `--if-needed` **development-only** cache fingerprints Python sources, script, dependency inputs, interpreter marker, platform/architecture and executable bytes. Investigate similarly complete invalidation/accounting for a separately authorized incremental packaging experiment; do not silently enable the dev bypass in production [N14]. Estimate: small-to-medium bounded build-graph work, meaningful verification burden. CI cold builds remain separate.
2. **Startup critical-path inspection before tuning:** login-shell PATH recovery is awaited before Python spawn, while some Host modules/imports and package verification precede/overlap renderer creation. Electron documents deferring unnecessary loading and avoiding synchronous main-process work [E2], [N3], [N2]. Measure these actual paths later before proposing a bounded change. Preserve PATH/tool discovery, trust/receipt checks and eventual full workload; no skipping security, warming or mandatory Plugins. Estimate: small investigation, uncertain optimization scope. Already-parallel window/backend start and existing delayed storage warming are not new opportunities.
3. **Resource accounting, not component removal:** package config ships frozen Python, official/factory and legacy recovery resources, and platform helpers. Inventory deduplication/packaging inclusion only where equivalent behavior and recovery remain. Do not infer that an apparently duplicate build/artifact can be removed [N13]. Estimate: small source/accounting pass; removals require proof and possibly D04.

These are opportunities to investigate, not a proven startup gain. Startup has priority over size then packaging; installed/fixed-workspace startup has priority over development/Welcome. None may improve a score by stopping before deferred work completes.

### Tauri 2

**Feasible primitives:** tagged window/webview APIs, native menus/window lifecycle, navigation/resource/page-load hooks and initialization scripts; official externalBin/shell support can ship a self-contained Python executable; Vue assets do not inherently need rewriting [T1], [T3]. The Host must own spawning and permissions, not give Plugins a general shell API. Window creation/labels must remain Host-owned.

**Concrete differences/gaps:** `Window::add_child` is desktop+unstable gated and has native position/size/resize semantics, not Electron guest DOM/CSS layout. Its Windows synchronous creation caveat is documented. Initialization scripts are not Electron contextBridge isolated preload equivalence (Windows adds them to subframes too). Tauri's rolling capabilities docs explicitly say Linux cannot distinguish embedded iframe requests from the window. Tagged ACL source says matching a window grants all its webviews regardless of webview selectors; use webview-specific restrictions rather than broad parent grants. App commands are allowed to all app windows/webviews **by default** unless the app command manifest restricts them. ACL therefore cannot be mistaken for Navide Package-version Grants or agent Execution Policy [T1], [T2].

**Minimum plausible Host adaptation:** narrow Rust Host services for lifecycle/native operations, Python supervision/token/catalog/WS integration, unchanged persistence formats, verified package loading and grant/policy broker; a Host-injected SDK-compatible bootstrap for existing frontend bytes; per-artifact local asset origins and capability-denying routes; instance-aware events/AI CLI delivery; receiver composition with revocation on navigation/teardown. Avoid a broad invoke/shell bridge and validate requests at the real producer. Generic label/URL matching is not exact child-document admission.

A **supported-API authenticated replacement for receiver frame/port binding is not yet established**, particularly on Linux. Separate child webviews could avoid shared-iframe attribution but do not automatically preserve receiver DOM composition, focus or modal stacking. This becomes an affected-candidate **D04 dependency** if equivalence needs a presentation/security/ownership exception. Unstable supported APIs are not themselves a framework fork; dependence on substantial custom engine changes would be a stop condition.

Estimate: medium lifecycle/supervision adaptation; **high, unresolved** view/broker/composition work, plus three-web-engine maintenance. Much Host logic is TypeScript/Node today; preserving Vue/Python does not make the Desktop Host implementation a drop-in swap. Reusing that logic in an added Node service would add a runtime/process and must be accounted, not assumed free. No such architecture is selected. Continue only to establish a bounded supported composition/security seam; stop the candidate if it requires removed workflows, weakening, a large rewrite or a maintained framework fork.

### Wails v3

**Feasible primitives:** beta.27 application services/startup/shutdown/ShouldQuit, native window APIs/options, menus/dialog examples, custom message/transport seams and multiple native top-level windows [W1], [W2]. Python can remain a packaged child supervised by Go, with Vue/xterm retained. Native camera/microphone permission options are not Navide Plugin grants. The inspected window example also calls native menu/dialog APIs; native details such as dock, permissions, key helpers and file-open behavior still need platform parity checks [W8].

**Evidence limits and actual differences:** the inspected APIs/examples establish top-level windows, not an Electron-compatible in-window guest carrier or exact receiver child/document binding. No supported separate embedded carrier satisfying Navide CSS stacking and frame admission was established by this pass. This is **U**, not “Wails cannot embed.” `RawMessageHandler(Window, message, *OriginInfo)` exists, but the attempted suggested OriginInfo source URL was 404 and its per-platform population/authentication was not established. The runtime dispatcher accepts request-carried window name/id and can choose the first window when unspecified; these are routing hints, not authorization. `AllowSimpleEventEmit` is default-off and explicitly warns that enabling it lets any script synthesize named Host events; it cannot be a trusted-input or Plugin identity mechanism [W1], [W3].

**Minimum plausible Host adaptation:** narrow Go Host service/asset handlers, Python supervision/authenticated transport, package verification/grants/selector/storage behavior, SDK bootstrap compatibility, and Host-owned instance/document/port routing with event/AI CLI ownership. Keep generic services/events/native open facilities unavailable to untrusted package frames. Whether the documented custom transport/raw-message seams can enforce that across macOS/Linux/Windows without framework patches remains U. A DOM component sharing the Host's world or an external window cannot silently replace isolated embedded contributions.

Estimate: medium lifecycle/supervision work; **high, unresolved** embedded carrier and security adaptation; high beta/API/platform maintenance uncertainty. The exact module adds a Go build toolchain (1.25.0 at this tag), not a replacement Python backend. Continue only for focused supported-API evidence on isolated child content and authenticated frame/document attribution. If no bounded approach preserves the mandatory behavior, record stop evidence or D04—not an implicit redesign. Beta status alone is not a hard incompatibility or a benchmark result.

## Unchanged packages versus public contracts

**Two independent questions, all-platform U:** (1) can the exact released, signed archive run unchanged; (2) does a Host adapter preserve the Manifest/public SDK/capability/error/lifecycle contract? A rebuilt demo answers neither for the original archive. No released package digest/version fixture was selected or installed here.

Manifest/schema and Backend Wire data are language-neutral. Self-contained backend executables can potentially remain byte-identical for the same OS/architecture and unchanged wire semantics. The Host must keep signature/trust/revocation, exact-version grants, content-bound backend consent, target validation and lifecycle/rollback behavior; changing package bytes invalidates the unchanged-package claim [N5], [N6], [N11].

Current package runtimes consume the SDK's private `window.nav` bootstrap and Electron-based receiver/frame mechanics. Tauri/Wails do not supply Electron contextBridge/WebFrameMain/MessageChannelMain by default. A **Host-owned compatible private adapter** might run unchanged built SDK bytes, while still preserving public contracts; that is U until the actual archive is tested. Rebuilding/modifying the SDK or package, changing origins/CSP expectations or native backend packaging is an explicit package-compatibility gap and migration cost, not automatic public-contract failure. Legacy v1's direct private bridge use adds a separate compatibility burden; do not silently abandon approved recovery workflows [N5], [N8].

## Required runtimes, distribution and update accounting

| Platform | Electron reference | Tauri 2 | Wails v3 |
| --- | --- | --- | --- |
| macOS | Bundled Electron/Chromium/Node; frozen Python, equivalent official Plugins, pane/Fn-key/STT helpers as configured. Navide target minimum is macOS 13. | OS WKWebView; Rust Host plus frozen Python, Plugins and required helpers/resources. No arbitrary lower OS floor is adopted. | OS WebKit; compiled Go Host plus the same components. Rolling install docs list older macOS floors, but Navide's floor is retained pending separate validation. |
| Linux | Electron bundle plus Python/Plugins/STT; AppImage/deb config. Conditional bubblewrap is a third-party-backend prerequisite. | System WebKitGTK 4.1/GTK dependencies; include required libraries/acquisition costs and bubblewrap, Python, Plugins/helpers. Distro/package support is U. | Default GTK4/WebKitGTK 6.0; rolling docs primarily support Ubuntu 24.04. Legacy GTK3/WebKit2GTK 4.1 opt-in is documented through v3.0.x, removal planned for v3.1: explicit compatibility/maintenance choice, not equivalent everywhere. Same Python/Plugins/helpers/bubblewrap. |
| Windows | Electron bundle; frozen Python .exe, Plugins/STT, NSIS installer. Native third-party Plugin backends remain refused. | WebView2 required; installer supports runtime provisioning choices. Preprovided runtime versus download/offline/fixed distribution must be reported separately. Python/Plugins/helpers and same native-backend refusal remain. | WebView2 required; rolling packaging docs describe NSIS/bootstrapper and MSIX options. Same backend/resources, explicit prerequisites and Windows refusal. |

Sources: package config [N13], Tauri prerequisites/sidecar/installer [T3], [T7], [T8], Wails installation/module/packaging [W4], [W7], [W9]. Rust/Go/C toolchains and Node/pnpm for Vue are build requirements, not automatically installed-app runtimes. A frozen Python or self-contained Plugin executable must not be counted as requiring user-installed Python. External agent CLIs and their runtime dependencies remain existing workload prerequisites, not removed to improve size.

Electron updater supports macOS, Windows and Linux **AppImage**, not deb; source comments note currently unsigned Windows updates [N13]. Tauri's updater requires signatures that cannot be disabled and has platform-specific app/update formats [T4]. Wails has actual tagged updater implementation and rolling documentation: app re-execs in helper mode, verifies/stages payloads, and swaps a target. The guide allows single-top-level-entry ZIP/tar trees, excludes DMG/PKG/MSI payloads in v1, and distinguishes signatures from digest-only checking [W5]. A single executable swap does not prove complete Python/Plugin/helper resource update parity. Preserve Navide update UI, channel, cleanup and quit semantics with an adapter; resource-tree update and failure recovery remain U.

Account installer bytes, installed footprint **and additional required acquisition/installation**, with and without preprovided webview runtimes; do not double count. No sizes, times or variation were measured. Common Python/frontend/official-Plugin work must be separated from Host build/final packaging. Local incremental Vue-only packaging and clean CI are separate scenarios. Signing/notarization feasibility is only a distribution requirement: no operations/credentials were provisioned and no signing waits enter speed scoring.

## Decisions and next evidence

| Finding | Disposition / closure evidence needed |
| --- | --- |
| Tauri Linux shared-iframe attribution and native child-view/layout differences | **Affected-candidate D04 dependency** if preserving the current boundary requires presentation/ownership/isolation exceptions. First establish a supported equivalent producer/consumer binding with deny tests; no exception approved. |
| Wails embedded carrier/frame authentication not established | Evidence gap, not proven impossibility. Seek focused supported API/source evidence; any necessary exception becomes **D04** for Wails. Do not enable broad event/service authority. |
| Host-private Electron transport absent in both alternatives | Adapter/package gap. Demonstrate one released **unchanged** archive separately from public-contract conformance, including a composed receiver and denied operation. Rebuilds must be disclosed. |
| Baseline source/prose disagreements | Re-audit the eventual merged snapshot and record observed versus target status. Do not edit rules/contracts or infer migration completion from plan checkmarks. |
| Windows native-backend refusal; Linux sandbox conditionality | Preserve baseline limitations; no new sandbox implementation or consent policy is authorized. Missing Linux/native environment evidence remains U. |
| Any substantial framework patch/fork, weakened boundary, removed workflow, large rewrite | Recommendation blocker; stop the affected approach with evidence rather than implementing a workaround. Ordinary supported Host adaptation is not automatically framework patching. |

Ticket 01 has no ticket blockers and this research assignment closes its **source-evidence deliverable only**. Runtime tickets still require **G01 user-confirmed merged baseline** and **G02 separately bounded execution assignment**. Then select exact scenarios/artifacts/observer contracts (D01), run related baseline suites and real-desktop probe (02), establish measurement methods and Electron reference (03), and obtain numeric worthwhile-gain/regression decision (04) **before rankings**. Tauri/Wails performance work needs the respective basic compatibility pass (05/06); a hard stop is not a pass.

Required later probe evidence: fresh challenges bound to run/workspace/window/package instance/pane, actual mounted Plugin request/event round-trip, evaluated local output reaching xterm, negative stale/wrong-instance/backend-only/echo/ack-only checks, and unauthorized-operation denial without the prohibited side effect. Workspace Probe Readiness covers only those pathways. Full workflows, cross-workspace continuity, three-platform behavior, visual presentation/focus and genuine trusted input remain unverified; human-only judgments belong in one consolidated checklist. No user-run ad-hoc automated source checks are requested.

**Bounded conclusion:** keep Electron as reference; investigate bounded build/startup opportunities without assuming gains; continue Tauri/Wails only toward the named composition/security evidence gates. There is insufficient evidence for adoption, full migration, or a split-host production strategy.

## Citation index

Local links resolve in this fixed checkout; symbol/range locators above were re-audited here. The [source locator index][locators] provides direct links to supplementary producers, consumers, contracts, plans and primary source bodies (including Tauri webview/ACL and Wails dispatcher/module). [Source-input hashes][sourcehashes] bind their exact bytes, including supplied drafts. Primary external source bodies are archived under [evidence][evidence]; owner URLs and HTTP failures are in [fetch log][fetchlog].

[N1]: ../../en-US/architecture.md
[N2]: ../../../src/main/index.ts
[N3]: ../../../src/main/backend.ts
[N4]: ../../../src/renderer/src/composables/useBackend.ts
[N5]: ../../en-US/plugin-development-v2.md
[N6]: ../../../src/main/plugins/pluginIpc.ts
[N7]: ../../../src/renderer/src/components/PluginRegionHost.vue
[N8]: ../../../src/main/plugins/pluginFrameBinding.ts
[N9]: ../../../src/renderer/src/platform/terminal/composables/useTerminal.ts
[N10]: ../../../src/main/plugins/pluginCapabilityBroker.ts
[N11]: ../../../src/main/plugins/pluginBackendSandbox.ts
[N12]: ../../adr/0001-preserve-agent-cli-semantics.md
[N13]: ../../../package.json
[N14]: ../../../scripts/build-plans-v2-backend.mjs
[E1]: https://github.com/electron/electron/blob/694f45852a0f1726cd23bfd379854de489cccb65/docs/api/webview-tag.md
[E2]: https://github.com/electron/electron/blob/694f45852a0f1726cd23bfd379854de489cccb65/docs/tutorial/performance.md
[T1]: https://github.com/tauri-apps/tauri/blob/30da1fd6e17de6107ecc850c95dfb16b5729f2dd/crates/tauri/src/window/mod.rs
[T2]: https://v2.tauri.app/security/capabilities/
[T3]: https://v2.tauri.app/develop/sidecar/
[T4]: https://v2.tauri.app/plugin/updater/
[T7]: https://v2.tauri.app/start/prerequisites/
[T8]: https://v2.tauri.app/distribute/windows-installer/
[T9]: https://v2.tauri.app/plugin/dialog/
[T10]: https://v2.tauri.app/plugin/deep-linking/
[W1]: https://github.com/wailsapp/wails/blob/90486a938587f0e6c1b31fee940302c1bfcf1b8b/v3/pkg/application/application_options.go
[W2]: https://v3.wails.io/features/windows/multiple/
[W3]: https://github.com/wailsapp/wails/blob/90486a938587f0e6c1b31fee940302c1bfcf1b8b/v3/pkg/application/webview_window_options.go
[W4]: https://v3.wails.io/getting-started/installation/
[W5]: https://v3.wails.io/guides/updater/
[W7]: https://github.com/wailsapp/wails/blob/90486a938587f0e6c1b31fee940302c1bfcf1b8b/v3/go.mod
[W8]: https://github.com/wailsapp/wails/blob/90486a938587f0e6c1b31fee940302c1bfcf1b8b/v3/examples/window/main.go
[W9]: https://v3.wails.io/guides/build/linux/
[locators]: ../../../.scratch/desktop-host-evaluation/evidence/01/source-locators.md
[er]: https://github.com/electron/electron/releases/tag/v44.4.5
[tr]: https://github.com/tauri-apps/tauri/releases/tag/tauri-v2.12.1
[wr]: https://github.com/wailsapp/wails/releases/tag/v3.0.0-beta.27
[evidence]: ../../../.scratch/desktop-host-evaluation/evidence/01/
[sourcehashes]: ../../../.scratch/desktop-host-evaluation/evidence/01/source-inputs.sha256
[fetchlog]: ../../../.scratch/desktop-host-evaluation/evidence/01/fetch-log.jsonl
