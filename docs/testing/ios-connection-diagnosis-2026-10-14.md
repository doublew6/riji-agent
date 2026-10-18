# iPhone connection diagnosis — 2026-10-14

The user reported “连接未完成” after the mentor UI installation. The specific
phone-side failure has **not been reproduced or resolved**. This record is
separate from successful installation and app launch.

## Read-only findings

- The verified Air host is a MacBook Air. The main service health endpoint
  returned HTTP 200; the three existing mobile/main loopback listeners were up.
- An anonymous request to the configured public `/mobile/v1/capabilities`
  endpoint returned the expected HTTP 401. A second request through a public DNS
  relay address retained hostname/TLS verification and also returned 401. This
  establishes ingress reachability from the development Mac, not successful
  authentication or connectivity from the phone.
- The latest installation backup's saved connection origin matches the public
  origin. A read-only query of Air's device registration confirms that the
  corresponding archived device exists and is not revoked. No credential/token
  was exported; the current phone Keychain remains unverified.
- After the user reported reconnecting the phone, both `devicectl` and
  `xcdevice` still reported the iPhone unavailable. The local USB enumeration did
  not identify an iPhone. Unlocking/reconnecting to the development Mac was
  requested. These observations do not establish the cause of the app's network
  error.

## Error presentation findings

`MobileTransport.swift` converts transport exceptions into `MobileError.transport`;
`MobileModels.swift` renders that as “连接未完成。未确认的内容仍保留，请核对原操作状态。”
The same notice is used for read-only page loading and mutations, so the text
does not distinguish offline, DNS, TLS, timeout, or interrupted transport. The
old pairing-specific message is not sufficient evidence that pairing failed.

The settings capability description also still says roundtable access is not
provided, despite the current capability-driven roundtable implementation.
These are verified presentation issues, not a diagnosis of the phone's failure.

## Next verification gate

1. Establish an available, unlocked physical-device connection.
2. Observe the exact app screen and failing action; capture only scoped,
   secret-free diagnostic evidence. Do not reset or re-pair to mask the failure.
3. Distinguish current credential restoration, authenticated capability loading,
   and the affected read or pending operation. Never resend an uncertain message
   as a diagnostic shortcut.
4. Reproduce the identified failure and verify the correction on the same path,
   followed by cold launch and foreground recovery.

The first simulator usability walkthrough belongs to development. It should
cover entry, keyboard/scrolling, mentor/roundtable controls, North Star selection
and failure recovery at a screen size comparable to the user's phone. Physical
touch, carrier networking, device authorization, iCloud and perceived comfort
remain separate phone checks. The prior Pro Max synthetic UI evidence is not
proof of a completed physical-phone usability walkthrough.

No app code, Air configuration, registration, production service, diary or memory
was changed during these checks. No model message was sent. No complete physical
or simulator walkthrough was performed in this diagnostic pass.
