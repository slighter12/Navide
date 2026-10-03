# Navide Domain Language

Navide is a desktop workbench that hosts agents and installable Plugins while
keeping platform authority distinct from package-declared needs and user-selected
execution preferences.

## Desktop platform

**Desktop Host**:
The Navide platform responsible for desktop lifecycle, native integration, and the authority boundary through which Plugins access platform capabilities.
_Avoid_: UI shell, frontend framework

**Plugin Public Contract**:
The compatibility promises Navide exposes to Plugin authors through its Manifest, public SDK, and capability semantics. Preserving this contract does not by itself guarantee that an existing released package works without rebuilding.
_Avoid_: Package compatibility, Host internals

**Workspace Probe Readiness**:
A Navide workspace milestone at which the selected Plugin and terminal pathways have produced fresh, correlated end-to-end evidence in the current application run. It establishes only those bounded pathways, not complete workflow usability or visual acceptance.
_Avoid_: Workspace usable, fully started, complete acceptance

## Plugin authorization

**Manifest Permission**:
A Plugin-authored declaration of the Host capabilities that a package needs and
discloses to the user. It is a request, not an authorization or an execution
policy.
_Avoid_: User permission, effective permission

**Host Capability Limit**:
A Host-owned boundary on which capability methods exist and which safety rules
cannot be granted by a Plugin or an Execution Policy.
_Avoid_: Manifest permission, user policy

**Execution Policy**:
The user's selected policy for operations initiated by an agent and mediated by
Navide. It has one active mode—full, allowlist, or denylist—and remains distinct
from Manifest Permissions and direct user interaction.
_Avoid_: Plugin grant, manifest policy

**Initiator**:
The Host-authenticated origin of an operation, such as a direct user action or
an agent request. An agent Initiator remains attached when work crosses a Plugin
backend and cannot be replaced by Plugin-supplied data.
_Avoid_: Caller process, plugin-supplied origin

**Policy Source**:
The setting selected to supply the effective Execution Policy. Navide provides a
default source, the user can provide a user source, and a repository can offer a
recommended source that becomes active only after the user accepts it.
_Avoid_: Permission layer, automatic repository grant

**Repository Policy Recommendation**:
A repository-provided Execution Policy setting that the user may choose for that
repository. It has no authority until accepted by the user.
_Avoid_: Repository grant, manifest permission

**Package-version Grant**:
The user's approval of the Manifest Permissions disclosed by one Plugin package
version. It does not define the user's general Execution Policy.
_Avoid_: Execution policy, repository policy

## Agent CLI integration

**Agent CLI Integration**:
Navide's support for an external coding-agent CLI, encompassing its execution
lifecycle, vendor-specific capabilities, conversation identification, and activity
and usage observations. It is distinct from the external CLI's own implementation.
_Avoid_: CLI core, external agent implementation

**Agent CLI Adapter**:
Navide's vendor-specific interpretation of an external coding-agent CLI's native
capabilities and observations under the shared Agent CLI Integration contract.
_Avoid_: Agent implementation, universal CLI behavior

**Agent CLI Runtime**:
The Navide module responsible for executing agent CLIs, PTY input/output, process
lifecycle, vendor adaptation, session identification and log observation. It is
distinct from application policy and durable application persistence.
_Avoid_: External CLI, entire backend

**CLI Capability Integration Status**:
Navide's declared integration status for a particular capability of a particular
agent CLI: integrated, vendor-unsupported or not-yet-integrated. Verification
evidence is separate; missing integration does not establish native incapability.
_Avoid_: Vendor support, verification result
