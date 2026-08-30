# Changelog

## 0.3.0 - 2026-08-30

First public release.

- python-can plugin interface `helix` for Helix CAN backends
  (`can.Bus(interface='helix', ...)`) over WebSocket
- Encrypted communication: ECDH P-256 key exchange, AES-256-GCM (HTTP)
  and ChaCha20-Poly1305 (WebSocket)
- ChaCha20-Poly1305 decryption of streamed HTTP responses
  (`CryptoSession.decrypt_http_chacha`, responses marked `X-Cipher: chacha20`)
- `HelixSession`: pre-authenticated sessions that keep credentials out
  of the python-can bus configuration (and its DEBUG logs)
- Device fingerprint pinning (`device_fingerprint=`), trust-on-first-use
  by default; fingerprint computed locally from the device public key
- Session-token authentication with single-use WebSocket tickets
- CAN FD support with correct DLC mapping
- Shared WebSocket connection across multiple buses to the same device
- REST API client: bitrate/timing configuration, bus on/off, channel state
- Robust reconnect behavior after failed initial connections
- Rate-limited receive-overflow warnings
- Opt-in device-side CAN frame batching (`can.Bus(..., batch=True)`) for
  high-rate buses: one decrypt per batch instead of per frame
