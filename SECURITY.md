# Security Policy

If you believe you have found a security vulnerability in
python-can-helix, please do NOT open a public issue.

Report it privately to Gothenburg Quantum Analytics support:
support@gqanalytics.com

Please include a description, reproduction steps, and the affected
version. You will get an acknowledgement within a few business days.

## Scope notes

- Authentication always goes through `HelixSession`; credentials are
  never part of the python-can bus configuration or its logs.
- The device's public key is fetched over plain HTTP, so an *unpinned*
  session is trust-on-first-use: the first contact cannot verify which
  device it is talking to. Pin the device fingerprint
  (`HelixSession(..., device_fingerprint=...)`, see README "Security")
  to lock a deployment to a known device. Reports assuming an attacker
  who was already on-path during first contact of an unpinned session
  are considered known limitations rather than vulnerabilities.
