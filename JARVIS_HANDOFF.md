# Jarvis Handoff

Status: planning only. Nothing has been built, flashed or tested yet. No code exists for this project in this repo.

## Goal
A Jarvis-style always-on voice assistant on an old phone. The phone is a thin client (wake word, mic, speaker, optional HUD screen). The brain runs on the user's Mac mini.

## Device and setup
- Phone: Google Pixel 3 (codename `blueline`), 64 GB, Snapdragon 845, 4 GB RAM.
- OS plan: **postmarketOS** (real Linux, no Android, no root, no Termux). This replaced the original "rooted Android" idea because the user does not want any Android OS features.
- Fallbacks if postmarketOS audio is broken: Droidian (Halium) or Ubuntu Touch. Support status for blueline was recalled from memory and NOT verified (the wiki hosts were blocked in the cloud session). Check wiki.postmarketos.org and the Droidian device list first.
- Mac mini: runs LM Studio with **Gemma** loaded. LM Studio serves an OpenAI-compatible API at `http://<mac>:1234/v1` (Developer tab, "Serve on local network").
- "Hermes": the user's name for the assistant/agent. Never clarified whether it is the Nous Hermes Agent framework, a model, or just a name. The user considered running it in Docker. Open question below.

## Architecture
```
Pixel 3 (postmarketOS): wake word, mic, speaker, optional kiosk HUD
   <-> WebSocket over LAN or Tailscale
Mac mini: STT (whisper.cpp / faster-whisper) -> LM Studio (Gemma) -> TTS (Kokoro / Piper)
```
- Options discussed for Hermes: (a) on the Mac next to the model (recommended: simpler, faster); (b) agent framework on the phone, calling Gemma on the Mac over the network (possible; the model itself will not run usefully on the phone: 4 GB RAM, no GPU acceleration).
- Docker on macOS has no Metal GPU access, so keep model inference, STT and TTS native on macOS; only the agent framework would go in Docker (reach the host via `host.docker.internal`).

## Key decisions
- Phone stays thin; heavy compute on the Mac.
- No Android features and no root: postmarketOS instead.
- Wake word on the device (openWakeWord or Porcupine) so audio only leaves the phone after "Jarvis".
- Tailscale on both devices for remote access. LM Studio must never be exposed to the open internet (no auth).

## Flashing plan (verify every command against the postmarketOS wiki)
1. `brew install --cask android-platform-tools` on the Mac.
2. Back up the phone. Enable developer options and OEM unlocking, then `adb reboot bootloader` and `fastboot flashing unlock` (wipes the phone).
3. `pmbootstrap` does not run natively on macOS: use a Linux VM or container (OrbStack/UTM), `pipx install pmbootstrap`.
4. `pmbootstrap init`: vendor `google`, device `blueline`, minimal UI, SSH key.
5. `pmbootstrap install` and `pmbootstrap export`, copy images to the Mac, flash with `fastboot` (exact partitions from the device wiki).
6. Boot, SSH in, test audio with `arecord` / `aplay`, test Wi-Fi.

## Open problems and next steps
1. **Check blueline support** on the postmarketOS and Droidian wikis, especially audio (speaker and mic), Wi-Fi, USB, charging.
2. Flash and verify mic and speaker before writing any assistant code. This is the biggest risk.
3. Clarify what "Hermes" is, and where it should run (Mac vs phone, Docker or not).
4. Test the link: from the phone, `curl http://<mac>:1234/v1/models` should list Gemma. Keep the Mac awake (`caffeinate`).
5. Build the MVP: phone hears "Jarvis", the Mac replies out loud. Stream everything (partial STT, token streaming, sentence-level TTS) for low latency.
6. Later: kiosk HUD (Cage + a web page served from the Mac), tool calling for calendar, smart home and Mac control.
7. Battery: running a phone plugged in 24/7 can swell the cell; remove the battery or cap charging if possible.

## Environment notes
- The cloud session could not attach the user's Mac folder: the "TagBot" computer listed three folders (LGTV Project, ClaudeRemote, AdhvikSchool) but did not offer them to cloud sessions. Alternative: `claude --teleport` locally.

## Secrets
None collected. If an API key is ever needed, keep it in an environment variable or a local `.env` that is gitignored.
