# Morning Briefing App

A comprehensive Python-based desktop application for fetching, analyzing, and presenting a personalized daily briefing. Built with `customtkinter`, this app integrates news fetching, AI-driven sentiment analysis, stock market tracking, text-to-speech (TTS), and more into a sleek, dark-themed dashboard.

## Features
- **Dashboard Interface**: Built with modern UI elements using CustomTkinter.
- **AI Analysis**: Uses local LLMs (via Ollama) to analyze news and extract insights, market moods, and sentiment scores.
- **Portfolio & Market Tracking**: Live updates of market indices and personalized stock portfolio monitoring.
- **Text-to-Speech (TTS)**: Built-in TTS engine to read the briefing aloud.
- **Notifications & Weekly Digest**: Supports automated email sending and PDF exports for daily digests.

## Setup
1. Ensure Python 3.10+ is installed.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Make sure you have Ollama running locally if using the AI analysis features.

## Usage
Run the main application:
```bash
./launch.sh
# or
python main.py
```
