# Record and run your own voice

This helper records a real microphone, converts it to the exact input required by
the live pipeline, and runs the same `voice.cli.live_run.run_single` path used by
the Azure-generated fixture. It never substitutes a fake transcript.

The required audio format is mono, 16 kHz, 16-bit signed PCM in a WAV container.
The helper asks `ffmpeg` to produce that format and validates the WAV before it
contacts Azure ASR.

## Run it

1. Work from the repository root. Make sure `ffmpeg` is installed, connect a
   microphone, and grant the terminal microphone permission.
2. In terminal 1, load the live credentials and start the existing turn server:

   ```bash
   set -a; source .env; set +a
   .venv/bin/uvicorn api:app --port 8000
   ```

3. In terminal 2, load the same environment and start a ten-second recording:

   ```bash
   set -a; source .env; set +a
   .venv/bin/python -m voice.cli.record_and_run --record --seconds 10 --out /tmp/credins-human
   ```

4. When `Speak now...` appears, say exactly:

   > Sa është komisioni për shlyerje të parakohshme të kredisë në Banka Credins?

The command prints the recording path, Azure's `ASR transcript`, the structured
`outcome`, and the real `answer.wav` path. Compare `ASR transcript` verbatim with
the sentence above. A material wording difference or a `clarify`/`handoff`
outcome means human speech recognition or confidence handling changed relative
to the Azure TTS fixture; an `answer` means the utterance passed the same guarded
path. Inspect `run.json` in the output directory for confidence, citations, and
stage timings.

Automatic input selection uses ALSA `default` on Linux, AVFoundation audio device
0 on macOS, and DirectShow `audio=default` on Windows. If a working microphone is
not the platform default, pass both its ffmpeg input format and device, for
example `--input-format pulse --input-device default` on a Linux PulseAudio
system. With no input device, the helper exits non-zero with `no audio input
device found`; it does not continue with synthetic data.
