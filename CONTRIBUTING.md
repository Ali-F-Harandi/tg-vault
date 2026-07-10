# Contributing to tg-vault

Thanks for your interest in improving tg-vault! 🎉

## Development Setup

```bash
# Clone
git clone https://github.com/kesafatkari/tg-vault.git
cd tg-vault

# Create a virtual environment
python -m venv venv
source venv/bin/activate   # Linux/Mac
# or: venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt
pip install -e .   # (if you add setup.py later)

# Run tests
python tg.py test
```

## Code Style

- **Python 3.8+** compatible
- Follow [PEP 8](https://peps.python.org/pep-0008/) (line length: 100 chars)
- Use type hints where it improves readability
- Docstrings for all public functions/classes (triple-quote, English preferred)
- Comments in code: English for general audience, Persian OK for project-specific quirks

## Project Structure

```
tg-vault/
├── tg.py                  # Main script (single-file design — keep it that way)
├── README.md              # English documentation
├── README.fa.md           # Persian documentation
├── CHANGELOG.md           # Version history
├── LICENSE                # MIT
├── requirements.txt       # Python dependencies
├── .gitignore             # Ignores config files, resume state, etc.
├── docs/
│   ├── TELEGRAM_LIMITS.md # Reference for Telegram Bot API limits
│   └── ARCHITECTURE.md    # Architecture overview & design decisions
└── examples/
    ├── parallel_uploads.py
    ├── backup_directory.py
    └── download_all.py
```

## Design Principles

1. **Single-file main script** — `tg.py` should remain a single file. If it grows too large, refactor into a package, but keep the CLI entrypoint simple.

2. **Bot-token-only** — Never require `api_id`/`api_hash` or MTProto for the main flow. Local Bot API Server support can be added as an optional feature.

3. **No external dependencies beyond `requests`** — keep the dependency surface minimal.

4. **Concurrency-safe by default** — every operation should be safe to run in parallel with other instances.

5. **Graceful failure** — always clean up temp messages on error/interrupt; always save resume state.

6. **Bilingual** — keep both `README.md` (English) and `README.fa.md` (Persian) in sync. If you update one, update the other.

## Pull Request Process

1. **Fork** the repository
2. **Create a feature branch**: `git checkout -b feature/my-feature`
3. **Make your changes** — keep commits focused and well-described
4. **Test manually** with at least one bot and one channel
5. **Update documentation** — both READMEs if applicable, CHANGELOG.md
6. **Submit PR** with a clear description of what changed and why

### Commit Message Format

```
type(scope): short description

Optional longer description.
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`

Examples:
- `feat(uploader): add AES-256-GCM encryption option`
- `fix(downloader): handle case when temp channel is full`
- `docs(readme): add Local Bot API Server section`

## Ideas for Contributions

### High-impact
- 🔐 **AES-256-GCM client-side encryption** — encrypt chunks before upload, decrypt after download. Manifest should store the IV but not the key (user provides key).
- 🌐 **Local Bot API Server support** — add `api_url` field per bot in config, so users can self-host and get 2 GB uploads + unlimited downloads.
- 🐳 **Docker image + REST API** — wrap tg-vault in a Flask/FastAPI service, expose `/upload` and `/download?url=...` endpoints.

### Medium-impact
- 🎬 **HTTP Range streaming** — for video files, allow partial download with Range requests (useful for media servers).
- 🖥️ **TUI** — replace the interactive menu with a Rich/Textual-based TUI with live progress bars.
- 📊 **Stats dashboard** — show upload/download history, bot usage stats, total bytes transferred.
- 🔄 **`tg.py sync`** — sync a local directory with the channel (like rclone).

### Low-impact / nice-to-have
- 🌍 **Translation** — add more languages (Arabic, Russian, French, etc.)
- 📦 **Package on PyPI** — `pip install tg-vault`
- 🧪 **Unit tests** — mock the Telegram API and test edge cases
- 📝 **`tg.py config edit`** — open the config file in `$EDITOR`

## Reporting Bugs

When reporting a bug, please include:

1. **tg-vault version**: `python tg.py --version`
2. **Python version**: `python --version`
3. **OS**: Linux/Mac/Windows + version
4. **Number of bots**: 1, 2, 5, etc.
5. **File size** you were trying to upload/download
6. **Full command** you ran
7. **Full output** (or at least the error message)
8. **Expected behavior** vs **actual behavior**

Do NOT include your bot tokens or channel IDs in bug reports!

## Code of Conduct

Be respectful. Be helpful. Be patient. We're all volunteers here.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
