# Maanav AI

Maanav AI is an experimental Windows desktop assistant with voice and text chat, a floating companion, configurable AI providers, and optional local voice, vision, hand tracking, and desktop routines.

## Highlights

- Routes text chat across configured cloud providers and a local Ollama fallback.
- Supports saved conversations and explicit local memory.
- Includes local speech input and output, with optional cloud speech features.
- Can analyze images or a screen capture when the user requests it.
- Offers optional camera hand tracking processed locally.
- Includes configurable routines for common desktop workflows.

Some features require separate provider credentials or optional packages. See the requirements files and in-app messages for optional setup details. No API credentials, personal chat history, saved memory, local model data, or machine-specific account configuration belong in this repository.

## Requirements

- Windows 10 or later
- Python 3.11+
- Optional: Ollama and a locally installed model for local AI chat

## Setup

1. Create and activate a virtual environment.
2. Install `requirements.txt`.
3. Set provider API keys as environment variables if you want to use cloud AI providers. Never commit keys or `.env` files.
4. Run `python app.py`.

Optional voice and hand-tracking features need the packages listed in `requirements-voice.txt` and `requirements-vision.txt`. Hand tracking also downloads Google's local MediaPipe task model with `python tools/download_hand_model.py`.

## Privacy notes

Screen capture, image analysis, camera hand tracking, local memory, and cloud model use have different data flows. Screen capture and computer-use features are user-triggered. Review the source and provider configuration before enabling a cloud service, since prompts and selected images may be sent to that provider.

Maanav AI is a personal project in active development. Provider APIs, optional packages, and desktop integrations can change; features may require configuration and have not been packaged as a production installer.

To launch the standalone hand-tracking preview, run python hand_tracking_studio.pyw after installing the optional vision requirements and downloading the local model.
