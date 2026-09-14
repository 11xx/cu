# Chrome and Chromium setup boundary

`cu browser products` reports named Chromium-family binary/profile candidates
(Google Chrome, Chromium, Thorium) and each one's setup state. A product is available only when a matching binary and profile are
both present at runtime; a profile directory alone is not an installed browser.
The report does not select the first match, grant a page, or register a host.

The current Firefox route uses a Manifest V2 background page, the Firefox
`browser` API, and the fixed `cu-browser-read@local` native-host allowlist. A
Chromium route needs a separate Manifest V3 service-worker entry, Chrome API
surface, extension development identity, and native-messaging registration for
that exact extension origin. Service workers can be suspended, so grant and
pending-request state must survive worker restart or be revoked; a native port
or a reconnect is not proof that a page remains authorized.

The bounded successor contract is:

1. Select one product and one profile explicitly and verify its binary, profile,
   extension identity, and native-host manifest before writing anything.
2. Load the unpacked development extension without granting a tab; grant only
   the current tab through the product's explicit UI.
3. Prove read, Stop, navigation, closure, worker restart, native disconnect,
   missing/mismatched host, and conflicting-manifest behavior with the selected
   product. A same-looking Firefox manifest or an engine-family match does not
   satisfy this proof.
4. Remove only files created by the setup and report the exact product/profile
   identity. Store publication, signing, security preference changes, and
   profile restart remain outside this route.

Whether a product is present is a runtime reading. Record the selected product,
profile, executable, extension identity, and native-host path in the run's
evidence; this document states the setup boundary without turning one host's
inventory into a support claim.

## Registration

A product is marked `native-host` only once CU's MV3 extension identity and the
product's profile-local native-host lookup have been proven with that product;
every other product reports `unprobed` and `cu web setup` does not offer it.

The Chromium extension ID is pinned by the public key in
`browser-extension/chromium/manifest.json`: an unpacked load derives the ID
from that key, so the same origin is valid on every machine and no private key
is needed to load it. `cu web setup --browser <product>` writes
`org.cu.read.json` into `<profile root>/NativeMessagingHosts/`, allowing only
`chrome-extension://<pinned id>/`; Chromium-family lookup does not accept
wildcard origins. The profile root is `--user-data-dir` when given, otherwise
the product's only existing default root. A missing root refuses, because setup
never creates a profile; several candidate roots refuse as ambiguous; and a
different existing file at either path refuses rather than being overwritten.
Setup registers the host only: it loads no extension and grants no tab.

## Service-worker lifecycle

The Chromium entry keeps grant state only in its service worker's memory, the
same controller Firefox runs in a persistent background page. A worker the
browser stops loses that state, and its native port closes; the host exits on
end of input and removes its helper socket, so a stopped worker ends every grant.
Nothing is persisted and nothing is restored: a restarted worker starts without a
grant and does not reconnect to the host until the user grants a tab again. A
native-port reconnect is never treated as renewed page authority.

Listeners reply through `sendResponse` in this entry, because a Chromium-family
listener's returned Promise delivers no reply; the Firefox entry keeps returning
Promises.
